"""Sign-up: a new company creates its workspace (how: services/signup.py).

    GET  /api/auth/signup/google/start      Google sends the founder back to its usual
                                            callback (routers/sso.py)
    GET  /api/auth/signup/microsoft/start   Microsoft 365: back to /api/auth/m365/callback
    POST /api/auth/signup/email             {name, email, code}: email a confirmation link
    POST /api/auth/signup/email/confirm     {token}: the link was opened -> the DNS record
    POST /api/auth/signup/email/complete    {token, password}: the record checks out ->
                                            the workspace, and a session {token, user}

Microsoft 365 also signs people in to a workspace made with it (any company's
tenant, kb/services/m365.py):
    GET  /api/auth/m365/start, /api/auth/m365/callback
"""

import logging
import sqlite3
import time

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import audit_store
import members_store
import sso
import sso_errors
from kb.audit import audited
from kb.deps import public_url
from kb.rate_limit import rate_limit
from kb.services import m365
from kb.services import signup as service
from kb.services.sign_in import ProviderFailed, code_for, login_error, mark_browser
from kb.services.signup import SIGNUP_KEY, SIGNUP_SECONDS, signup_codes, signup_methods

router = APIRouter()
log = logging.getLogger("uvicorn.error")
EMAIL_HINT = service.EMAIL_RE


def _check_start(request, method, name, code):
    """The founder's name and invite code, before going to the provider: None if
    they're fine, else where to send the browser."""
    rate_limit(request, "signup")
    if method not in signup_methods():
        return login_error("signup_off")
    if not 1 <= len(name) <= 60:
        return RedirectResponse("/#signup?error=name")
    codes = signup_codes()
    if codes and (code not in codes or members_store.signup_code_used(code)):
        return RedirectResponse("/#signup?error=invite")
    mark_browser(request)
    request.session[SIGNUP_KEY] = {"name": name, "code": code if codes else None,
                                   "method": method, "at": time.time()}
    return None


@router.get("/api/auth/signup/google/start")
async def signup_start(request: Request, name: str = "", code: str = ""):
    name, code = " ".join(name.split()), code.strip()
    refused = _check_start(request, "google", name, code)
    if refused:
        return refused
    c = sso.client("google")
    try:
        return await c.authorize_redirect(
            request, public_url(request, "/api/auth/sso/google/callback"),
            **sso.authorize_params("google"))
    except Exception:              # couldn't reach Google's discovery document
        log.exception("Sign-up: couldn't start the Google sign-in")
        return login_error("sso_failed", tab="signup")


@router.get("/api/auth/signup/microsoft/start")
async def signup_m365_start(request: Request, name: str = "", code: str = ""):
    name, code = " ".join(name.split()), code.strip()
    refused = _check_start(request, m365.PROVIDER, name, code)
    if refused:
        return refused
    return await _m365_redirect(request, tab="signup", prompt="select_account")


async def _m365_redirect(request, tab="login", **params):
    try:
        return await m365.client().authorize_redirect(
            request, public_url(request, "/api/auth/m365/callback"), **params)
    except Exception:
        log.exception("Microsoft 365: couldn't start the sign-in")
        return login_error("sso_failed", tab=tab)


@router.get("/api/auth/m365/start")
async def m365_start(request: Request, login_hint: str = ""):
    """Sign in to a workspace with Microsoft 365 (not a sign-up)."""
    rate_limit(request, "sso_start")
    if not m365.configured():
        return login_error("sso_unavailable")
    request.session.pop(SIGNUP_KEY, None)
    mark_browser(request)
    hint = login_hint.strip().lower()
    return await _m365_redirect(request, prompt="select_account",
                                **({"login_hint": hint} if EMAIL_HINT.match(hint) else {}))


@router.get("/api/auth/m365/callback")
async def m365_callback(request: Request):
    c = m365.client()
    if not c:
        return login_error("sso_unavailable")
    signup = request.session.pop(SIGNUP_KEY, None)
    if (signup and signup.get("method") == m365.PROVIDER
            and time.time() - signup.get("at", 0) < SIGNUP_SECONDS):
        return await service.finish_m365_signup(c, request, signup["name"], signup.get("code"))
    try:
        claims = await service.m365_claims(c, request)
    except ProviderFailed as e:
        ref = sso_errors.new_ref()
        log.warning("Microsoft 365: sign-in failed (%s), reference %s", e.code, ref)
        return login_error(e.error, ref)
    try:
        user = sso.user_for(m365.PROVIDER, claims)
    except sso.NotAllowed as e:
        return login_error(e.reason)
    except (OSError, sqlite3.Error):
        ref = sso_errors.new_ref()
        log.exception("Microsoft 365: couldn't decide who gets in, reference %s", ref)
        return login_error("sso_failed", ref)
    members_store.log_event(user["workspace"], m365.PROVIDER, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + code_for(request, user))


# ---- Email and DNS -----------------------------------------------------------------------
class EmailSignupIn(BaseModel):
    name: str = Field(max_length=200)
    email: str = Field(max_length=320)
    code: str = Field(default="", max_length=200)


class LinkIn(BaseModel):
    token: str = Field(max_length=200)


class CompleteIn(LinkIn):
    password: str = Field(max_length=200)


def _email_on():
    if "email" not in signup_methods():
        raise service.SignupRefused(403, "signup_off", "New workspaces can't be created this "
                                                       "way on this server.")


def _platform(audit, action, email, details):
    """A sign-up still pending has no workspace: the operators' chain has it."""
    audit.record("signup", email, details, workspace=audit_store.PLATFORM, actor=email,
                 action=action, actor_type="user")


@router.post("/api/auth/signup/email")
def signup_email(body: EmailSignupIn, request: Request, audit=audited("signup.email_start")):
    rate_limit(request, "signup")
    _email_on()
    name = " ".join(body.name.split())
    if not 1 <= len(name) <= 60:
        raise service.SignupRefused(400, "name", "Give your workspace a name.")
    row = service.start_email_signup(name, body.email, body.code.strip(),
                                     public_url(request, "").rstrip("/"))
    _platform(audit, None, row["email"], {"name": name, "domain": row["domain"]})
    return {"ok": True, "email": row["email"], "expires_at": row["expires_at"]}


@router.post("/api/auth/signup/email/confirm")
def signup_email_confirm(body: LinkIn, request: Request,
                         audit=audited("signup.email_confirm")):
    rate_limit(request, "signup")
    _email_on()
    view = service.confirm_email(body.token)
    _platform(audit, None, view["email"], {"domain": view["domain"]})
    return view


@router.post("/api/auth/signup/email/complete")
def signup_email_complete(body: CompleteIn, request: Request,
                          audit=audited("signup.email_complete")):
    rate_limit(request, "dns_check")
    _email_on()
    out = service.complete_email_signup(request, body.token, body.password)
    user = out["user"]
    _platform(audit, None, user["username"], {"workspace": user["workspace"]})
    from kb.routers.auth import _shown        # as signing in shows it (the trial too)
    return {"token": out["token"], "user": _shown(user)}
