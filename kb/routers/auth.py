"""Password sign-in, sessions (the one-time SSO code -> a token), sign-out."""

import asyncio
import logging
import secrets
import sqlite3
import time
import urllib.parse

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import auth
import members_store
import sessions_store
import sso
from kb import state
from kb.audit import audited
from kb.deps import current_user, public_url, public_user
from kb.rate_limit import rate_limit
from kb.services.sign_in import BROWSER_KEY
from kb.services.signup import signup_codes, signup_on
from kb.settings import SESSION_SECONDS

router = APIRouter()
log = logging.getLogger("uvicorn.error")

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60


class LoginIn(BaseModel):
    username: str
    password: str


class CodeIn(BaseModel):
    code: str


@router.get("/api/auth/providers")
def auth_providers():
    try:
        email_sso = members_store.any_connections()
    except sqlite3.Error:
        log.exception("Couldn't read the sign-in connections")
        email_sso = False
    return {"providers": [{"id": p, "name": sso.label(p)} for p in sso.configured()],
            "password": auth.password_login_on(), "email_sso": email_sso,
            "signup": signup_on(), "signup_invite": bool(signup_codes())}


@router.post("/api/auth/login")
async def login(body: LoginIn, request: Request):
    rate_limit(request, "login")
    if not auth.password_login_on():
        raise HTTPException(403, "Password sign-in is turned off. Use single sign-on.")
    failed_logins = state.failed_logins
    key = body.username.strip().lower()
    count, locked_until = failed_logins.get(key, (0, 0))
    if time.time() < locked_until:
        raise HTTPException(429, f"Too many failed attempts. Try again in "
                                 f"{int(locked_until - time.time())} seconds.")
    if not members_store.has_password_accounts():
        raise HTTPException(503, "No user accounts exist yet. Create one with: "
                                 "python auth.py add <username>")
    # Hashing is slow on purpose: off the event loop.
    member = await run_in_threadpool(auth.check_password, body.username, body.password)
    if member and not auth.sso_required(member):
        failed_logins.pop(key, None)
        user = {"username": member["username"], "name": member["name"],
                **auth.access_for(member["username"])}
        token = secrets.token_urlsafe(32)
        sessions_store.create(token, user, SESSION_SECONDS)   # survives restarts / deploys
        members_store.log_event(user["workspace"], "password", user["username"], "signed_in")
        return {"token": token, "user": user}
    if member:                                 # right password, but SSO is required
        members_store.log_event(member["workspace"], "password", member["username"],
                                "refused", "sso_required")
    # A wrong password and a right one that may not be used get the same answer
    # (and the same wait), so this can't be used to check passwords.
    await asyncio.sleep(1)                     # slow down password guessing
    count += 1
    if len(failed_logins) > 10_000:
        for k in [k for k, (_, until) in failed_logins.items() if until < time.time()]:
            del failed_logins[k]
    failed_logins[key] = ((0, time.time() + LOCKOUT_SECONDS)
                          if count >= MAX_FAILED_LOGINS else (count, 0))
    if _sso_domain(key):
        raise HTTPException(403, "Your workspace uses single sign-on. Sign in with your work "
                                 "account above.")
    raise HTTPException(401, "Wrong username or password.")


def _sso_domain(username):
    """Is this an email on a domain whose workspace requires single sign-on?
    (Decided from the domain alone, which the email lookup already reveals.)"""
    if "@" not in username:
        return False
    workspace = members_store.workspace_for_domain(username.rpartition("@")[2])
    return bool(workspace and members_store.requires_sso(workspace))


@router.post("/api/auth/sso/exchange")
def sso_exchange(body: CodeIn, request: Request):
    user = sso.redeem_code(body.code, request.session.get(BROWSER_KEY))
    if not user:
        raise HTTPException(401, "That sign-in expired. Please sign in again.")
    token = secrets.token_urlsafe(32)
    sessions_store.create(token, user, SESSION_SECONDS)
    return {"token": token, "user": public_user(user)}


@router.post("/api/auth/logout")
def logout(request: Request, authorization: str = Header(default="")):
    """Ends the session. If it came through a workspace provider set to "sign out
    there too", logout_url is where the browser goes next to end that session."""
    token = authorization.removeprefix("Bearer ").strip()
    user = sessions_store.get(token) or {}
    sessions_store.delete(token)
    return {"ok": True, "logout_url": _provider_logout_url(request, user)}


def _provider_logout_url(request, user):
    """OpenID Connect RP-initiated logout, when the provider supports it and the
    admin turned it on. (SAML single logout isn't supported.)"""
    try:
        conn = members_store.get_connection(user.get("connection"))
    except sqlite3.Error:
        log.exception("Couldn't read sign-in connection %s to sign out there",
                      user.get("connection"))
        return None
    end = conn and conn["protocol"] == "oidc" and conn["logout_at_idp"] and \
        conn["metadata"].get("end_session_endpoint")
    if not end:
        return None
    params = {"client_id": conn["client_id"],
              "post_logout_redirect_uri": public_url(request, "/")}
    if user.get("id_token"):
        params["id_token_hint"] = user["id_token"]
    return end + ("&" if "?" in end else "?") + urllib.parse.urlencode(params)


@router.post("/api/auth/logout-everywhere")
def logout_everywhere(user=Depends(current_user), audit=audited("session.sign_out_everywhere")):
    """Ends every session this person has, on every device, this one included."""
    ended = sessions_store.delete_user(user["username"])
    audit.record("member", user["username"], {"sessions_ended": ended}, user=user)
    return {"ok": True, "ended": ended}


@router.get("/api/auth/me")
def me(user=Depends(current_user)):
    return public_user(user)
