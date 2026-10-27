"""Single sign-on over OpenID Connect (sso.py): the server-wide providers (Google,
Microsoft), and a workspace's own providers, found from the email someone types
on the login page. SAML is routers/saml.py; the shared steps services/sign_in.py."""

import logging
import re
import secrets
import sqlite3
import time
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import auth
import members_store
import sso
import sso_errors
from kb.deps import public_url
from kb.rate_limit import rate_limit
from kb.services.sign_in import (SSO_TESTS_SESSION_KEY, TEST_STATE_PREFIX, ProviderFailed,
                                 code_for, connection_or_none, finish_sso_test, log_failure,
                                 login_error, mark_browser, provider_answer, sso_test,
                                 test_failure, usable_connection)
from kb.services import m365
from kb.services.signup import SIGNUP_KEY, SIGNUP_SECONDS, finish_signup, signup_on

router = APIRouter()
log = logging.getLogger("uvicorn.error")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[a-z0-9.-]{1,253}\.[a-z]{2,63}$")


@router.get("/api/auth/sso/{provider}/start")
async def sso_start(provider: str, request: Request):
    rate_limit(request, "sso_start")
    c = sso.client(provider)
    if not c:
        return RedirectResponse("/#login?error=sso_unavailable")
    request.session.pop(SIGNUP_KEY, None)
    mark_browser(request)
    try:
        return await c.authorize_redirect(
            request, public_url(request, f"/api/auth/sso/{provider}/callback"),
            **sso.authorize_params(provider))
    except Exception:              # couldn't reach the provider's discovery document
        log.exception("SSO %s: couldn't start sign-in", provider)
        return RedirectResponse("/#login?error=sso_failed")


@router.get("/api/auth/sso/{provider}/callback")
async def sso_callback(provider: str, request: Request):
    c = sso.client(provider)
    if not c:
        return RedirectResponse("/#login?error=sso_unavailable")
    signup = request.session.pop(SIGNUP_KEY, None)
    if (provider == "google" and signup and signup.get("method", "google") == "google"
            and time.time() - signup.get("at", 0) < SIGNUP_SECONDS):
        return await finish_signup(c, request, signup["name"], signup.get("code"))
    return await _finish_sso(provider, c, request, lambda claims: sso.user_for(provider, claims))


async def _finish_sso(name, c, request, user_for):
    """Check the provider's answer, then hand the browser a one-time code for the
    session. No workspace's admin owns a server-wide provider, so its failures
    are only in the server log (with the reference)."""
    try:
        claims, _ = await provider_answer(name, c, request)
    except ProviderFailed as e:
        ref = sso_errors.new_ref()
        log.warning("SSO %s: sign-in failed (%s), reference %s", name, e.code, ref)
        return login_error(e.error, ref)
    try:
        user = user_for(claims)
    except sso.NotAllowed as e:
        return login_error(e.reason, _log_refusal(name, claims, e.reason))
    except Exception:              # e.g. the database can't be read
        ref = sso_errors.new_ref()
        log.exception("SSO %s: couldn't decide who gets in, reference %s", name, ref)
        return login_error("sso_failed", ref)
    members_store.log_event(user["workspace"], name, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + code_for(request, user))


def _log_refusal(method, claims, reason):
    """Show a refusal by a server-wide provider to the workspace whose domain it
    was. Returns the reference logged with it, or None."""
    email = claims.get("email") or claims.get("preferred_username")
    if not isinstance(email, str) or "@" not in email:
        return None
    try:
        workspace = members_store.workspace_for_domain(email.rpartition("@")[2])
        if workspace:
            ref = sso_errors.new_ref()
            members_store.log_event(workspace, method, email.strip().lower(), "refused", reason,
                                    ref)
            return ref
    except sqlite3.Error:
        log.exception("Couldn't log a sign-in refusal")
    return None


# ---- A workspace's own provider, found from the email typed on the login page --------
class EmailIn(BaseModel):
    email: str = Field(max_length=320)


def _start_url(conn, email):
    if conn["protocol"] == "saml":
        return f"/api/auth/saml/{conn['id']}/start"
    return f"/api/auth/sso/c/{conn['id']}/start?" + urllib.parse.urlencode({"login_hint": email})


@router.post("/api/auth/sso/discover")
def sso_discover(body: EmailIn, request: Request):
    rate_limit(request, "discover")
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter your work email address.")
    try:
        conns = sso.connections_for_email(email)
    except sqlite3.Error:
        log.exception("Couldn't look up sign-in for an email")
        raise HTTPException(503, "Sign-in settings can't be read. Please try again.")
    return {"connections": [{"id": c["id"], "name": c["label"], "url": _start_url(c, email),
                             "kind": _provider_kind(c)} for c in conns],
            **_route_for(email)}


# Whose logo the sign-in page shows for a workspace's provider, from where it lives.
_KINDS = (("accounts.google.com", "google"), ("login.microsoftonline.com", "microsoft"),
          ("sts.windows.net", "microsoft"), ("okta.com", "okta"), ("oktapreview.com", "okta"),
          ("auth0.com", "auth0"), ("/realms/", "keycloak"), ("authentik", "authentik"))


def _provider_kind(conn):
    """"google", "microsoft", "okta", ... from the provider's issuer or entity ID,
    or None when it's not one we know (the page then shows no logo)."""
    where = (conn.get("issuer") or "").lower()
    return next((kind for needle, kind in _KINDS if needle in where), None)


def _route_for(email):
    """For the sign-in page's route pass: whether this email's domain has a
    workspace, how it signs people in, and where roles come from there. Never
    the workspace's name: anyone can ask about any domain (rate-limited), and a
    domain that answers only says what Continue's redirect would show anyway."""
    workspace = members_store.workspace_for_domain(email.rpartition("@")[2])
    if not workspace:
        return {"workspace": None, "signup": signup_on()}
    source = members_store.role_source(workspace)
    conn = members_store.get_connection(source) if source else None
    return {"workspace": {"google": "google" in sso.configured(),
                          "password": auth.password_login_on()
                          and not members_store.requires_sso(workspace),
                          "roles_from": conn["label"] if conn and conn["enabled"] else None,
                          # any company's Microsoft 365 (kb/services/m365.py), when set up
                          **({"microsoft365": True} if m365.configured() else {})}}


@router.get("/api/auth/sso/c/{cid}/start")
async def sso_connection_start(cid: str, request: Request, login_hint: str = "",
                               test: str = ""):
    rate_limit(request, "sso_start")
    trial = sso_test(test, cid)
    conn = connection_or_none(cid) if trial else usable_connection(cid, "oidc")
    if not conn or conn["protocol"] != "oidc":
        return RedirectResponse("/#login?error=sso_unavailable")
    mark_browser(request)
    extra = {"login_hint": login_hint} if EMAIL_RE.match(login_hint) else {}
    if trial:
        extra["prompt"] = "login"          # show the admin the provider's sign-in page
        extra["state"] = TEST_STATE_PREFIX + secrets.token_urlsafe(24)
        tests = dict(list(request.session.get(SSO_TESTS_SESSION_KEY, {}).items())[-4:])
        request.session[SSO_TESTS_SESSION_KEY] = {**tests, extra["state"]: test}
    try:
        return await sso.connection_client(conn).authorize_redirect(
            request, public_url(request, f"/api/auth/sso/c/{cid}/callback"), **extra)
    except Exception as e:
        log.exception("SSO connection %s: couldn't start sign-in", cid)
        return login_error("sso_failed", log_failure(conn, sso_errors.from_oidc_exception(e)))


@router.get("/api/auth/sso/c/{cid}/callback")
async def sso_connection_callback(cid: str, request: Request):
    state = request.query_params.get("state", "")
    trial = None
    if state.startswith(TEST_STATE_PREFIX):
        tests = request.session.get(SSO_TESTS_SESSION_KEY, {})
        trial = sso_test(tests.get(state), cid)
        request.session[SSO_TESTS_SESSION_KEY] = {k: v for k, v in tests.items() if k != state}
        if not trial:                      # expired or unknown: a test, never a sign-in
            return RedirectResponse("/#sso-test", status_code=303)
    conn = connection_or_none(cid) if trial else usable_connection(cid, "oidc")
    if not conn or conn["protocol"] != "oidc":
        return RedirectResponse("/#login?error=sso_unavailable")
    # Did this browser start a sign-in here? (Authlib keeps its state in the cookie.)
    started_here = any(k.startswith(f"_state_conn_{cid}_") for k in request.session)
    try:
        try:
            client = sso.connection_client(conn)
        except Exception as e:     # e.g. its secret can't be decrypted any more
            log.exception("SSO connection %s: can't use its settings", cid)
            raise ProviderFailed("sso_failed", "settings_unreadable",
                                 f"{type(e).__name__}: {e}") from None
        claims, id_token = await provider_answer(f"connection {cid}", client, request)
    except ProviderFailed as e:
        if trial:
            return finish_sso_test(trial, conn, test_failure(e.code, e.detail))
        # Only for a browser that started a sign-in here: anyone can open this
        # link with an error in it. The code only, never the provider's words.
        return login_error(e.error, log_failure(conn, e.code) if started_here else None)
    if trial:
        try:
            result = sso.try_connection(conn, claims)
        except Exception as e:
            log.exception("SSO connection %s: test failed", cid)
            result = test_failure("sso_failed", f"{type(e).__name__}: {e}")
        return finish_sso_test(trial, conn, result, claims)
    try:
        user = sso.user_for_connection(conn, claims)
    except sso.NotAllowed as e:
        ref = sso_errors.new_ref()
        members_store.log_event(conn["workspace"], cid, claims.get("email"), "refused", e.reason,
                                ref)
        return login_error(e.reason, ref)
    except Exception:
        log.exception("SSO connection %s: couldn't decide who gets in", cid)
        return login_error("sso_failed", log_failure(conn, "sso_failed"))
    if conn["metadata"].get("end_session_endpoint") and id_token:
        user["id_token"] = id_token           # for signing out there (POST /auth/logout)
    members_store.log_event(conn["workspace"], cid, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + code_for(request, user))
