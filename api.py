"""
api.py  —  the JSON API behind the React frontend (frontend/).

Same brain as the Streamlit app: the agent (agent.py), login (auth.py) and the
docs folder (docs_store.py). Only the screen is different.

MULTI-TENANT: every user belongs to a workspace (company). Documents, the search
index, integrations and index status are all per workspace (workspaces.py), so
one company never sees another company's data.

Run it (from the project folder):
    venv\\Scripts\\uvicorn api:app --reload --port 8010
then, in frontend/:   npm run dev     -> http://localhost:5173

Endpoints (all under /api, all except login need "Authorization: Bearer <token>"):
    GET    /auth/providers        which sign-in options the login page shows (email_sso:
                                  ask for an email first, as some workspace has its own provider)
    POST   /auth/login            {username, password} -> {token, user}
    GET    /auth/sso/{provider}/start      send the browser to Google / Microsoft
    GET    /auth/sso/{provider}/callback   they send it back here (sso.py decides who gets in)
    POST   /auth/sso/discover     {email} -> {connections} the workspace's own sign-in providers
    GET    /auth/sso/c/{id}/start | /callback   the same, through one of those providers
    GET    /auth/saml/{id}/metadata | /start  POST /acs  GET /done   ... when it's SAML
    POST   /auth/sso/exchange     {code} -> {token, user}   the one-time code from the callback
    POST   /auth/logout           -> {logout_url}: also sign out at the provider, if it's set to
    POST   /auth/logout-everywhere  end all of this person's sessions, on every device
    GET    /auth/me
    POST   /chat                  {question, role} -> {answer, sources, steps}
    POST   /chat/stream           same, streamed live (steps + answer tokens) as SSE
    GET    /docs                  list documents                       (HR / Admin)
    GET    /docs/{access}/{name}  text of a .txt, or the file itself    (HR / Admin)
    POST   /docs                  {name, access, content} new .txt      (HR / Admin)
    PUT    /docs/{access}/{name}  {content} save a .txt                 (HR / Admin)
    DELETE /docs/{access}/{name}                                        (HR / Admin)
    POST   /docs/upload           multipart: files[], access            (HR / Admin)
    POST   /reindex               rebuild the search index              (HR / Admin)
    GET    /index/status          background re-index progress
    GET    /knowledge             everything the assistant can search (uploads + synced files)
    GET    /integrations          what this workspace has connected
    POST   /integrations/{provider}/connect     -> {url} of the consent screen  (HR / Admin)
    GET    /integrations/{provider}/callback    the provider redirects back here
    GET    /integrations/google_drive/folders   browse folders to pick         (HR / Admin)
    PUT    /integrations/google_drive/folders   {folders} choose what to sync  (HR / Admin)
    PUT    /integrations/{provider}/access      {access: restricted|everyone}
                                  who may ask about its files, then re-index  (HR / Admin)
    POST   /integrations/{provider}/sync        sync now                      (HR / Admin)
    DELETE /integrations/{provider}             disconnect + delete copies    (HR / Admin)
  ({provider} = google_drive | notion)
    GET    /admin/sign-in         this workspace's domains and sign-in providers  (admin)
    PUT    /admin/sign-in/policy  {require_sso}: passwords only for admins           (admin)
    POST   /admin/sign-in/domains {domain} -> the DNS record that proves it      (admin)
    POST   /admin/sign-in/domains/{domain}/verify   check the DNS record          (admin)
    DELETE /admin/sign-in/domains/{domain}                                         (admin)
    POST   /admin/sign-in/connections {label, issuer, client_id, client_secret}    (admin)
    PATCH  /admin/sign-in/connections/{id} change any of those, or {enabled}       (admin)
    POST   /admin/sign-in/saml-connections {label, metadata_url | metadata_xml |
                                  entity_id + sso_url + certificate, email_attribute} (admin)
    PATCH  /admin/sign-in/saml-connections/{id} change any of those, or {enabled}  (admin)
    PUT    /admin/sign-in/connections/{id}/roles {group_claim, rules: [{group, role}]}
                                  roles from the groups a provider sends            (admin)
    GET    /admin/members         everyone in this workspace, with where their role comes from
    PATCH  /admin/members/{username} {role: public|hr|admin|null, disabled}       (admin)
    DELETE /admin/members/{username}                                               (admin)
    POST   /admin/members/{username}/sign-out   end all their sessions                (admin)
    DELETE /admin/sign-in/connections/{id}                                         (admin)
Chat, uploads, re-index and sync now are rate-limited per person and per workspace
(USER_LIMITS); request bodies and uploaded files are size-capped (_BodyLimit,
upload_docs); every response carries security headers (_SecurityHeaders).
Every document change re-indexes automatically; connectors re-sync every 30 minutes;
SAML providers set up from a metadata URL fetch it again daily (or as it says).

ROLES come from the account (members_store.py): public, hr or admin. Admin
counts as HR everywhere here. An HR / Admin account can send "X-Role: public" to
see the app as a public user would; the header can never raise anyone TO HR.
"""

import asyncio
import calendar
import contextlib
import json
import io
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from saml2 import time_util as saml2_time
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

import auth                          # noqa: E402  (needs the .env loaded first)
import docs_store                    # noqa: E402
import health                        # noqa: E402
import ingest                        # noqa: E402
import integrations_store            # noqa: E402
import members_store                 # noqa: E402
import observability                 # noqa: E402
import safety                        # noqa: E402
import saml_sp                       # noqa: E402
import sessions_store                # noqa: E402
import sso                           # noqa: E402
import sso_errors                    # noqa: E402
import sso_setup                     # noqa: E402
import url_guard                     # noqa: E402
import workspaces                    # noqa: E402
from agent import Cancelled, build_agent  # noqa: E402
from connectors import google_drive, notion  # noqa: E402

@contextlib.asynccontextmanager
async def _lifespan(app):
    """Background jobs that run for the life of the server."""
    safety.require_guards()          # REQUIRE_TOXICITY_GUARD=1: refuse to start without it
    threading.Thread(target=_sync_scheduler, daemon=True).start()
    threading.Thread(target=_saml_refresh_scheduler, daemon=True).start()
    yield


observability.init_error_tracking()   # only with SENTRY_DSN; before the app is made
app = FastAPI(title="Knowledge Assistant API", lifespan=_lifespan)
log = logging.getLogger("uvicorn.error")   # shows in the uvicorn console
# Holds Authlib's state / nonce / PKCE verifier between the redirect to Google or
# Microsoft and the callback — nothing else goes in it. Without SESSION_SECRET a
# random key is used, which only cancels sign-ins in progress during a restart.
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32),
                   session_cookie="kb_oauth", max_age=10 * 60, same_site="lax",
                   https_only=os.getenv("PUBLIC_URL", "").startswith("https://"))


class _BodyLimit:
    """Refuse a request body over UPLOAD_MAX_REQUEST_MB with a 413: at once when
    its Content-Length says so, else as soon as that much has arrived. So a huge
    upload is never taken in (each file has its own, smaller limit: upload_docs)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit_mb = _env_int("UPLOAD_MAX_REQUEST_MB", UPLOAD_MAX_REQUEST_MB)
        limit, received = limit_mb * 1024 * 1024, 0
        too_big = HTTPException(413, f"That's too much to send at once: the limit is {limit_mb} "
                                     "MB. Send fewer or smaller files.")
        try:
            length = int(dict(scope["headers"]).get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > limit:
            response = Response(json.dumps({"detail": too_big.detail}), 413,
                                media_type="application/json", headers={"Connection": "close"})
            return await response(scope, receive, send)

        async def counted():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise too_big           # FastAPI answers it with a 413
            return message

        await self.app(scope, counted, send)


# Content Security Policy for the site (the built React app, frontend/dist) and
# the API. Scripts only from this server: no inline or injected ones. Styles may
# be inline (UI libraries add <style> tags at run time, e.g. the toasts) and come
# from Google Fonts, like the fonts themselves (frontend/index.html). Sign-in needs nothing
# extra: the IdP posts SAML answers from its own page, and every move to a
# provider is a redirect or a top-level navigation, not a form of ours.
CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; "
       "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")
# FastAPI's own API pages (/docs, /redoc) load their scripts from a CDN, inline.
API_DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
                "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
                "worker-src 'self' blob:; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                    "Referrer-Policy": "same-origin",
                    "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}
HSTS_SECONDS = 180 * 24 * 60 * 60        # HSTS_MAX_AGE in the environment; 0 turns it off


class _SecurityHeaders:
    """Security headers on every response. HSTS only over https: as the browser
    saw it, so behind a proxy that ends TLS this needs uvicorn's --proxy-headers
    (Dockerfile) to trust its X-Forwarded-Proto."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        csp = API_DOCS_CSP if path in (app.docs_url, app.redoc_url) else CSP
        headers = {**SECURITY_HEADERS, "Content-Security-Policy": csp}
        hsts = _env_int("HSTS_MAX_AGE", HSTS_SECONDS, minimum=0)
        if scope.get("scheme") == "https" and hsts:
            headers["Strict-Transport-Security"] = f"max-age={hsts}"

        async def with_headers(message):
            if message["type"] == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (k.lower().encode(), v.encode()) for k, v in headers.items()
                    if k.lower().encode() not in have]
            await send(message)

        await self.app(scope, receive, with_headers)


app.add_middleware(_BodyLimit)
app.add_middleware(_SecurityHeaders)      # added last, so outermost: on every answer

SESSION_SECONDS = 12 * 60 * 60
# Rate limits on the sign-in endpoints anyone can call, per client address and
# minute, plus a ceiling for all addresses together (an address can be faked in
# X-Forwarded-For if the proxy settings trust anyone). RATE_LIMITS=off turns
# them off (the tests do).
LIMITS = {"login": 30, "discover": 30, "sso_start": 60, "saml_acs": 60, "dns_check": 20,
          "signup": 10}
ALL_ADDRESSES = 20                       # x the per-address limit, per minute
# Limits on what a signed-in person can make the server do (it costs OpenAI calls
# or indexing time), per minute: (per person, per workspace). Each can be set in
# the environment, e.g. RATE_LIMIT_CHAT_USER=40, RATE_LIMIT_CHAT_WORKSPACE=600.
USER_LIMITS = {"chat": (20, 300), "upload": (30, 120), "reindex": (5, 20), "sync": (5, 20)}
_hits = {}                # (bucket, address | user | workspace) -> recent times
_hits_lock = threading.Lock()
# Uploads: each file, and each request as a whole, in MB (UPLOAD_MAX_FILE_MB,
# UPLOAD_MAX_REQUEST_MB). Every other request body has the per-request limit too.
UPLOAD_MAX_FILE_MB = 25
UPLOAD_MAX_REQUEST_MB = 100
# Kept with the session (for signing out at the provider), never sent to the browser.
PRIVATE_USER_KEYS = ("id_token",)

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[a-z0-9.-]{1,253}\.[a-z]{2,63}$")
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60
STEP_PREVIEW_CHARS = 600
DRIVE_SYNC_EVERY = 30 * 60        # seconds between automatic connector syncs
OAUTH_STATE_SECONDS = 10 * 60

_failed_logins = {}       # username -> (count, locked_until)
_agents = {}              # (workspace, role) -> agent run() function
_agents_lock = threading.Lock()

# ---- Background re-indexing (per workspace) ----
# Every document change schedules a re-index of THAT workspace in a background
# thread; the UI polls GET /api/index/status. Changes made while an index runs
# set `pending`, so one more pass runs right after (they are coalesced).
_indexes = {}             # workspace -> status dict
_index_lock = threading.Lock()
_ingest_lock = threading.Lock()   # one rebuild at a time (shared Chroma store)
# Chat questions wait while their workspace's index is swapped, and the swap
# waits for questions already running — so nobody searches a half-built index.
_gate = threading.Condition()
_gate_state = {}          # workspace -> {"indexing": bool, "active": int}

_oauth_states = {}        # state -> {"workspace", "user", "expires", "redirect_uri"}
_syncing = set()          # (workspace, provider) syncs that are running
_sync_lock = threading.Lock()


def _new_index_state():
    return {"status": "idle", "step": 0, "total": 4, "message": "", "pending": False,
            "summary": None, "error": None, "finished_at": None}


def _gate_of(ws):
    return _gate_state.setdefault(ws, {"indexing": False, "active": 0})


# ---- Auth --------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str
    password: str


def current_user(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    user = sessions_store.get(token)
    if not user:
        raise HTTPException(401, "Please sign in.")
    # Workspace and role are looked up again on EVERY request, so removing someone
    # (or their HR role) takes effect now, not when their 12-hour session ends.
    try:
        access = (sso.access_for(user["username"], user.get("org"), user.get("connection"))
                  if user.get("provider")
                  else auth.access_for(user["username"]))
    except (OSError, sqlite3.Error):         # e.g. the database can't be read
        log.exception("Couldn't read the sign-in settings")
        raise HTTPException(503, "Sign-in settings can't be read. Please tell your admin.")
    if not access:
        sessions_store.delete(token)
        raise HTTPException(401, "Please sign in.")
    return {**user, **access}


def _rate_limits_on():
    return os.getenv("RATE_LIMITS", "on").strip().lower() not in ("off", "false", "0", "no")


def _take(checks):
    """Count one hit against each (key, limit, message) for the last minute, or
    none if any is used up: then a 429 with that message and Retry-After."""
    now = time.time()
    with _hits_lock:
        if len(_hits) > 10_000:                       # forget the quiet ones
            for k in [k for k, v in _hits.items() if not v or v[-1] < now - 60]:
                del _hits[k]
            if len(_hits) > 10_000:                   # a flood of fake addresses
                _hits.clear()
        recents = []
        for key, limit, message in checks:
            recent = [t for t in _hits.get(key, []) if t > now - 60]
            if len(recent) >= limit:
                wait = max(1, int(recent[-limit] + 60 - now + 0.999))
                raise HTTPException(429, message.format(wait=wait, s="" if wait == 1 else "s"),
                                    headers={"Retry-After": str(wait)})
            recents.append((key, recent))
        for key, recent in recents:
            _hits[key] = recent + [now]


def rate_limit(request: Request, bucket):
    if not _rate_limits_on():
        return
    address = request.client.host if request.client else "?"
    message = "Too many attempts. Wait {wait} second{s} and try again."
    _take([((bucket, address), LIMITS[bucket], message),
           ((bucket, "*"), LIMITS[bucket] * ALL_ADDRESSES, message)])


def user_limit(user, bucket):
    """Per person and per workspace limits (USER_LIMITS) on something costly."""
    if not _rate_limits_on():
        return
    per_user, per_workspace = (_env_int(f"RATE_LIMIT_{bucket.upper()}_{who}", default)
                               for who, default in zip(("USER", "WORKSPACE"),
                                                       USER_LIMITS[bucket]))
    verb, noun = {"chat": ("asked", "question"), "upload": ("uploaded files", "time"),
                  "reindex": ("started", "re-index"), "sync": ("started", "sync")}[bucket]

    def did(n):
        return f"{verb} {n} {noun}{'' if n == 1 else 'es' if noun.endswith('x') else 's'}"
    _take([((bucket, "user", user["username"]), per_user,
            f"You've {did(per_user)} in the last minute, the most one person can. "
            "Try again in {wait} second{s}."),
           ((bucket, "workspace", ws_of(user)), per_workspace,
            f"Your workspace has {did(per_workspace)} in the last minute, the most it can. "
            "Try again in {wait} second{s}.")])


def _env_int(name, default, minimum=1):
    """A whole number from the environment, or `default` if it isn't set (or isn't one)."""
    try:
        return max(minimum, int(os.getenv(name, "")))
    except ValueError:
        return default


def public_user(user):
    return {k: v for k, v in user.items() if k not in PRIVATE_USER_KEYS}


def current_role(user=Depends(current_user), x_role: str = Header(default="hr")):
    """HR only if the ACCOUNT is HR or admin; X-Role can only lower it ("view as public")."""
    return "hr" if user["role"] in members_store.STAFF_ROLES and x_role == "hr" else "public"


def require_hr(user=Depends(current_user), role=Depends(current_role)):
    if role != "hr":
        raise HTTPException(403, "Switch to HR / Admin to manage documents and integrations."
                            if user["role"] in members_store.STAFF_ROLES else
                            "Only HR / Admin accounts can manage documents and integrations.")
    return user


def require_admin(user=Depends(current_user), role=Depends(current_role)):
    """Admins only, and not while viewing the app as a public user."""
    if user["role"] != "admin" or role != "hr":
        raise HTTPException(403, "Switch to HR / Admin to change sign-in settings."
                            if user["role"] == "admin" else
                            "Only admins can change sign-in settings.")
    return user


def ws_of(user):
    ws = user.get("workspace") or workspaces.DEFAULT
    if not workspaces.valid(ws):
        raise HTTPException(400, "Invalid workspace on this account.")
    return ws


@app.get("/api/auth/providers")
def auth_providers():
    try:
        email_sso = members_store.any_connections()
    except sqlite3.Error:
        log.exception("Couldn't read the sign-in connections")
        email_sso = False
    return {"providers": [{"id": p, "name": sso.label(p)} for p in sso.configured()],
            "password": auth.password_login_on(), "email_sso": email_sso,
            "signup": signup_on(), "signup_invite": bool(signup_codes())}


def signup_on():
    """Can a new company create a workspace here? Only when whoever runs the server
    says so (WORKSPACE_SIGNUP=on), and only with Google Workspace, which proves the
    founder's company owns its domain."""
    return (os.getenv("WORKSPACE_SIGNUP", "off").strip().lower() in ("on", "true", "1", "yes")
            and "google" in sso.configured())


def signup_codes():
    """Invite codes (WORKSPACE_SIGNUP_CODES, comma-separated). When there are any,
    sign-up needs one, and each makes one workspace."""
    return {c.strip() for c in os.getenv("WORKSPACE_SIGNUP_CODES", "").split(",") if c.strip()}


@app.post("/api/auth/login")
async def login(body: LoginIn, request: Request):
    rate_limit(request, "login")
    if not auth.password_login_on():
        raise HTTPException(403, "Password sign-in is turned off. Use single sign-on.")
    key = body.username.strip().lower()
    count, locked_until = _failed_logins.get(key, (0, 0))
    if time.time() < locked_until:
        raise HTTPException(429, f"Too many failed attempts. Try again in "
                                 f"{int(locked_until - time.time())} seconds.")
    if not members_store.has_password_accounts():
        raise HTTPException(503, "No user accounts exist yet. Create one with: "
                                 "python auth.py add <username>")
    # Hashing is slow on purpose: off the event loop.
    member = await run_in_threadpool(auth.check_password, body.username, body.password)
    if member and not auth.sso_required(member):
        _failed_logins.pop(key, None)
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
    if len(_failed_logins) > 10_000:
        for k in [k for k, (_, until) in _failed_logins.items() if until < time.time()]:
            del _failed_logins[k]
    _failed_logins[key] = ((0, time.time() + LOCKOUT_SECONDS)
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


# ---- Single sign-on (sso.py) ---------------------------------------------------
# Errors go back to the login page as /#login?error=<code>&ref=<reference>; it
# explains each code in general terms. The reference is on the admin's log entry
# (and in the server log), which says exactly why (sso_errors.py).
class CodeIn(BaseModel):
    code: str


def _login_error(error, ref=None, status_code=307, tab="login"):
    """Back to the sign-in page (or its Create a workspace tab) with what went wrong."""
    query = {"error": error, **({"ref": ref} if ref else {})}
    return RedirectResponse(f"/#{tab}?" + urllib.parse.urlencode(query), status_code=status_code)


class _ProviderFailed(Exception):
    """The provider's answer can't be used. `error` is for the login page, `code`
    (sso_errors.py) for the admin, and `detail` (partly the provider's own
    words) only for the server log and the admin's Test sign-in."""

    def __init__(self, error, code, detail):
        super().__init__(detail)
        self.error, self.code, self.detail = error, code, detail


# The one-time code a sign-in ends with only works in the browser that started it:
# /start puts an ID in this browser's (Lax, signed) sign-in cookie, the code is
# tied to it, and /auth/sso/exchange (same-site POST, so the cookie comes along)
# checks it. Otherwise someone could finish a sign-in as themselves and send
# you the link, signing you in to their account.
BROWSER_KEY = "sso_browser"


def _mark_browser(request):
    request.session.setdefault(BROWSER_KEY, secrets.token_urlsafe(16))


def _code_for(request, user):
    return sso.issue_code(user, request.session.get(BROWSER_KEY))


@app.get("/api/auth/sso/{provider}/start")
async def sso_start(provider: str, request: Request):
    rate_limit(request, "sso_start")
    c = sso.client(provider)
    if not c:
        return RedirectResponse("/#login?error=sso_unavailable")
    request.session.pop(SIGNUP_KEY, None)
    _mark_browser(request)
    try:
        return await c.authorize_redirect(
            request, _public_url(request, f"/api/auth/sso/{provider}/callback"),
            **sso.authorize_params(provider))
    except Exception:              # couldn't reach the provider's discovery document
        log.exception("SSO %s: couldn't start sign-in", provider)
        return RedirectResponse("/#login?error=sso_failed")


@app.get("/api/auth/sso/{provider}/callback")
async def sso_callback(provider: str, request: Request):
    c = sso.client(provider)
    if not c:
        return RedirectResponse("/#login?error=sso_unavailable")
    signup = request.session.pop(SIGNUP_KEY, None)
    if provider == "google" and signup and time.time() - signup.get("at", 0) < SIGNUP_SECONDS:
        return await _finish_signup(c, request, signup["name"], signup.get("code"))
    return await _finish_sso(provider, c, request, lambda claims: sso.user_for(provider, claims))


# ---- Sign-up: a new company creates its workspace ---------------------------------
# The founder names the workspace, then signs in with Google. Their Google
# Workspace account proves their company owns its primary domain (the `hd`
# claim), so that domain is verified for the new workspace straight away and
# they become its admin. It reuses the Google callback, so the provider needs no
# new redirect URI: /start marks this browser's sign-in cookie, the callback
# takes the mark off.
SIGNUP_KEY = "signup"
SIGNUP_SECONDS = 15 * 60


@app.get("/api/auth/signup/google/start")
async def signup_start(request: Request, name: str = "", code: str = ""):
    rate_limit(request, "signup")
    name, code = " ".join(name.split()), code.strip()
    if not signup_on():
        return _login_error("signup_off")
    if not 1 <= len(name) <= 60:
        return RedirectResponse("/#signup?error=name")
    codes = signup_codes()
    if codes and (code not in codes or members_store.signup_code_used(code)):
        return RedirectResponse("/#signup?error=invite")
    c = sso.client("google")
    _mark_browser(request)
    request.session[SIGNUP_KEY] = {"name": name, "code": code if codes else None,
                                   "at": time.time()}
    try:
        return await c.authorize_redirect(
            request, _public_url(request, "/api/auth/sso/google/callback"),
            **sso.authorize_params("google"))
    except Exception:              # couldn't reach Google's discovery document
        log.exception("Sign-up: couldn't start the Google sign-in")
        return _login_error("sso_failed", tab="signup")


async def _finish_signup(c, request, name, code=None):
    """Google sent the founder back: create the workspace, or say why not."""
    try:
        claims, _ = await _provider_answer("google", c, request)
        email, person, org = sso.identity("google", claims)
    except _ProviderFailed as e:
        ref = sso_errors.new_ref()
        log.warning("Sign-up: Google sign-in failed (%s), reference %s", e.code, ref)
        return _login_error(e.error, ref, tab="signup")
    except sso.NotAllowed as e:    # unverified, or a personal account
        return _login_error(e.reason, tab="signup")
    if email.rpartition("@")[2] != org:
        return _login_error("signup_domain", tab="signup")
    codes = signup_codes()
    if codes and code not in codes:          # removed from the list meanwhile
        return _login_error("invite", tab="signup")
    try:
        workspace = members_store.create_workspace(name, org, email, person,
                                                   code=code if codes else None)
    except members_store.WorkspaceTaken as e:
        return _login_error(e.reason, tab="signup")
    except Exception:
        ref = sso_errors.new_ref()
        log.exception("Sign-up: couldn't create the workspace, reference %s", ref)
        return _login_error("sso_failed", ref, tab="signup")
    log.warning("Sign-up: %s created the workspace %s for %s", email, workspace, org)
    members_store.log_event(workspace, "google", email, "signed_in")
    user = {"username": email, "name": person, "provider": "google", "org": org,
            "workspace": workspace, "role": "admin"}
    return RedirectResponse("/#sso?welcome=1&code=" + _code_for(request, user))


async def _provider_answer(name, c, request):
    """The provider sent the browser back: (claims, id token) if its answer checks
    out, else _ProviderFailed."""
    error = request.query_params.get("error")
    if error:                      # the provider refused; log why (e.g. admin consent needed)
        detail = f"{error}: {request.query_params.get('error_description', '')}".rstrip(": ")
        log.warning("SSO %s: provider returned %s", name, detail)
        raise _ProviderFailed("cancelled" if error == "access_denied" else "sso_failed",
                              sso_errors.from_provider_error(error), detail)
    try:
        token = await c.authorize_access_token(request)    # checks state + the ID token
    except Exception as e:
        log.exception("SSO %s: sign-in callback failed", name)
        raise _ProviderFailed("sso_failed", sso_errors.from_oidc_exception(e),
                              f"{type(e).__name__}: {e}") from None
    return token.get("userinfo") or {}, token.get("id_token")


async def _finish_sso(name, c, request, user_for):
    """Check the provider's answer, then hand the browser a one-time code for the
    session. No workspace's admin owns a server-wide provider, so its failures
    are only in the server log (with the reference)."""
    try:
        claims, _ = await _provider_answer(name, c, request)
    except _ProviderFailed as e:
        ref = sso_errors.new_ref()
        log.warning("SSO %s: sign-in failed (%s), reference %s", name, e.code, ref)
        return _login_error(e.error, ref)
    try:
        user = user_for(claims)
    except sso.NotAllowed as e:
        return _login_error(e.reason, _log_refusal(name, claims, e.reason))
    except Exception:              # e.g. the database can't be read
        ref = sso_errors.new_ref()
        log.exception("SSO %s: couldn't decide who gets in, reference %s", name, ref)
        return _login_error("sso_failed", ref)
    members_store.log_event(user["workspace"], name, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + _code_for(request, user))


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


# ---- "Test sign-in": an admin tries a provider in a popup --------------------------
# The admin gets a ticket (POST /api/admin/sign-in/connections/{id}/test) and opens
# the provider's start URL with it. The answer is checked exactly as for a real
# sign-in, but nobody is signed in or added: the result (what the provider sent,
# and what would happen) waits under the ticket for the admin's screen to fetch.
# It works on a provider that's turned off, so it can be tried before going live.
SSO_TEST_SECONDS = 10 * 60
_sso_tests = {}            # ticket -> {"workspace", "cid", "expires", "result"}
_sso_tests_lock = threading.Lock()
# A test's OIDC state starts with this, and maps to its ticket in the sign-in
# cookie: a test answer is never taken for a real sign-in, even once the ticket
# has expired.
TEST_STATE_PREFIX = "t."
SSO_TESTS_SESSION_KEY = "sso_tests"


def _new_sso_test(workspace, cid):
    ticket, now = secrets.token_urlsafe(24), time.time()
    with _sso_tests_lock:
        for t in [t for t, v in _sso_tests.items() if v["expires"] < now]:
            del _sso_tests[t]
        _sso_tests[ticket] = {"workspace": workspace, "cid": cid, "result": None,
                              "expires": now + SSO_TEST_SECONDS}
    return ticket


def _sso_test(ticket, cid):
    """The test a ticket started for this connection, if it's still running."""
    with _sso_tests_lock:
        test = _sso_tests.get(ticket or "")
    if test and test["cid"] == cid and test["expires"] > time.time() and not test["result"]:
        return test
    return None


def _shown(values):
    """What the provider sent, for the admin to read (short, no token internals)."""
    skip = {"nonce", "at_hash", "c_hash", "auth_time", "iat", "exp", "nbf"}
    return {k: (", ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v))[:300]
            for k, v in list((values or {}).items())[:60] if k not in skip}


def _explained(code):
    """The admin's reason and fix for a code (sso_errors.py), if it's a known one."""
    found = sso_errors.describe(code) or {"reason": None, "fix": None}
    return {"reason_text": found["reason"], "fix": found["fix"]}


def _finish_sso_test(test, conn, result, sent=None):
    result = {**result, **({} if result["ok"] else _explained(result["reason"])),
              "sent": _shown(sent)}
    with _sso_tests_lock:
        test["result"] = result
    members_store.log_event(conn["workspace"], conn["id"], result.get("email"), "test",
                            None if result["ok"] else result.get("reason"))
    return RedirectResponse("/#sso-test", status_code=303)


def _test_failure(code, detail):
    return {"ok": False, "reason": code, "detail": (detail or "")[:300] or None}


def _connection_or_none(cid):
    try:
        return members_store.get_connection(cid)
    except sqlite3.Error:
        log.exception("Couldn't read sign-in connection %s", cid)
        return None


# Failed sign-ins are logged for the admin, but anyone can call a callback with
# an error: only so many per provider per minute, so the log can't be flooded.
FAILURES_LOGGED_PER_MINUTE = 20
_failures_logged = {}      # connection id -> recent times


def _log_failure(conn, code):
    """Log a failed sign-in (a code from sso_errors.py) for the admin. Returns the
    reference to show the person; it's in the server log even when the admin's
    log is full for the minute."""
    ref, now = sso_errors.new_ref(), time.time()
    log.warning("SSO connection %s: sign-in failed (%s), reference %s", conn["id"], code, ref)
    with _hits_lock:
        recent = [t for t in _failures_logged.get(conn["id"], []) if t > now - 60]
        if len(recent) >= FAILURES_LOGGED_PER_MINUTE:
            return ref
        _failures_logged[conn["id"]] = recent + [now]
    try:
        members_store.log_event(conn["workspace"], conn["id"], None, "failed", code, ref)
    except sqlite3.Error:
        log.exception("Couldn't log a failed sign-in")
    return ref


# A workspace's own provider, found from the email someone types on the login page.
class EmailIn(BaseModel):
    email: str = Field(max_length=320)


def _usable_connection(cid, protocol):
    try:
        conn = members_store.get_connection(cid)
    except sqlite3.Error:
        log.exception("Couldn't read sign-in connection %s", cid)
        return None
    return conn if conn and conn["enabled"] and conn["protocol"] == protocol else None


def _start_url(conn, email):
    if conn["protocol"] == "saml":
        return f"/api/auth/saml/{conn['id']}/start"
    return f"/api/auth/sso/c/{conn['id']}/start?" + urllib.parse.urlencode({"login_hint": email})


@app.post("/api/auth/sso/discover")
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
                          "roles_from": conn["label"] if conn and conn["enabled"] else None}}


@app.get("/api/auth/sso/c/{cid}/start")
async def sso_connection_start(cid: str, request: Request, login_hint: str = "",
                               test: str = ""):
    rate_limit(request, "sso_start")
    trial = _sso_test(test, cid)
    conn = _connection_or_none(cid) if trial else _usable_connection(cid, "oidc")
    if not conn or conn["protocol"] != "oidc":
        return RedirectResponse("/#login?error=sso_unavailable")
    _mark_browser(request)
    extra = {"login_hint": login_hint} if EMAIL_RE.match(login_hint) else {}
    if trial:
        extra["prompt"] = "login"          # show the admin the provider's sign-in page
        extra["state"] = TEST_STATE_PREFIX + secrets.token_urlsafe(24)
        tests = dict(list(request.session.get(SSO_TESTS_SESSION_KEY, {}).items())[-4:])
        request.session[SSO_TESTS_SESSION_KEY] = {**tests, extra["state"]: test}
    try:
        return await sso.connection_client(conn).authorize_redirect(
            request, _public_url(request, f"/api/auth/sso/c/{cid}/callback"), **extra)
    except Exception as e:
        log.exception("SSO connection %s: couldn't start sign-in", cid)
        return _login_error("sso_failed", _log_failure(conn, sso_errors.from_oidc_exception(e)))


@app.get("/api/auth/sso/c/{cid}/callback")
async def sso_connection_callback(cid: str, request: Request):
    state = request.query_params.get("state", "")
    trial = None
    if state.startswith(TEST_STATE_PREFIX):
        tests = request.session.get(SSO_TESTS_SESSION_KEY, {})
        trial = _sso_test(tests.get(state), cid)
        request.session[SSO_TESTS_SESSION_KEY] = {k: v for k, v in tests.items() if k != state}
        if not trial:                      # expired or unknown: a test, never a sign-in
            return RedirectResponse("/#sso-test", status_code=303)
    conn = _connection_or_none(cid) if trial else _usable_connection(cid, "oidc")
    if not conn or conn["protocol"] != "oidc":
        return RedirectResponse("/#login?error=sso_unavailable")
    # Did this browser start a sign-in here? (Authlib keeps its state in the cookie.)
    started_here = any(k.startswith(f"_state_conn_{cid}_") for k in request.session)
    try:
        try:
            client = sso.connection_client(conn)
        except Exception as e:     # e.g. its secret can't be decrypted any more
            log.exception("SSO connection %s: can't use its settings", cid)
            raise _ProviderFailed("sso_failed", "settings_unreadable",
                                  f"{type(e).__name__}: {e}") from None
        claims, id_token = await _provider_answer(f"connection {cid}", client, request)
    except _ProviderFailed as e:
        if trial:
            return _finish_sso_test(trial, conn, _test_failure(e.code, e.detail))
        # Only for a browser that started a sign-in here: anyone can open this
        # link with an error in it. The code only, never the provider's words.
        return _login_error(e.error, _log_failure(conn, e.code) if started_here else None)
    if trial:
        try:
            result = sso.try_connection(conn, claims)
        except Exception as e:
            log.exception("SSO connection %s: test failed", cid)
            result = _test_failure("sso_failed", f"{type(e).__name__}: {e}")
        return _finish_sso_test(trial, conn, result, claims)
    try:
        user = sso.user_for_connection(conn, claims)
    except sso.NotAllowed as e:
        ref = sso_errors.new_ref()
        members_store.log_event(conn["workspace"], cid, claims.get("email"), "refused", e.reason,
                                ref)
        return _login_error(e.reason, ref)
    except Exception:
        log.exception("SSO connection %s: couldn't decide who gets in", cid)
        return _login_error("sso_failed", _log_failure(conn, "sso_failed"))
    if conn["metadata"].get("end_session_endpoint") and id_token:
        user["id_token"] = id_token           # for signing out there (POST /auth/logout)
    members_store.log_event(conn["workspace"], cid, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + _code_for(request, user))


# ---- SAML (saml_sp.py) -------------------------------------------------------------
# The IdP posts its answer from its own site, so our Lax cookie doesn't come with
# it. /acs checks the answer and parks it for a minute; /done, a same-site GET,
# checks the cookie set at /start, so an answer only signs in the browser that
# asked for it (no logging someone in to an attacker's account).
_saml_parked = {}          # key -> {"cid", "request_id", "email", "name", "sent", "expires"}
_saml_lock = threading.Lock()
SAML_PARK_SECONDS = 60
SAML_SESSION_KEY = "saml_requests"
SAML_TESTS_SESSION_KEY = "saml_tests"     # request id -> test ticket
TEST_RELAY_PREFIX = "t:"


def _saml_conn(cid, trial=None):
    """(connection, its keys), or (None, None) if it can't sign anyone in now.
    A test may use a connection that's turned off."""
    conn = _connection_or_none(cid) if trial else _usable_connection(cid, "saml")
    if not conn or conn["protocol"] != "saml" or not saml_sp.xmlsec_binary():
        return None, None
    try:
        return conn, members_store.connection_keys(cid)
    except Exception:              # e.g. INTEGRATIONS_KEY changed: can't decrypt its key
        log.exception("SAML %s: can't read its signing key", cid)
        return None, None


# ---- The IdP's metadata, fetched again from its URL ------------------------------------
# An IdP rotates its signing key by publishing the new certificate in its metadata
# (often beside the old one for a while). A provider set up from a metadata URL
# fetches it again: on a schedule (daily, or sooner if the metadata says), and
# when an answer's signature fails (at most every SAML_REFRESH_GAP). Only changes
# from the SAME IdP are applied: a new entity ID, or a sign-in URL on another
# host, is held back for the admin to look at ("needs review").
# Every worker runs the schedule; each fetch is claimed in the database first
# (members_store.claim_metadata_fetch), so a provider is fetched by one of them.
SAML_REFRESH_EVERY = 24 * 60 * 60        # unless its validUntil / cacheDuration say sooner
SAML_REFRESH_CHECK_EVERY = 10 * 60       # how often the schedule looks; also the shortest wait
SAML_REFRESH_RETRY = 60 * 60             # after a failed fetch
SAML_REFRESH_GAP = 5 * 60                # at most one fetch per provider this often
CERT_UPDATED = "Certificate updated automatically"


def _next_refresh(idp):
    """When metadata just read (saml_sp.read_idp_metadata) should be fetched again."""
    wait = min(SAML_REFRESH_EVERY, idp["refresh_within"] or SAML_REFRESH_EVERY)
    return time.time() + max(SAML_REFRESH_CHECK_EVERY, wait)


def _metadata_attention(conn, column, problem, reason, **status):
    """Record a metadata problem; tell the admin when it's a new one."""
    members_store.set_metadata_status(conn["id"], **{column: problem}, **status)
    if conn.get(column) != problem:
        members_store.log_event(conn["workspace"], conn["id"], None, "attention", reason)
        # TODO: email the workspace's admins as well, once there's a way to send mail.


def _refresh_saml_metadata(conn, due_by=None):
    """Fetch a SAML provider's metadata from its URL again (the same checks as
    when it was added), and apply it if it's from the same IdP. Returns the
    connection as it is now, or None if this didn't fetch: it has no URL, it
    isn't due by `due_by`, or it was fetched less than SAML_REFRESH_GAP ago (by
    any worker)."""
    settings, cid = conn["metadata"], conn["id"]
    url = settings.get("metadata_url")
    if not url or not members_store.claim_metadata_fetch(cid, SAML_REFRESH_GAP, due_by):
        return None
    try:
        xml = sso_setup.fetch_saml_metadata(url).decode("utf-8", "replace")
        idp = _read_idp(xml)
    except (sso_setup.SetupError, saml_sp.SamlError, url_guard.UnsafeURL) as e:
        log.warning("SAML %s: couldn't refresh its metadata: %s", cid, e)
        # Only our own checks' words reach the admin, never what the provider sent.
        problem = ("couldn't fetch it from the metadata URL." if isinstance(e, sso_setup.SetupError)
                   else str(e))
        _metadata_attention(conn, "metadata_error", problem,
                            f"Metadata refresh failed: {problem}",
                            metadata_due_at=time.time() + SAML_REFRESH_RETRY)
        return members_store.get_connection(cid)
    old_host, new_host = (urllib.parse.urlsplit(u or "").hostname
                          for u in (settings.get("sso_url"), idp["sso_url"]))
    if idp["entity_id"] != conn["issuer"]:
        review = "the identity provider's entity ID changed"
    elif new_host != old_host:
        review = "its sign-in URL moved to another host"
    else:
        review = None
    if review:
        log.warning("SAML %s: fetched metadata not applied: %s", cid, review)
        _metadata_attention(conn, "needs_review", review,
                            f"Metadata change needs review: {review}. Nothing was changed: "
                            "if it's right, load it again (Edit → Metadata URL).",
                            metadata_error=None, metadata_due_at=_next_refresh(idp))
        return members_store.get_connection(cid)
    status = {"metadata_refreshed_at": time.time(), "metadata_due_at": _next_refresh(idp),
              "needs_review": None, "metadata_error": None}
    if xml == settings.get("idp_metadata"):
        members_store.set_metadata_status(cid, **status)
        return members_store.get_connection(cid)
    try:
        before = saml_sp.read_idp_metadata(settings["idp_metadata"])["fingerprints"]
    except (KeyError, saml_sp.SamlError):
        before = None
    new = {**settings, "idp_metadata": xml, "sso_url": idp["sso_url"],
           "want_authn_requests_signed": idp["want_authn_requests_signed"]}
    if not members_store.replace_metadata(cid, conn["updated_at"], new, **status):
        return members_store.get_connection(cid)    # an admin changed it meanwhile: theirs stands
    if before != idp["fingerprints"]:
        log.warning("SAML %s: its IdP's signing certificates changed; updated", cid)
        members_store.log_event(conn["workspace"], cid, None, "updated", CERT_UPDATED)
    elif (idp["sso_url"], idp["want_authn_requests_signed"]) != (
            settings.get("sso_url"), bool(settings.get("want_authn_requests_signed"))):
        members_store.log_event(conn["workspace"], cid, None, "updated",
                                "Sign-in settings updated automatically from the metadata")
    return members_store.get_connection(cid)


def _saml_newer_metadata(conn, trial):
    """After an answer's signature failed: (connection, keys) with IdP metadata
    newer than `conn`'s, fetched now or by another worker just before; or None."""
    if not conn["metadata"].get("metadata_url"):
        return None
    try:
        _refresh_saml_metadata(conn)
    except Exception:              # e.g. the database is busy: the answer is just refused
        log.exception("SAML %s: couldn't refresh its metadata", conn["id"])
    newer, keys = _saml_conn(conn["id"], trial)
    if newer and newer["metadata"].get("idp_metadata") != conn["metadata"].get("idp_metadata"):
        return newer, keys
    return None


def _refresh_due_saml_metadata(now=None):
    """Fetch the metadata of every provider set up from a URL that's due by `now`."""
    now = now or time.time()
    try:
        connections = members_store.saml_connections_from_urls()
    except sqlite3.Error:
        log.exception("Couldn't list the SAML providers to refresh")
        return
    for c in connections:
        if (c["metadata_due_at"] or 0) <= now:
            try:
                _refresh_saml_metadata(c, due_by=now)
            except Exception:
                log.exception("SAML %s: couldn't refresh its metadata", c["id"])


def _saml_refresh_scheduler():
    while True:
        time.sleep(SAML_REFRESH_CHECK_EVERY)
        _refresh_due_saml_metadata()


_sp_metadata_cache = {}    # (connection id, updated_at, base URL) -> XML


@app.get("/api/auth/saml/{cid}/metadata")
def saml_metadata(cid: str, request: Request):
    """Our SP's metadata, for the customer's IdP (public, like any SP's). Built
    once per version of the connection: it needs the decrypted key."""
    conn = _connection_or_none(cid)
    if not conn or conn["protocol"] != "saml" or not saml_sp.xmlsec_binary():
        raise HTTPException(404, "No such SAML sign-in.")
    base = _public_url(request, "")
    key = (cid, conn["updated_at"], base)
    if key not in _sp_metadata_cache:
        _, keys = _saml_conn(cid, trial=True)
        if not keys:
            raise HTTPException(503, "This sign-in's settings can't be read. Tell the admin.")
        if len(_sp_metadata_cache) > 1000:
            _sp_metadata_cache.clear()
        _sp_metadata_cache[key] = saml_sp.sp_metadata(conn, keys, base)
    return Response(_sp_metadata_cache[key], media_type="application/samlmetadata+xml")


@app.get("/api/auth/saml/{cid}/start")
def saml_start(cid: str, request: Request, test: str = ""):
    rate_limit(request, "sso_start")
    trial = _sso_test(test, cid)
    conn, keys = _saml_conn(cid, trial)
    if not conn:
        return RedirectResponse("/#login?error=sso_unavailable")
    try:
        request_id, url = saml_sp.start(conn, keys, _public_url(request, ""),
                                        TEST_RELAY_PREFIX + test if trial else "kb",
                                        force_authn=bool(trial))
    except Exception:
        log.exception("SAML %s: couldn't start sign-in", cid)
        return _login_error("sso_failed", _log_failure(conn, "saml_invalid"))
    members_store.remember_saml_request(cid, request_id, saml_sp.REQUEST_SECONDS)
    _mark_browser(request)
    # This browser started it. Keep the last few, in case of several tabs.
    request.session[SAML_SESSION_KEY] = (request.session.get(SAML_SESSION_KEY, []) +
                                         [request_id])[-5:]
    if trial:
        tests = dict(list(request.session.get(SAML_TESTS_SESSION_KEY, {}).items())[-4:])
        request.session[SAML_TESTS_SESSION_KEY] = {**tests, request_id: test}
    return RedirectResponse(url)


@app.post("/api/auth/saml/{cid}/acs")
def saml_acs(cid: str, request: Request, SAMLResponse: str = Form(""),
             RelayState: str = Form("")):
    rate_limit(request, "saml_acs")
    # Only to report a failed TEST to the admin; it never lets anything through.
    trial = (_sso_test(RelayState[len(TEST_RELAY_PREFIX):], cid)
             if RelayState.startswith(TEST_RELAY_PREFIX) else None)
    conn, keys = _saml_conn(cid, trial)
    if not conn:
        return RedirectResponse("/#login?error=sso_unavailable", status_code=303)

    def refuse(code, detail):
        if trial:
            return _finish_sso_test(trial, conn, _test_failure(code, detail))
        return _login_error("sso_failed", _log_failure(conn, code), status_code=303)

    def check(conn, keys):
        return saml_sp.finish(conn, keys, _public_url(request, ""), SAMLResponse,
                              members_store.open_saml_requests(cid))

    try:
        try:
            answer = check(conn, keys)
        except saml_sp.SamlError as e:
            # Signed with a key we don't know: the IdP may have a new one. Check
            # the SAME answer against its metadata as it is now. Nothing has been
            # used up by a check (that's below, once), so every rule still holds.
            newer = _saml_newer_metadata(conn, trial) if e.bad_signature else None
            if not newer:
                raise
            log.warning("SAML %s: a signature failed; checking again with new metadata", cid)
            conn, keys = newer
            answer = check(conn, keys)
    except saml_sp.SamlError as e:
        log.warning("SAML %s: refused an answer: %s", cid, e)
        return refuse(e.code, f"SAML: {e}")
    expires = time.time() + saml_sp.REQUEST_SECONDS
    if answer["expires"]:
        expires = calendar.timegm(saml2_time.str_to_time(answer["expires"]))
    if not (members_store.close_saml_request(answer["request_id"]) and
            members_store.first_use_of_assertion(answer["assertion_id"], expires)):
        log.warning("SAML %s: refused a replayed answer", cid)
        return refuse("saml_replayed", "SAML: this answer was already used")
    key, now = secrets.token_urlsafe(24), time.time()
    with _saml_lock:
        for k in [k for k, v in _saml_parked.items() if v["expires"] < now]:
            del _saml_parked[k]
        _saml_parked[key] = {"cid": cid, "request_id": answer["request_id"],
                             "email": answer["email"], "name": answer["name"],
                             "sent": answer["sent"], "expires": now + SAML_PARK_SECONDS}
    return RedirectResponse(f"/api/auth/saml/{cid}/done?" + urllib.parse.urlencode({"k": key}),
                            status_code=303)


@app.get("/api/auth/saml/{cid}/done")
def saml_done(cid: str, request: Request, k: str = ""):
    with _saml_lock:
        parked = _saml_parked.pop(k, None)
    if not parked or parked["cid"] != cid or parked["expires"] < time.time():
        return RedirectResponse("/#login?error=expired")
    started_here = request.session.get(SAML_SESSION_KEY, [])
    request_id = parked["request_id"]
    if request_id not in started_here:
        log.warning("SAML %s: an answer arrived in a browser that didn't start it", cid)
        return RedirectResponse("/#login?error=sso_failed")
    request.session[SAML_SESSION_KEY] = [r for r in started_here if r != request_id]
    tests = request.session.get(SAML_TESTS_SESSION_KEY, {})
    trial = _sso_test(tests.get(request_id), cid)
    if request_id in tests:
        request.session[SAML_TESTS_SESSION_KEY] = {r: t for r, t in tests.items()
                                                   if r != request_id}
        if not trial:                      # expired: a test, never a sign-in
            return RedirectResponse("/#sso-test")
    conn, _ = _saml_conn(cid, trial)
    if not conn:
        return RedirectResponse("/#login?error=sso_unavailable")
    # The attributes too, for the provider's group rules.
    claims = {**(parked["sent"] or {}), "email": parked["email"], "name": parked["name"]}
    if trial:
        try:
            result = sso.try_connection(conn, claims)
        except Exception as e:
            log.exception("SAML %s: test failed", cid)
            result = _test_failure("sso_failed", f"{type(e).__name__}: {e}")
        return _finish_sso_test(trial, conn, result, parked["sent"])
    try:
        user = sso.user_for_connection(conn, claims)
    except sso.NotAllowed as e:
        ref = sso_errors.new_ref()
        members_store.log_event(conn["workspace"], cid, parked["email"], "refused", e.reason,
                                ref)
        return _login_error(e.reason, ref)
    except Exception:
        log.exception("SAML %s: couldn't decide who gets in", cid)
        return _login_error("sso_failed", _log_failure(conn, "sso_failed"))
    members_store.log_event(conn["workspace"], cid, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + _code_for(request, user))


@app.post("/api/auth/sso/exchange")
def sso_exchange(body: CodeIn, request: Request):
    user = sso.redeem_code(body.code, request.session.get(BROWSER_KEY))
    if not user:
        raise HTTPException(401, "That sign-in expired. Please sign in again.")
    token = secrets.token_urlsafe(32)
    sessions_store.create(token, user, SESSION_SECONDS)
    return {"token": token, "user": public_user(user)}


@app.post("/api/auth/logout")
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
              "post_logout_redirect_uri": _public_url(request, "/")}
    if user.get("id_token"):
        params["id_token_hint"] = user["id_token"]
    return end + ("&" if "?" in end else "?") + urllib.parse.urlencode(params)


@app.post("/api/auth/logout-everywhere")
def logout_everywhere(user=Depends(current_user)):
    """Ends every session this person has, on every device, this one included."""
    ended = sessions_store.delete_user(user["username"])
    log.warning("%s signed out everywhere (%d sessions)", user["username"], ended)
    return {"ok": True, "ended": ended}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return public_user(user)


# ---- Sign-in settings: a workspace's domains and its own providers (admin) -------
class DomainIn(BaseModel):
    domain: str = Field(max_length=260)


CONNECTION_ID = r"^[0-9a-f]{12}$"


class ConnectionIn(BaseModel):
    id: str | None = Field(default=None, pattern=CONNECTION_ID)   # from /new-provider
    label: str = Field(min_length=1, max_length=60)
    issuer: str = Field(max_length=500)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str = Field(min_length=1, max_length=2000)
    enabled: bool = True           # the admin screen adds providers off, to test first


class ConnectionPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=60)
    issuer: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, min_length=1, max_length=300)
    client_secret: str | None = Field(default=None, min_length=1, max_length=2000)
    enabled: bool | None = None
    logout_at_idp: bool | None = None       # also sign out at the provider


def _domain_out(row):
    name, value = members_store.txt_record(row) if row["verify_token"] else (None, None)
    return {"domain": row["domain"], "verified": row["verified_at"] is not None,
            "verified_at": row["verified_at"], "txt_name": name, "txt_value": value}


class SamlConnectionIn(BaseModel):
    """The IdP's metadata from ONE of: a URL, the XML, or its three values by hand."""
    id: str | None = Field(default=None, pattern=CONNECTION_ID)   # from /new-provider
    label: str | None = Field(default=None, min_length=1, max_length=60)
    metadata_url: str | None = Field(default=None, max_length=1000)
    metadata_xml: str | None = Field(default=None, max_length=500_000)
    entity_id: str | None = Field(default=None, max_length=500)
    sso_url: str | None = Field(default=None, max_length=1000)
    certificate: str | None = Field(default=None, max_length=20_000)
    email_attribute: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


def _connection_out(request, c):
    """Everything the admin screen shows; never a secret."""
    out = {"id": c["id"], "protocol": c["protocol"], "label": c["label"],
           "issuer": c["issuer"], "enabled": c["enabled"],
           "created_at": c["created_at"], "last_login_at": c["last_login_at"],
           "group_claim": c.get("group_claim"),
           "group_rules": [{"group": r["group_name"], "role": r["role"]}
                           for r in members_store.group_rules(c["id"])],
           # failing: its latest sign-ins all failed (sso_errors.health)
           "health": sso_errors.health(members_store.connection_events(c["id"]))}
    if c["protocol"] == "saml":
        entity_id, acs, metadata_url = saml_sp.sp_urls(_public_url(request, ""), c["id"])
        settings = c["metadata"]
        try:
            cert_expires_at = saml_sp.read_idp_metadata(settings["idp_metadata"])[
                "cert_expires_at"]
        except (KeyError, saml_sp.SamlError):
            cert_expires_at = None
        return {**out, "sp_entity_id": entity_id, "acs_url": acs,
                "sp_metadata_url": metadata_url, "sso_url": settings.get("sso_url"),
                "metadata_url": settings.get("metadata_url"),
                "email_attribute": settings.get("email_attribute"),
                "signs_requests": bool(settings.get("want_authn_requests_signed")),
                "metadata_refreshed_at": c.get("metadata_refreshed_at"),
                "cert_expires_at": cert_expires_at,
                "needs_review": bool(c.get("needs_review")),
                "review_reason": c.get("needs_review")}
    return {**out, "client_id": c["client_id"],
            "redirect_uri": _public_url(request, f"/api/auth/sso/c/{c['id']}/callback"),
            "logout_at_idp": c["logout_at_idp"],
            "supports_logout": bool(c["metadata"].get("end_session_endpoint")),
            "post_logout_redirect_uri": _public_url(request, "/")}


def _saml_settings(body, old=None):
    """(IdP entity ID, settings, metadata status) from the admin's input, checked.
    `old` settings are kept for anything not given. The status (see
    members_store.METADATA_STATUS) is empty if the IdP's metadata wasn't given
    again. Raises HTTPException(400)."""
    if not saml_sp.xmlsec_binary():
        raise HTTPException(400, "SAML isn't available on this server: xmlsec1 isn't "
                                 "installed. Ask whoever runs it.")
    settings = dict(old or {})
    sources = [bool(body.metadata_url), bool(body.metadata_xml),
               bool(body.entity_id or body.sso_url or body.certificate)]
    if sum(sources) > 1:
        raise HTTPException(400, "Give the identity provider's metadata URL, its metadata "
                                 "XML, or its three values by hand: only one of them.")
    try:
        if body.metadata_url:
            url = body.metadata_url.strip()
            xml = sso_setup.fetch_saml_metadata(url).decode("utf-8", "replace")
            settings.update(metadata_url=url)
        elif body.metadata_xml:
            xml = body.metadata_xml.strip()
            settings.pop("metadata_url", None)
        elif sources[2]:
            if not (body.entity_id and body.sso_url and body.certificate):
                raise HTTPException(400, "By hand, give all three: the identity provider's "
                                         "entity ID, sign-in URL and certificate.")
            xml = saml_sp.manual_idp_metadata(body.entity_id.strip(), body.sso_url.strip(),
                                              body.certificate)
            settings.pop("metadata_url", None)
        elif old is None:
            raise HTTPException(400, "Give the identity provider's metadata: a URL, the XML, "
                                     "or its entity ID, sign-in URL and certificate.")
        else:
            xml = settings["idp_metadata"]
        idp = _read_idp(xml)
    except sso_setup.SetupError as e:
        raise _setup_error(e)
    except (saml_sp.SamlError, url_guard.UnsafeURL) as e:
        raise HTTPException(400, f"SAML metadata: {e}")
    settings.update(idp_metadata=xml, sso_url=idp["sso_url"],
                    want_authn_requests_signed=idp["want_authn_requests_signed"])
    if body.email_attribute is not None:
        settings["email_attribute"] = body.email_attribute.strip() or None
    status = {}
    if any(sources):           # new metadata, checked by the admin: nothing to review
        fetched = bool(body.metadata_url)
        status = {"metadata_refreshed_at": time.time() if fetched else None,
                  "metadata_due_at": _next_refresh(idp) if fetched else None,
                  "needs_review": None, "metadata_error": None}
    return idp["entity_id"], settings, status


def _read_idp(xml):
    """saml_sp.read_idp_metadata, and the browser is only sent to https."""
    idp = saml_sp.read_idp_metadata(xml)
    url_guard.require_https(idp["sso_url"])
    return idp


def _setup_error(e):
    return HTTPException(400, str(e))


def _connection_id(wanted):
    """The ID a new provider gets: the one /new-provider showed the admin, if free."""
    if not wanted:
        return members_store.new_connection_id()
    if members_store.get_connection(wanted):
        raise HTTPException(409, "That provider was already added. Reload the page.")
    return wanted


@app.get("/api/admin/sign-in/new-provider")
def new_provider(request: Request, user=Depends(require_admin)):
    """An ID for a provider about to be added, with the addresses its IdP needs:
    an IdP admin usually has to enter these BEFORE they get the client ID or
    metadata to give us. Nothing is saved until the provider is added."""
    cid = members_store.new_connection_id()
    entity_id, acs, metadata_url = saml_sp.sp_urls(_public_url(request, ""), cid)
    return {"id": cid,
            "redirect_uri": _public_url(request, f"/api/auth/sso/c/{cid}/callback"),
            "sp_entity_id": entity_id, "acs_url": acs, "sp_metadata_url": metadata_url}


@app.get("/api/admin/sign-in")
def sign_in_settings(request: Request, user=Depends(require_admin)):
    ws = ws_of(user)
    connections = members_store.list_connections(ws)
    labels = {c["id"]: c["label"] for c in connections}
    return {"domains": [_domain_out(d) for d in members_store.list_domains(ws)],
            "connections": [_connection_out(request, c) for c in connections],
            "require_sso": members_store.requires_sso(ws),
            "role_source": members_store.role_source(ws),
            "group_admin_days": members_store.group_admin_days(ws),
            "password_login": auth.password_login_on(),
            "events": [{**e, "connection": _event_method(labels, e["connection_id"]),
                        **_explained(e["reason"])}
                       for e in members_store.recent_events(ws)]}


def _event_method(labels, method):
    """What someone signed in with: a workspace provider's name, "password", or a
    built-in provider (Google, ...). None for a provider deleted since."""
    if method in labels:
        return labels[method]
    if method == "password":
        return "password"
    if method and not re.fullmatch(CONNECTION_ID, method):
        return sso.label(method)
    return None


class PolicyIn(BaseModel):
    require_sso: bool


@app.put("/api/admin/sign-in/policy")
def set_policy(body: PolicyIn, user=Depends(require_admin)):
    """Require single sign-on: nobody but the workspace's admins may use a password."""
    ws = ws_of(user)
    if body.require_sso:
        if not any(d["verified_at"] for d in members_store.list_domains(ws)):
            raise HTTPException(400, "Verify a domain first, or nobody could sign in.")
        if not (members_store.list_connections(ws, enabled_only=True) or sso.configured()):
            raise HTTPException(400, "Turn on a sign-in provider first, or nobody could sign in.")
    members_store.set_requires_sso(ws, body.require_sso)
    log.warning("%s turned %s single sign-on for %s", user["username"],
                "on required" if body.require_sso else "off required", ws)
    return {"require_sso": body.require_sso}


@app.post("/api/admin/sign-in/domains")
def add_domain(body: DomainIn, user=Depends(require_admin)):
    try:
        domain = sso_setup.normalize_domain(body.domain)
    except sso_setup.SetupError as e:
        raise _setup_error(e)
    return _domain_out(members_store.claim_domain(ws_of(user), domain))


@app.post("/api/admin/sign-in/domains/{domain}/verify")
def verify_domain(domain: str, request: Request, user=Depends(require_admin)):
    rate_limit(request, "dns_check")
    ws = ws_of(user)
    row = members_store.get_domain(ws, domain)
    if not row:
        raise HTTPException(404, "Add the domain first.")
    if row["verified_at"] is None:
        name, value = members_store.txt_record(row)
        if value not in sso_setup.txt_values(name):
            raise HTTPException(400, f"No TXT record {name} with the value {value} yet. "
                                     "DNS changes can take a few minutes to appear.")
        try:
            members_store.mark_verified(ws, row["domain"])
        except ValueError as e:
            raise HTTPException(409, str(e))
        log.warning("Workspace %s verified the domain %s", ws, row["domain"])
    return _domain_out(members_store.get_domain(ws, domain))


@app.delete("/api/admin/sign-in/domains/{domain}")
def delete_domain(domain: str, user=Depends(require_admin)):
    domain = domain.strip().lower()
    row = members_store.get_domain(ws_of(user), domain)
    if row and row["verified_at"]:
        _keep_a_way_in(user, ws_of(user), domain=domain)
    if not members_store.remove_domain(domain, workspace=ws_of(user)):
        raise HTTPException(404, "This workspace doesn't have that domain.")
    return {"ok": True}


@app.post("/api/admin/sign-in/connections")
def add_connection(body: ConnectionIn, request: Request, user=Depends(require_admin)):
    try:
        issuer = sso_setup.normalize_issuer(body.issuer)
        metadata = sso_setup.oidc_metadata(issuer)
    except sso_setup.SetupError as e:
        raise _setup_error(e)
    conn = members_store.create_connection(ws_of(user), "oidc", body.label.strip(), issuer,
                                           body.client_id.strip(),
                                           {"client_secret": body.client_secret},
                                           metadata, user["username"],
                                           cid=_connection_id(body.id))
    if not body.enabled:
        conn = members_store.update_connection(conn["workspace"], conn["id"], enabled=False)
    return _connection_out(request, conn)


@app.patch("/api/admin/sign-in/connections/{cid}")
def edit_connection(cid: str, body: ConnectionPatch, request: Request,
                    user=Depends(require_admin)):
    ws = ws_of(user)
    conn = members_store.get_connection(cid)
    if not conn or conn["workspace"] != ws:
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    if conn["protocol"] != "oidc":
        raise HTTPException(400, "That's a SAML provider: change it under saml-connections.")
    fields = body.model_dump(exclude_none=True)
    if "client_secret" in fields:
        fields["keys"] = {"client_secret": fields.pop("client_secret")}
    for key in ("label", "client_id"):
        if key in fields:
            fields[key] = fields[key].strip()
    if "issuer" in fields:
        if "keys" not in fields:
            # Else the saved secret would go to whatever the new issuer's token
            # endpoint is.
            raise HTTPException(400, "Enter the client secret again when you change the issuer.")
        try:
            fields["issuer"] = sso_setup.normalize_issuer(fields["issuer"])
            fields["metadata"] = sso_setup.oidc_metadata(fields["issuer"])
        except sso_setup.SetupError as e:
            raise _setup_error(e)
    if fields.get("enabled") is False:
        _keep_a_way_in(user, ws, without=cid)
    if fields:
        conn = members_store.update_connection(ws, cid, **fields)
    return _connection_out(request, conn)


@app.post("/api/admin/sign-in/saml-connections")
def add_saml_connection(body: SamlConnectionIn, request: Request, user=Depends(require_admin)):
    if not (body.label or "").strip():
        raise HTTPException(400, "Give the sign-in button a name.")
    entity_id, settings, status = _saml_settings(body)
    cid = _connection_id(body.id)
    sp_key, sp_cert = saml_sp.new_keypair(f"Knowledge assistant {cid}")
    conn = members_store.create_connection(ws_of(user), "saml", body.label.strip(), entity_id,
                                           None, {"sp_key": sp_key, "sp_cert": sp_cert},
                                           settings, user["username"], cid=cid)
    conn = members_store.update_connection(conn["workspace"], cid, **status,
                                           **({"enabled": False} if body.enabled is False
                                              else {}))
    return _connection_out(request, conn)


@app.patch("/api/admin/sign-in/saml-connections/{cid}")
def edit_saml_connection(cid: str, body: SamlConnectionIn, request: Request,
                         user=Depends(require_admin)):
    ws = ws_of(user)
    conn = members_store.get_connection(cid)
    if not conn or conn["workspace"] != ws or conn["protocol"] != "saml":
        raise HTTPException(404, "This workspace has no such SAML sign-in provider.")
    fields = {}
    if body.label is not None:
        fields["label"] = body.label.strip()
    if body.enabled is not None:
        fields["enabled"] = body.enabled
        if body.enabled is False:
            _keep_a_way_in(user, ws, without=cid)
    if any(v is not None for v in (body.metadata_url, body.metadata_xml, body.entity_id,
                                   body.sso_url, body.certificate, body.email_attribute)):
        fields["issuer"], fields["metadata"], status = _saml_settings(body, old=conn["metadata"])
        fields.update(status)
    if fields:
        conn = members_store.update_connection(ws, cid, **fields)
    return _connection_out(request, conn)


@app.post("/api/admin/sign-in/connections/{cid}/test")
def test_connection(cid: str, user=Depends(require_admin)):
    """Start a "Test sign-in": open `url` in a popup, then poll /tests/{ticket}."""
    ws = ws_of(user)
    conn = members_store.get_connection(cid)
    if not conn or conn["workspace"] != ws:
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    if conn["protocol"] == "saml" and not saml_sp.xmlsec_binary():
        raise HTTPException(400, "SAML isn't available on this server: xmlsec1 isn't installed.")
    ticket = _new_sso_test(ws, cid)
    path = (f"/api/auth/saml/{cid}/start" if conn["protocol"] == "saml"
            else f"/api/auth/sso/c/{cid}/start")
    return {"ticket": ticket, "url": f"{path}?" + urllib.parse.urlencode({"test": ticket})}


@app.get("/api/admin/sign-in/tests/{ticket}")
def test_result(ticket: str, user=Depends(require_admin)):
    with _sso_tests_lock:
        test = _sso_tests.get(ticket)
    if not test or test["workspace"] != ws_of(user) or test["expires"] < time.time():
        raise HTTPException(404, "That test has expired. Start it again.")
    return {"done": test["result"] is not None, "result": test["result"]}


class GroupRule(BaseModel):
    group: str = Field(min_length=1, max_length=256)
    role: str = Field(pattern=r"^(public|hr|admin)$")


class GroupRulesIn(BaseModel):
    group_claim: str | None = Field(default=None, max_length=300)
    rules: list[GroupRule] = Field(default_factory=list, max_length=200)
    make_role_source: bool = False   # take over setting roles from another provider


def _no_admin_left(e):
    who = ", ".join(e.admins)
    return HTTPException(400, f"This would leave the workspace with no admin ({who} "
                              f"{'is' if len(e.admins) == 1 else 'are'} admin through "
                              "groups). Make someone else admin on the Members tab first.")


@app.put("/api/admin/sign-in/connections/{cid}/roles")
def set_group_roles(cid: str, body: GroupRulesIn, request: Request,
                    user=Depends(require_admin)):
    """Roles from the groups a provider sends: they apply at each person's next
    sign-in. Rules make it the workspace's one role source (members_store.py)."""
    ws = ws_of(user)
    claim = (body.group_claim or "").strip() or None
    try:
        found = members_store.set_group_rules(ws, cid, claim,
                                              [(r.group, r.role) for r in body.rules],
                                              make_role_source=body.make_role_source)
    except members_store.RoleSourceTaken as e:
        source = members_store.get_connection(e.source)
        name = source["label"] if source else "another provider"
        raise HTTPException(409, f"Roles come from {name}. A workspace takes roles from one "
                                 "provider: switch it to this one to save these rules.")
    except members_store.NoAdminLeft as e:
        raise _no_admin_left(e)
    if not found:
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    log.warning("%s set the group rules of %s; the role source of %s is %s", user["username"],
                cid, ws, members_store.role_source(ws))
    return _connection_out(request, members_store.get_connection(cid))


class GroupAdminDaysIn(BaseModel):
    days: int = Field(ge=0, le=3650)


@app.put("/api/admin/sign-in/group-admin-days")
def set_group_admin_days(body: GroupAdminDaysIn, user=Depends(require_admin)):
    """How long admin from groups lasts without a sign-in through the role source (0: forever)."""
    members_store.set_group_admin_days(ws_of(user), body.days)
    return {"group_admin_days": body.days}


def _keep_a_way_in(user, ws, without=None, domain=None):
    """Refuse a change that would lock out the admin making it (they signed in
    through this provider, or with an email on this domain and have no password),
    or leave "Require single sign-on" on with no way to sign in."""
    if without and user.get("connection") == without:
        raise HTTPException(400, "You're signed in through this provider, so this would sign "
                                 "you out for good. Sign in another way first (for example, "
                                 "an admin password).")
    if domain and user.get("provider") and user["username"].endswith("@" + domain):
        raise HTTPException(400, f"You signed in with an @{domain} address, so this would sign "
                                 "you out for good. Sign in another way first.")
    if not members_store.requires_sso(ws):
        return
    providers = [c for c in members_store.list_connections(ws, enabled_only=True)
                 if c["id"] != without]
    domains = [d for d in members_store.list_domains(ws)
               if d["verified_at"] and d["domain"] != domain]
    if not domains or not (providers or sso.configured()):
        raise HTTPException(400, "Single sign-on is required, and this would leave no way to "
                                 "sign in with it. Turn that off first (Sign-in rules).")


@app.delete("/api/admin/sign-in/connections/{cid}")
def delete_connection(cid: str, user=Depends(require_admin)):
    _keep_a_way_in(user, ws_of(user), without=cid)
    try:
        found = members_store.delete_connection(ws_of(user), cid)
    except members_store.NoAdminLeft as e:
        raise _no_admin_left(e)
    if not found:
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    return {"ok": True}


# ---- Chat --------------------------------------------------------------------
class ChatIn(BaseModel):
    question: str


OUTLINE_ROLES = {"public": ("public", "hr"), "hr": ("hr",)}   # OUTLINE_MIN_ROLE -> roles


def outline_on(ws, role):
    """The Outline wiki is configured server-wide (.env) for the default workspace.
    It is searched with ONE server-wide token, so the wiki has no per-user
    permissions: OUTLINE_MIN_ROLE says which roles get it at all (public, the
    default, = everyone; hr = HR / Admin only). An unknown value turns it off."""
    if ws != workspaces.DEFAULT or not os.getenv("OUTLINE_API_TOKEN"):
        return False
    wanted = os.getenv("OUTLINE_MIN_ROLE", "public").strip().lower() or "public"
    if wanted not in OUTLINE_ROLES:
        log.warning("OUTLINE_MIN_ROLE=%r isn't public or hr; the Outline wiki is off", wanted)
        return False
    return role in OUTLINE_ROLES[wanted]


def get_agent(ws, role):
    with _agents_lock:
        if (ws, role) not in _agents:
            _agents[(ws, role)] = build_agent(role, workspaces.collection(ws),
                                              outline_on(ws, role))
        return _agents[(ws, role)]


@app.post("/api/chat")
def chat(body: ChatIn, user=Depends(current_user), role=Depends(current_role)):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    user_limit(user, "chat")
    ws = ws_of(user)
    with _gate:
        if not _gate.wait_for(lambda: not _gate_of(ws)["indexing"], timeout=120):
            raise HTTPException(503, "Documents are being re-indexed. Try again in a moment.")
        _gate_of(ws)["active"] += 1
    try:
        answer, trace, sources = get_agent(ws, role)(question, with_sources=True)
    finally:
        with _gate:
            _gate_of(ws)["active"] -= 1
            _gate.notify_all()
    return {"answer": answer, "sources": sources, "steps": _steps(trace)}


def _steps(trace):
    return [{"tool": t["tool"], "input": t["input"],
             "output": t["output"][:STEP_PREVIEW_CHARS]
             + (" …(trimmed)" if len(t["output"]) > STEP_PREVIEW_CHARS else "")}
            for t in trace]


def _enter_chat(ws):
    """Count a question in, once its workspace's index isn't being swapped."""
    with _gate:
        if not _gate.wait_for(lambda: not _gate_of(ws)["indexing"], timeout=120):
            raise HTTPException(503, "Documents are being re-indexed. Try again in a moment.")
        _gate_of(ws)["active"] += 1


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn, user=Depends(current_user), role=Depends(current_role)):
    """Same as /api/chat, but streams what's happening as Server-Sent Events:
         status      {"text"}                       e.g. "Checking your question"
         step_start  {"id", "tool", "label"}        e.g. "Searching company docs"
         step_end    {"id", "tool", "result"}       e.g. "found 2 relevant documents"
         answer      {"text"}                       the answer so far (PII already masked)
         done        {"answer", "sources", "steps"} the final, checked answer
         error       {"message"}
    If the browser goes away mid-answer, the agent is told to stop."""
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    user_limit(user, "chat")
    ws = ws_of(user)
    await run_in_threadpool(_enter_chat, ws)      # may wait: keep the event loop free

    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    stop = threading.Event()                      # set when the client disconnects

    def put(event):                               # called from the worker thread
        try:
            loop.call_soon_threadsafe(events.put_nowait, event)
        except RuntimeError:                      # the event loop has shut down
            pass

    def work():
        try:
            answer, trace, sources = get_agent(ws, role)(question, with_sources=True,
                                                         on_event=put, cancel=stop)
            put({"type": "done", "answer": answer, "sources": sources,
                 "steps": _steps(trace)})
        except Cancelled:                          # nobody is listening any more
            pass
        except Exception as e:                     # show it in the chat, keep serving
            # Not the question itself: it's tenant data (its length helps spot limits).
            log.exception("Chat: answering failed in workspace %s (role %s, %d characters)",
                          ws, role, len(question))
            put({"type": "error", "message": f"Something went wrong: {type(e).__name__}"})
        finally:
            with _gate:
                _gate_of(ws)["active"] -= 1
                _gate.notify_all()
            put(None)

    threading.Thread(target=work, daemon=True).start()

    async def sse():
        try:
            while (event := await events.get()) is not None:
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # Ends normally, or is cancelled here when the client disconnects:
            # either way nobody needs the rest of the work.
            stop.set()

    # X-Accel-Buffering: no -> nginx passes each event through immediately.
    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- Re-indexing -------------------------------------------------------------
def _run_index(ws):
    """Background worker: re-index one workspace until no more changes are pending."""
    def progress(step, total, message):
        with _index_lock:
            _indexes[ws].update(step=step, total=total, message=message)

    while True:
        with _index_lock:
            _indexes[ws].update(status="indexing", step=0, message="Starting",
                                pending=False, error=None)
        with _gate:
            _gate_of(ws)["indexing"] = True
            _gate.wait_for(lambda: _gate_of(ws)["active"] == 0, timeout=300)
        summary, error = None, None
        try:
            with _ingest_lock, contextlib.redirect_stdout(io.StringIO()):
                summary = ingest.main(progress=progress,
                                      docs_folder=workspaces.docs_root(ws),
                                      collection=workspaces.collection(ws),
                                      connector_access=integrations_store
                                      .chunk_access_levels(ws))
            with _agents_lock:
                for key in [k for k in _agents if k[0] == ws]:
                    del _agents[key]   # next question loads the new index
        except Exception as e:        # keep the server alive; show it in the UI
            log.exception("Index: re-indexing workspace %s failed", ws)
            error = f"{type(e).__name__}: {e}"
        finally:
            with _gate:
                _gate_of(ws)["indexing"] = False
                _gate.notify_all()
        with _index_lock:
            if _indexes[ws]["pending"]:
                continue               # more changes arrived meanwhile
            _indexes[ws].update(status="error" if error else "idle", summary=summary,
                                error=error, finished_at=time.time(),
                                message=error or "Up to date")
            return


def _schedule_index(ws):
    with _index_lock:
        state = _indexes.setdefault(ws, _new_index_state())
        if state["status"] == "indexing":
            state["pending"] = True
            return
        state.update(status="indexing", step=0, message="Queued")
    threading.Thread(target=_run_index, args=(ws,), daemon=True).start()


@app.post("/api/reindex")
def reindex(user=Depends(require_hr)):
    """Manual re-index (changes already re-index automatically)."""
    user_limit(user, "reindex")
    _schedule_index(ws_of(user))
    return index_status(user)


@app.get("/api/index/status")
def index_status(user=Depends(current_user)):
    with _index_lock:
        state = _indexes.get(ws_of(user)) or _new_index_state()
        return {k: state[k] for k in ("status", "step", "total", "message", "pending",
                                      "summary", "error", "finished_at")}


# ---- Documents ---------------------------------------------------------------
class NewDocIn(BaseModel):
    name: str
    access: str
    content: str


class SaveDocIn(BaseModel):
    content: str


def _existing_path(ws, access, name):
    path = docs_store.doc_path(access, name, workspaces.docs_root(ws))
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Document not found.")
    return path


@app.get("/api/docs")
def list_docs(user=Depends(require_hr)):
    root = workspaces.docs_root(ws_of(user))
    docs = [{k: d[k] for k in ("access", "name", "size")} for d in docs_store.list_docs(root)]
    return {"docs": docs, "upload_limits": _upload_limits()}


def _upload_limits():
    """For the upload screen, so it can say so before sending a file that's too big."""
    return {"file_mb": _env_int("UPLOAD_MAX_FILE_MB", UPLOAD_MAX_FILE_MB),
            "request_mb": _env_int("UPLOAD_MAX_REQUEST_MB", UPLOAD_MAX_REQUEST_MB)}


@app.get("/api/docs/{access}/{name}")
def read_doc(access: str, name: str, user=Depends(require_hr)):
    path = _existing_path(ws_of(user), access, name)
    if name.lower().endswith(".pdf"):
        return FileResponse(path, media_type="application/pdf", filename=name)
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"access": access, "name": name, "content": f.read()}


@app.post("/api/docs")
def create_doc(body: NewDocIn, user=Depends(require_hr)):
    ws = ws_of(user)
    root = workspaces.docs_root(ws)
    name = docs_store.safe_name(body.name)
    if body.access not in docs_store.ACCESS_LEVELS:
        raise HTTPException(400, "Access must be public or restricted.")
    if not name or not body.content.strip():
        raise HTTPException(400, "Please give a file name and some content.")
    if os.path.exists(docs_store.doc_path(body.access, name, root)):
        raise HTTPException(409, f"{name} already exists. Edit it instead.")
    docs_store.save(body.access, name, body.content.encode("utf-8"), root)
    _schedule_index(ws)
    return {"access": body.access, "name": name}


@app.put("/api/docs/{access}/{name}")
def save_doc(access: str, name: str, body: SaveDocIn, user=Depends(require_hr)):
    ws = ws_of(user)
    _existing_path(ws, access, name)
    if not name.lower().endswith(".txt"):
        raise HTTPException(400, "Only .txt documents can be edited here.")
    docs_store.save(access, name, body.content.encode("utf-8"), workspaces.docs_root(ws))
    _schedule_index(ws)
    return {"ok": True}


@app.delete("/api/docs/{access}/{name}")
def delete_doc(access: str, name: str, user=Depends(require_hr)):
    ws = ws_of(user)
    os.remove(_existing_path(ws, access, name))
    _schedule_index(ws)
    return {"ok": True}


@app.post("/api/docs/upload")
def upload_docs(files: list[UploadFile] = File(...), access: str = Form("public"),
                user=Depends(require_hr)):
    """The files arrive already on disk (Starlette spools big ones to temporary
    files), with the whole request capped by _BodyLimit. Each is checked (size,
    and that it is what its name says) before any is saved."""
    user_limit(user, "upload")
    ws = ws_of(user)
    if access not in docs_store.ACCESS_LEVELS:
        raise HTTPException(400, "Access must be public or restricted.")
    max_mb = _env_int("UPLOAD_MAX_FILE_MB", UPLOAD_MAX_FILE_MB)
    picked = []
    for f in files:
        if not f.filename.lower().endswith(docs_store.EXTENSIONS):
            continue
        name = docs_store.safe_name(f.filename)
        if not name:
            continue
        f.file.seek(0, os.SEEK_END)
        size = f.file.tell()
        f.file.seek(0)
        if size > max_mb * 1024 * 1024:
            raise HTTPException(413, f"{f.filename} is {size / 1024 / 1024:.1f} MB. Files can be "
                                     f"up to {max_mb} MB each. Nothing was uploaded.")
        head = f.file.read(8192)
        f.file.seek(0)
        if not docs_store.content_matches(name, head):
            kind = "PDF" if name.endswith(".pdf") else "plain text"
            raise HTTPException(415, f"{f.filename} isn't a {kind} file, though its name says "
                                     "so. Nothing was uploaded.")
        picked.append((name, f))
    if not picked:
        raise HTTPException(400, "Please choose at least one .txt or .pdf file.")
    for name, f in picked:
        docs_store.save_file(access, name, f.file, workspaces.docs_root(ws))
    _schedule_index(ws)
    return {"saved": [name for name, _ in picked]}


# ---- Integrations ------------------------------------------------------------
# Every connector module has the same shape: PROVIDER, configured(), auth_url(),
# finish_connect(), sync(), disconnect(). The endpoints below work for all of them:
#   POST   /api/integrations/{provider}/connect   -> {url} of the provider's consent screen
#   GET    /api/integrations/{provider}/callback  the provider redirects the browser back here
#   POST   /api/integrations/{provider}/sync      sync now
#   DELETE /api/integrations/{provider}           disconnect + delete the synced copies
CONNECTORS = {c.PROVIDER: c for c in (google_drive, notion)}
LABELS = {"google_drive": "Google Drive", "notion": "Notion"}
SETUP_ENV = {"google_drive": "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET",
             "notion": "NOTION_CLIENT_ID / NOTION_CLIENT_SECRET"}


def _connector(provider):
    c = CONNECTORS.get(provider)
    if not c:
        raise HTTPException(404, "Unknown integration.")
    return c


def _ready_to_sync(ws, provider):
    """Drive syncs only what the admin picked; Notion's picking happens on its own
    consent screen, so anything shared with us is wanted."""
    if provider != google_drive.PROVIDER:
        return True
    row = integrations_store.get(ws, provider) or {}
    return bool((row.get("settings") or {}).get("folders"))


def _sync(ws, provider):
    """Sync one workspace's connector (background); re-index if anything changed."""
    key = (ws, provider)
    with _sync_lock:
        if key in _syncing:
            return
        _syncing.add(key)
    try:
        integrations_store.update(ws, provider, status="syncing", last_error=None)
        count, changed = CONNECTORS[provider].sync(ws)
        integrations_store.update(ws, provider, status="connected",
                                  last_sync_at=time.time(), doc_count=count)
        if changed:
            _schedule_index(ws)
    except Exception as e:
        log.exception("Sync: %s failed for workspace %s", provider, ws)
        integrations_store.update(ws, provider, status="error",
                                  last_error=f"{type(e).__name__}: {e}"[:500])
    finally:
        with _sync_lock:
            _syncing.discard(key)


def _start_sync(ws, provider):
    threading.Thread(target=_sync, args=(ws, provider), daemon=True).start()


def _sync_scheduler():
    """Re-sync every connected integration every DRIVE_SYNC_EVERY seconds."""
    while True:
        time.sleep(DRIVE_SYNC_EVERY)
        for provider, c in CONNECTORS.items():
            if not c.configured():
                continue
            for row in integrations_store.all_connected(provider):
                if row["status"] in ("connected", "error"):
                    _start_sync(row["workspace"], provider)


def _public_url(request: Request, path):
    """Absolute URL of one of our pages, for providers to redirect back to."""
    base = os.getenv("PUBLIC_URL") or str(request.base_url)
    return base.rstrip("/") + path


def _redirect_uri(request: Request, provider):
    return _public_url(request, f"/api/integrations/{provider}/callback")


def _integration_info(ws, provider):
    row = integrations_store.get(ws, provider)
    if row:
        status = row["status"]
    else:
        status = "disconnected" if CONNECTORS[provider].configured() else "not_configured"
    info = {"id": provider, "name": LABELS[provider], "status": status,
            "account": row and row["account"], "connected_by": row and row["connected_by"],
            "last_sync_at": row and row["last_sync_at"],
            "doc_count": (row and row["doc_count"]) or 0,
            "last_error": row and row["last_error"],
            "access": integrations_store.access_of(row)}
    if provider == google_drive.PROVIDER:
        info["folders"] = ((row and row.get("settings")) or {}).get("folders", [])
    return info


@app.get("/api/integrations")
def list_integrations(user=Depends(current_user), role=Depends(current_role)):
    ws = ws_of(user)
    return {"workspace": ws, "integrations": [
        {"id": "uploads", "name": "Uploaded files", "status": "connected",
         "doc_count": len(docs_store.list_docs(workspaces.docs_root(ws)))},
        _integration_info(ws, "google_drive"),
        _integration_info(ws, "notion"),
        {"id": "outline", "name": "Outline wiki",
         "status": "connected" if outline_on(ws, role) else "unavailable"},
    ]}


@app.post("/api/integrations/{provider}/connect")
def integration_connect(provider: str, request: Request, user=Depends(require_hr)):
    c = _connector(provider)
    if not c.configured():
        raise HTTPException(400, f"{LABELS[provider]} isn't set up on this server yet "
                                 f"({SETUP_ENV[provider]} missing).")
    now = time.time()
    for s in [k for k, v in _oauth_states.items() if v["expires"] < now]:
        del _oauth_states[s]
    state = secrets.token_urlsafe(24)
    redirect_uri = _redirect_uri(request, provider)
    _oauth_states[state] = {"workspace": ws_of(user), "user": user["username"],
                            "provider": provider, "expires": now + OAUTH_STATE_SECONDS,
                            "redirect_uri": redirect_uri}
    return {"url": c.auth_url(redirect_uri, state)}


@app.get("/api/integrations/{provider}/callback")
def integration_callback(provider: str, state: str = "", code: str = "", error: str = ""):
    """The provider sends the browser back here. The one-time `state` ties it to the
    workspace and user who clicked Connect (a redirect carries no session header)."""
    c = _connector(provider)
    pending = _oauth_states.pop(state, None)
    if not pending or pending["expires"] < time.time() or pending.get("provider") != provider:
        return RedirectResponse("/#integrations?error=expired")
    if error or not code:
        return RedirectResponse("/#integrations?error=" + (error or "cancelled"))
    ws = pending["workspace"]
    try:
        c.finish_connect(ws, code, pending["redirect_uri"], pending["user"])
    except Exception:
        log.exception("Integration %s: couldn't finish connecting workspace %s", provider, ws)
        return RedirectResponse("/#integrations?error=connect_failed")
    if _ready_to_sync(ws, provider):
        _start_sync(ws, provider)
        return RedirectResponse(f"/#integrations?connected={provider}")
    # Drive, new connection: nothing is synced until the admin picks folders.
    return RedirectResponse(f"/#integrations?connected={provider}&choose=1")


@app.post("/api/integrations/{provider}/sync")
def integration_sync(provider: str, user=Depends(require_hr)):
    _connector(provider)
    user_limit(user, "sync")
    ws = ws_of(user)
    if not integrations_store.get(ws, provider):
        raise HTTPException(400, f"{LABELS[provider]} isn't connected.")
    _start_sync(ws, provider)
    return {"ok": True}


@app.delete("/api/integrations/{provider}")
def integration_disconnect(provider: str, user=Depends(require_hr)):
    c = _connector(provider)
    ws = ws_of(user)
    c.disconnect(ws)
    _schedule_index(ws)            # drop its files from the search index
    return {"ok": True}


class AccessIn(BaseModel):
    access: str = Field(pattern=r"^(restricted|everyone)$")


@app.put("/api/integrations/{provider}/access")
def integration_access(provider: str, body: AccessIn, user=Depends(require_hr)):
    """Who may ask about this connection's files: HR and admins only
    ("restricted", the default) or everyone in the workspace. The files are
    re-indexed so their chunks carry the new level."""
    _connector(provider)
    ws = ws_of(user)
    row = integrations_store.get(ws, provider)
    if not row:
        raise HTTPException(400, f"{LABELS[provider]} isn't connected.")
    if integrations_store.access_of(row) != body.access:
        integrations_store.update(ws, provider,
                                  settings=dict(row.get("settings") or {}, access=body.access))
        log.warning("%s set who can ask about %s files in %s to %s", user["username"],
                    LABELS[provider], ws, body.access)
        _schedule_index(ws)
    return {"access": body.access}


class FoldersIn(BaseModel):
    folders: list[dict]


@app.get("/api/integrations/google_drive/folders")
def drive_folders(parent: str = "", user=Depends(require_hr)):
    """Browse what can be picked: top level (Shared Drives + My Drive root) or a
    folder — its sub-folders and its files."""
    ws = ws_of(user)
    if not integrations_store.get(ws, google_drive.PROVIDER):
        raise HTTPException(400, "Google Drive isn't connected.")
    try:
        return google_drive.list_folders(ws, parent or None)
    except Exception as e:
        log.exception("Google Drive: couldn't list folders for workspace %s", ws)
        raise HTTPException(502, f"Couldn't list Google Drive folders: {e}")


@app.put("/api/integrations/google_drive/folders")
def drive_set_folders(body: FoldersIn, user=Depends(require_hr)):
    """Save which folders / Shared Drives / single files to sync, then sync
    (anything no longer picked is dropped from the index)."""
    ws = ws_of(user)
    drive = integrations_store.get(ws, google_drive.PROVIDER)
    if not drive:
        raise HTTPException(400, "Google Drive isn't connected.")
    kinds = ("drive", "folder", "file")
    folders = [{"id": str(f["id"]), "name": str(f.get("name", ""))[:200],
                "kind": f.get("kind") if f.get("kind") in kinds else "folder",
                **({"mimeType": str(f["mimeType"])[:100]} if f.get("mimeType") else {})}
               for f in body.folders if f.get("id")][:200]
    settings = dict(drive.get("settings") or {}, folders=folders)
    integrations_store.update(ws, google_drive.PROVIDER, settings=settings)
    _start_sync(ws, google_drive.PROVIDER)
    return {"folders": folders}


@app.get("/api/knowledge")
def knowledge(user=Depends(current_user), role=Depends(current_role)):
    """Everything this workspace's assistant can search: uploaded files plus files
    synced by connectors, with how much of each is in the search index.
    Public users only see public items (like search itself)."""
    ws = ws_of(user)
    root = workspaces.docs_root(ws)

    # Chunks per file in the index (by file path), straight from Chroma.
    chunks = {}
    try:
        import chromadb
        col = chromadb.PersistentClient(path=ingest.DB_FOLDER).get_collection(
            workspaces.collection(ws))
        for meta in col.get(include=["metadatas"])["metadatas"]:
            key = os.path.normcase(os.path.abspath(meta.get("source", "")))
            chunks[key] = chunks.get(key, 0) + 1
    except Exception as e:                     # no index yet: everything shows 0
        if type(e).__name__ != "NotFoundError":
            log.exception("Knowledge: couldn't read the search index of workspace %s", ws)

    def indexed(path):
        return chunks.get(os.path.normcase(os.path.abspath(path)), 0)

    items = []
    for d in docs_store.list_docs(root):
        if role != "hr" and d["access"] == "restricted":
            continue
        items.append({"title": d["name"], "source": "Uploaded", "access": d["access"],
                      "url": None, "modified": os.path.getmtime(d["path"]),
                      "size": d["size"], "chunks": indexed(d["path"])})

    levels = integrations_store.chunk_access_levels(ws)
    connectors_dir = os.path.join(root, "connectors")
    for provider in sorted(os.listdir(connectors_dir)) if os.path.isdir(connectors_dir) else []:
        folder = os.path.join(connectors_dir, provider)
        try:
            with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        access = levels.get(provider, "restricted")       # as ingest.py tags them
        if role != "hr" and access == "restricted":
            continue
        for name, info in manifest.items():
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            items.append({"title": info.get("title") or name,
                          "source": info.get("provider") or provider, "access": access,
                          "url": info.get("url"), "modified": info.get("modified"),
                          "size": os.path.getsize(path), "chunks": indexed(path)})

    items.sort(key=lambda i: (i["source"] != "Uploaded", i["title"].lower()))
    return {"workspace": ws, "items": items,
            "outline": outline_on(ws, role)}         # searched live, so not listed here


# ---- Members (admin) --------------------------------------------------------------
class MemberPatch(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(public|hr|admin)$")  # null: from groups
    disabled: bool | None = None


def _member_out(m, admin_days=None):
    source = ("override" if m["override_role"] else
              "groups" if m["group_role"] else "default")
    if admin_days is None:
        admin_days = members_store.group_admin_days(m["workspace"])
    return {"username": m["username"], "name": m["name"], "role": m["role"],
            "role_source": source, "override_role": m["override_role"],
            "group_role": m["group_role"], "groups": m["groups"],
            "groups_updated_at": m["groups_updated_at"],
            # admin from groups older than the limit: kept only as the last admin
            "admin_kept": members_store.group_admin_stale(m, admin_days),
            "disabled": m["disabled"],
            "has_password": m["has_password"], "last_login_at": m["last_login_at"],
            "created_at": m["created_at"]}


def _workspace_member(user, username):
    member = members_store.get(username)
    if not member or member["workspace"] != ws_of(user):
        raise HTTPException(404, "This workspace has no such member.")
    if member["username"] == user["username"]:
        # Also keeps at least one admin: the one asking.
        raise HTTPException(400, "You can't change your own access. Ask another admin.")
    return member


@app.get("/api/admin/members")
def list_members(user=Depends(require_admin)):
    ws = ws_of(user)
    members_store.expire_group_admins(ws)            # so the list shows who's admin now
    days = members_store.group_admin_days(ws)
    return {"members": [_member_out(m, days) for m in members_store.list_members(ws)]}


@app.patch("/api/admin/members/{username}")
def edit_member(username: str, body: MemberPatch, user=Depends(require_admin)):
    member = _workspace_member(user, username)
    if "role" in body.model_fields_set:
        members_store.set_role(member["username"], body.role, member["workspace"])
    if body.disabled is not None:
        members_store.set_disabled(member["username"], body.disabled)
        if body.disabled:          # out now, on every device, not at their next click
            sessions_store.delete_user(member["username"], member["workspace"])
    log.warning("%s changed %s's access: %s", user["username"], member["username"],
                body.model_dump(exclude_unset=True))
    m = next(x for x in members_store.list_members(member["workspace"])
             if x["username"] == member["username"])
    return _member_out(m)


@app.delete("/api/admin/members/{username}")
def delete_member(username: str, user=Depends(require_admin)):
    member = _workspace_member(user, username)
    members_store.remove(member["username"])
    sessions_store.delete_user(member["username"], member["workspace"])
    log.warning("%s removed %s", user["username"], member["username"])
    return {"ok": True}


@app.post("/api/admin/members/{username}/sign-out")
def sign_out_member(username: str, user=Depends(require_admin)):
    """End every session a member has. They can sign in again (unless their access
    is off): for a lost laptop, say."""
    member = _workspace_member(user, username)
    ended = sessions_store.delete_user(member["username"], member["workspace"])
    log.warning("%s signed %s out everywhere (%d sessions)", user["username"],
                member["username"], ended)
    return {"ok": True, "ended": ended}


@app.get("/api/health")
def health_check():
    """The process is up. Docker's HEALTHCHECK calls this: keep it cheap, always 200.
    "guards" shows whether the toxicity check is running, since the app starts
    without it unless REQUIRE_TOXICITY_GUARD says not to."""
    return {"ok": True, "guards": safety.guards_status()}


@app.get("/api/health/ready")
def ready_check():
    """Can this server actually serve? (database, search index, encryption key, and
    the toxicity guard if REQUIRE_TOXICITY_GUARD; see health.py). 503 with the failing check's reason otherwise. For a deploy gate or
    an uptime monitor, not for Docker: restarting the container fixes none of them."""
    guards = safety.guards_status()
    ok, checks = health.ready(members_store.DB_PATH, ingest.DB_FOLDER, guards=guards)
    return JSONResponse({"ok": ok, "checks": checks, "guards": guards},
                        status_code=200 if ok else 503)


# ---- The website -------------------------------------------------------------
# In production (Docker) the built React app lives in frontend/dist and is served
# from here, so one container + one port serves both the site and the API.
# Locally, `npm run dev` serves the site instead and this folder doesn't exist.
_SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")

from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.exceptions import HTTPException as _NotServed  # noqa: E402


class _Site(StaticFiles):
    """index.html is never cached, so a deploy shows up on the next load.
    Built assets have content hashes in their names, so they cache forever.
    Any other path that isn't a file (/chat, /admin/sign-in, ...) is a screen of
    the app: it gets index.html, whose router (frontend/src/routes.tsx) shows it."""
    async def get_response(self, path, scope):
        path = path.replace("\\", "/")
        try:
            response = await super().get_response(path, scope)
        except _NotServed as exc:
            # A missing API route, asset or file (a "." in its name) stays a 404.
            screen = (not path.startswith(("api/", "assets/"))
                      and "." not in path.rsplit("/", 1)[-1])
            if exc.status_code != 404 or not screen:
                raise
            response = await super().get_response("index.html", scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


if os.path.isdir(_SITE):
    app.mount("/", _Site(directory=_SITE, html=True), name="site")
