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
    POST   /auth/logout
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
    POST   /integrations/{provider}/sync        sync now                      (HR / Admin)
    DELETE /integrations/{provider}             disconnect + delete copies    (HR / Admin)
  ({provider} = google_drive | notion)
    GET    /admin/sign-in         this workspace's domains and sign-in providers  (admin)
    POST   /admin/sign-in/domains {domain} -> the DNS record that proves it      (admin)
    POST   /admin/sign-in/domains/{domain}/verify   check the DNS record          (admin)
    DELETE /admin/sign-in/domains/{domain}                                         (admin)
    POST   /admin/sign-in/connections {label, issuer, client_id, client_secret}    (admin)
    PATCH  /admin/sign-in/connections/{id} change any of those, or {enabled}       (admin)
    POST   /admin/sign-in/saml-connections {label, metadata_url | metadata_xml |
                                  entity_id + sso_url + certificate, email_attribute} (admin)
    PATCH  /admin/sign-in/saml-connections/{id} change any of those, or {enabled}  (admin)
    DELETE /admin/sign-in/connections/{id}                                         (admin)
Every document change re-indexes automatically; connectors re-sync every 30 minutes.

ROLES come from the account (members_store.py): public, hr or admin. Admin
counts as HR everywhere here. An HR / Admin account can send "X-Role: public" to
see the app as a public user would; the header can never raise anyone TO HR.
"""

import calendar
import contextlib
import json
import io
import logging
import queue
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from saml2 import time_util as saml2_time
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

import auth                          # noqa: E402  (needs the .env loaded first)
import docs_store                    # noqa: E402
import ingest                        # noqa: E402
import integrations_store            # noqa: E402
import members_store                 # noqa: E402
import saml_sp                       # noqa: E402
import sessions_store                # noqa: E402
import sso                           # noqa: E402
import sso_setup                     # noqa: E402
import url_guard                     # noqa: E402
import workspaces                    # noqa: E402
from agent import build_agent        # noqa: E402
from connectors import google_drive, notion  # noqa: E402

@contextlib.asynccontextmanager
async def _lifespan(app):
    """Background jobs that run for the life of the server."""
    threading.Thread(target=_sync_scheduler, daemon=True).start()
    yield


app = FastAPI(title="Knowledge Assistant API", lifespan=_lifespan)
log = logging.getLogger("uvicorn.error")   # shows in the uvicorn console
# Holds Authlib's state / nonce / PKCE verifier between the redirect to Google or
# Microsoft and the callback — nothing else goes in it. Without SESSION_SECRET a
# random key is used, which only cancels sign-ins in progress during a restart.
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32),
                   session_cookie="kb_oauth", max_age=10 * 60, same_site="lax",
                   https_only=os.getenv("PUBLIC_URL", "").startswith("https://"))

SESSION_SECONDS = 12 * 60 * 60
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
            "password": auth.password_login_on(), "email_sso": email_sso}


@app.post("/api/auth/login")
def login(body: LoginIn):
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
    user = auth.authenticate(body.username, body.password)
    if not user:
        time.sleep(1)                          # slow down password guessing
        count += 1
        _failed_logins[key] = ((0, time.time() + LOCKOUT_SECONDS)
                               if count >= MAX_FAILED_LOGINS else (count, 0))
        raise HTTPException(401, "Wrong username or password.")
    _failed_logins.pop(key, None)
    token = secrets.token_urlsafe(32)
    sessions_store.create(token, user, SESSION_SECONDS)   # survives restarts / deploys
    return {"token": token, "user": user}


# ---- Single sign-on (sso.py) ---------------------------------------------------
# Errors go back to the login page as /#login?error=<code>; it explains each code.
class CodeIn(BaseModel):
    code: str


@app.get("/api/auth/sso/{provider}/start")
async def sso_start(provider: str, request: Request):
    c = sso.client(provider)
    if not c:
        return RedirectResponse("/#login?error=sso_unavailable")
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
    return await _finish_sso(provider, c, request, lambda claims: sso.user_for(provider, claims))


async def _provider_answer(name, c, request):
    """The provider sent the browser back. (claims, None, None) if its answer
    checks out, else (None, error code for the login page, detail for the log)."""
    error = request.query_params.get("error")
    if error:                      # the provider refused; log why (e.g. admin consent needed)
        detail = f"{error}: {request.query_params.get('error_description', '')}".rstrip(": ")
        log.warning("SSO %s: provider returned %s", name, detail)
        return None, "cancelled" if error == "access_denied" else "sso_failed", detail
    try:
        token = await c.authorize_access_token(request)    # checks state + the ID token
    except Exception as e:
        log.exception("SSO %s: sign-in callback failed", name)
        return None, "sso_failed", f"{type(e).__name__}: {e}"
    return token.get("userinfo") or {}, None, None


async def _finish_sso(name, c, request, user_for):
    """Check the provider's answer, then hand the browser a one-time code for the session."""
    claims, error, _ = await _provider_answer(name, c, request)
    if error:
        return RedirectResponse(f"/#login?error={error}")
    try:
        user = user_for(claims)
    except sso.NotAllowed as e:
        return RedirectResponse(f"/#login?error={e.reason}")
    except Exception:              # e.g. the database can't be read
        log.exception("SSO %s: couldn't decide who gets in", name)
        return RedirectResponse("/#login?error=sso_failed")
    return RedirectResponse("/#sso?code=" + sso.issue_code(user))


# ---- "Test sign-in": an admin tries a provider in a popup --------------------------
# The admin gets a ticket (POST /api/admin/sign-in/connections/{id}/test) and opens
# the provider's start URL with it. The answer is checked exactly as for a real
# sign-in, but nobody is signed in or added: the result (what the provider sent,
# and what would happen) waits under the ticket for the admin's screen to fetch.
# It works on a provider that's turned off, so it can be tried before going live.
SSO_TEST_SECONDS = 10 * 60
_sso_tests = {}            # ticket -> {"workspace", "cid", "expires", "result"}
_sso_tests_lock = threading.Lock()
SSO_TEST_SESSION_KEY = "sso_test"


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


def _finish_sso_test(test, conn, result, sent=None):
    result = {**result, "sent": _shown(sent)}
    with _sso_tests_lock:
        test["result"] = result
    members_store.log_event(conn["workspace"], conn["id"], result.get("email"), "test",
                            None if result["ok"] else result.get("reason"))
    return RedirectResponse("/#sso-test", status_code=303)


def _test_failure(code, detail):
    return {"ok": False, "reason": code, "detail": (detail or "")[:300] or None}


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
def sso_discover(body: EmailIn):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter your work email address.")
    try:
        conns = sso.connections_for_email(email)
    except sqlite3.Error:
        log.exception("Couldn't look up sign-in for an email")
        raise HTTPException(503, "Sign-in settings can't be read. Please try again.")
    return {"connections": [{"id": c["id"], "name": c["label"], "url": _start_url(c, email)}
                            for c in conns]}


@app.get("/api/auth/sso/c/{cid}/start")
async def sso_connection_start(cid: str, request: Request, login_hint: str = "",
                               test: str = ""):
    trial = _sso_test(test, cid)
    conn = members_store.get_connection(cid) if trial else _usable_connection(cid, "oidc")
    if not conn or conn["protocol"] != "oidc":
        return RedirectResponse("/#login?error=sso_unavailable")
    if trial:
        request.session[SSO_TEST_SESSION_KEY] = {"cid": cid, "ticket": test}
    else:
        request.session.pop(SSO_TEST_SESSION_KEY, None)     # a real sign-in, not a test
    extra = {"login_hint": login_hint} if EMAIL_RE.match(login_hint) else {}
    if trial:
        extra["prompt"] = "login"          # show the admin the provider's sign-in page
    try:
        return await sso.connection_client(conn).authorize_redirect(
            request, _public_url(request, f"/api/auth/sso/c/{cid}/callback"), **extra)
    except Exception:
        log.exception("SSO connection %s: couldn't start sign-in", cid)
        return RedirectResponse("/#login?error=sso_failed")


@app.get("/api/auth/sso/c/{cid}/callback")
async def sso_connection_callback(cid: str, request: Request):
    marker = request.session.pop(SSO_TEST_SESSION_KEY, None) or {}
    trial = _sso_test(marker.get("ticket"), cid) if marker.get("cid") == cid else None
    conn = members_store.get_connection(cid) if trial else _usable_connection(cid, "oidc")
    if not conn or conn["protocol"] != "oidc":
        return RedirectResponse("/#login?error=sso_unavailable")
    claims, error, detail = await _provider_answer(f"connection {cid}",
                                                   sso.connection_client(conn), request)
    if trial:
        result = sso.try_connection(conn, claims) if claims is not None else \
            _test_failure(error, detail)
        return _finish_sso_test(trial, conn, result, claims)
    if error:
        members_store.log_event(conn["workspace"], cid, None, "failed", detail)
        return RedirectResponse(f"/#login?error={error}")
    try:
        user = sso.user_for_connection(conn, claims)
    except sso.NotAllowed as e:
        members_store.log_event(conn["workspace"], cid, claims.get("email"), "refused", e.reason)
        return RedirectResponse(f"/#login?error={e.reason}")
    except Exception:
        log.exception("SSO connection %s: couldn't decide who gets in", cid)
        return RedirectResponse("/#login?error=sso_failed")
    members_store.log_event(conn["workspace"], cid, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + sso.issue_code(user))


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
    conn = members_store.get_connection(cid) if trial else _usable_connection(cid, "saml")
    if not conn or conn["protocol"] != "saml" or not saml_sp.xmlsec_binary():
        return None, None
    return conn, members_store.connection_keys(cid)


@app.get("/api/auth/saml/{cid}/metadata")
def saml_metadata(cid: str, request: Request):
    """Our SP's metadata, for the customer's IdP (public, like any SP's)."""
    conn = members_store.get_connection(cid)
    if not conn or conn["protocol"] != "saml" or not saml_sp.xmlsec_binary():
        raise HTTPException(404, "No such SAML sign-in.")
    xml = saml_sp.sp_metadata(conn, members_store.connection_keys(cid), _public_url(request, ""))
    return Response(xml, media_type="application/samlmetadata+xml")


@app.get("/api/auth/saml/{cid}/start")
def saml_start(cid: str, request: Request, test: str = ""):
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
        return RedirectResponse("/#login?error=sso_failed")
    members_store.remember_saml_request(cid, request_id, saml_sp.REQUEST_SECONDS)
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
    # Only to report a failed TEST to the admin; it never lets anything through.
    trial = (_sso_test(RelayState[len(TEST_RELAY_PREFIX):], cid)
             if RelayState.startswith(TEST_RELAY_PREFIX) else None)
    conn, keys = _saml_conn(cid, trial)
    if not conn:
        return RedirectResponse("/#login?error=sso_unavailable", status_code=303)

    def refuse(detail):
        if trial:
            return _finish_sso_test(trial, conn, _test_failure("sso_failed", detail))
        members_store.log_event(conn["workspace"], cid, None, "failed", detail)
        return RedirectResponse("/#login?error=sso_failed", status_code=303)

    try:
        answer = saml_sp.finish(conn, keys, _public_url(request, ""), SAMLResponse,
                                members_store.open_saml_requests(cid))
    except saml_sp.SamlError as e:
        log.warning("SAML %s: refused an answer: %s", cid, e)
        return refuse(f"SAML: {e}")
    expires = time.time() + saml_sp.REQUEST_SECONDS
    if answer["expires"]:
        expires = calendar.timegm(saml2_time.str_to_time(answer["expires"]))
    if not (members_store.close_saml_request(answer["request_id"]) and
            members_store.first_use_of_assertion(answer["assertion_id"], expires)):
        log.warning("SAML %s: refused a replayed answer", cid)
        return refuse("SAML: this answer was already used")
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
    conn, _ = _saml_conn(cid, trial)
    if not conn:
        return RedirectResponse("/#login?error=sso_unavailable")
    claims = {"email": parked["email"], "name": parked["name"]}
    if trial:
        return _finish_sso_test(trial, conn, sso.try_connection(conn, claims), parked["sent"])
    try:
        user = sso.user_for_connection(conn, claims)
    except sso.NotAllowed as e:
        members_store.log_event(conn["workspace"], cid, parked["email"], "refused", e.reason)
        return RedirectResponse(f"/#login?error={e.reason}")
    except Exception:
        log.exception("SAML %s: couldn't decide who gets in", cid)
        return RedirectResponse("/#login?error=sso_failed")
    members_store.log_event(conn["workspace"], cid, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + sso.issue_code(user))


@app.post("/api/auth/sso/exchange")
def sso_exchange(body: CodeIn):
    user = sso.redeem_code(body.code)
    if not user:
        raise HTTPException(401, "That sign-in expired. Please sign in again.")
    token = secrets.token_urlsafe(32)
    sessions_store.create(token, user, SESSION_SECONDS)
    return {"token": token, "user": user}


@app.post("/api/auth/logout")
def logout(authorization: str = Header(default="")):
    sessions_store.delete(authorization.removeprefix("Bearer ").strip())
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user


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
           "created_at": c["created_at"], "last_login_at": c["last_login_at"]}
    if c["protocol"] == "saml":
        entity_id, acs, metadata_url = saml_sp.sp_urls(_public_url(request, ""), c["id"])
        settings = c["metadata"]
        return {**out, "sp_entity_id": entity_id, "acs_url": acs,
                "sp_metadata_url": metadata_url, "sso_url": settings.get("sso_url"),
                "metadata_url": settings.get("metadata_url"),
                "email_attribute": settings.get("email_attribute"),
                "signs_requests": bool(settings.get("want_authn_requests_signed"))}
    return {**out, "client_id": c["client_id"],
            "redirect_uri": _public_url(request, f"/api/auth/sso/c/{c['id']}/callback")}


def _saml_settings(body, old=None):
    """(IdP entity ID, settings) from the admin's input, checked. `old` settings
    are kept for anything not given. Raises HTTPException(400)."""
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
        idp = saml_sp.read_idp_metadata(xml)
        url_guard.require_https(idp["sso_url"])
    except sso_setup.SetupError as e:
        raise _setup_error(e)
    except (saml_sp.SamlError, url_guard.UnsafeURL) as e:
        raise HTTPException(400, f"SAML metadata: {e}")
    settings.update(idp_metadata=xml, sso_url=idp["sso_url"],
                    want_authn_requests_signed=idp["want_authn_requests_signed"])
    if body.email_attribute is not None:
        settings["email_attribute"] = body.email_attribute.strip() or None
    return idp["entity_id"], settings


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
            "events": [{**e, "connection": labels.get(e["connection_id"])}
                       for e in members_store.recent_events(ws)]}


@app.post("/api/admin/sign-in/domains")
def add_domain(body: DomainIn, user=Depends(require_admin)):
    try:
        domain = sso_setup.normalize_domain(body.domain)
    except sso_setup.SetupError as e:
        raise _setup_error(e)
    return _domain_out(members_store.claim_domain(ws_of(user), domain))


@app.post("/api/admin/sign-in/domains/{domain}/verify")
def verify_domain(domain: str, user=Depends(require_admin)):
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
        try:
            fields["issuer"] = sso_setup.normalize_issuer(fields["issuer"])
            fields["metadata"] = sso_setup.oidc_metadata(fields["issuer"])
        except sso_setup.SetupError as e:
            raise _setup_error(e)
    if fields:
        conn = members_store.update_connection(ws, cid, **fields)
    return _connection_out(request, conn)


@app.post("/api/admin/sign-in/saml-connections")
def add_saml_connection(body: SamlConnectionIn, request: Request, user=Depends(require_admin)):
    if not (body.label or "").strip():
        raise HTTPException(400, "Give the sign-in button a name.")
    entity_id, settings = _saml_settings(body)
    cid = _connection_id(body.id)
    sp_key, sp_cert = saml_sp.new_keypair(f"Knowledge assistant {cid}")
    conn = members_store.create_connection(ws_of(user), "saml", body.label.strip(), entity_id,
                                           None, {"sp_key": sp_key, "sp_cert": sp_cert},
                                           settings, user["username"], cid=cid)
    if body.enabled is False:
        conn = members_store.update_connection(conn["workspace"], cid, enabled=False)
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
    if any(v is not None for v in (body.metadata_url, body.metadata_xml, body.entity_id,
                                   body.sso_url, body.certificate, body.email_attribute)):
        fields["issuer"], fields["metadata"] = _saml_settings(body, old=conn["metadata"])
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


@app.delete("/api/admin/sign-in/connections/{cid}")
def delete_connection(cid: str, user=Depends(require_admin)):
    if not members_store.delete_connection(ws_of(user), cid):
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    return {"ok": True}


# ---- Chat --------------------------------------------------------------------
class ChatIn(BaseModel):
    question: str


def outline_on(ws):
    """The Outline wiki is configured server-wide (.env) for the default workspace."""
    return ws == workspaces.DEFAULT and bool(os.getenv("OUTLINE_API_TOKEN"))


def get_agent(ws, role):
    with _agents_lock:
        if (ws, role) not in _agents:
            _agents[(ws, role)] = build_agent(role, workspaces.collection(ws), outline_on(ws))
        return _agents[(ws, role)]


@app.post("/api/chat")
def chat(body: ChatIn, user=Depends(current_user), role=Depends(current_role)):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
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


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn, user=Depends(current_user), role=Depends(current_role)):
    """Same as /api/chat, but streams what's happening as Server-Sent Events:
         status      {"text"}                       e.g. "Checking your question"
         step_start  {"id", "tool", "label"}        e.g. "Searching company docs"
         step_end    {"id", "tool", "result"}       e.g. "found 2 relevant documents"
         answer      {"text"}                       the answer so far (PII already masked)
         done        {"answer", "sources", "steps"} the final, checked answer
         error       {"message"}"""
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    ws = ws_of(user)
    with _gate:
        if not _gate.wait_for(lambda: not _gate_of(ws)["indexing"], timeout=120):
            raise HTTPException(503, "Documents are being re-indexed. Try again in a moment.")
        _gate_of(ws)["active"] += 1

    events = queue.Queue()

    def work():
        try:
            answer, trace, sources = get_agent(ws, role)(question, with_sources=True,
                                                         on_event=events.put)
            events.put({"type": "done", "answer": answer, "sources": sources,
                        "steps": _steps(trace)})
        except Exception as e:                     # show it in the chat, keep serving
            events.put({"type": "error", "message": f"Something went wrong: {type(e).__name__}"})
        finally:
            with _gate:
                _gate_of(ws)["active"] -= 1
                _gate.notify_all()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def sse():
        while (event := events.get()) is not None:
            yield f"data: {json.dumps(event)}\n\n"

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
                                      collection=workspaces.collection(ws))
            with _agents_lock:
                for key in [k for k in _agents if k[0] == ws]:
                    del _agents[key]   # next question loads the new index
        except Exception as e:        # keep the server alive; show it in the UI
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
    return {"docs": docs}


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
    ws = ws_of(user)
    if access not in docs_store.ACCESS_LEVELS:
        raise HTTPException(400, "Access must be public or restricted.")
    saved = []
    for f in files:
        if not f.filename.lower().endswith(docs_store.EXTENSIONS):
            continue
        name = docs_store.safe_name(f.filename)
        if name:
            docs_store.save(access, name, f.file.read(), workspaces.docs_root(ws))
            saved.append(name)
    if not saved:
        raise HTTPException(400, "Please choose at least one .txt or .pdf file.")
    _schedule_index(ws)
    return {"saved": saved}


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
            "last_error": row and row["last_error"]}
    if provider == google_drive.PROVIDER:
        info["folders"] = ((row and row.get("settings")) or {}).get("folders", [])
    return info


@app.get("/api/integrations")
def list_integrations(user=Depends(current_user)):
    ws = ws_of(user)
    return {"workspace": ws, "integrations": [
        {"id": "uploads", "name": "Uploaded files", "status": "connected",
         "doc_count": len(docs_store.list_docs(workspaces.docs_root(ws)))},
        _integration_info(ws, "google_drive"),
        _integration_info(ws, "notion"),
        {"id": "outline", "name": "Outline wiki",
         "status": "connected" if outline_on(ws) else "unavailable"},
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
        return RedirectResponse("/#integrations?error=connect_failed")
    if _ready_to_sync(ws, provider):
        _start_sync(ws, provider)
        return RedirectResponse(f"/#integrations?connected={provider}")
    # Drive, new connection: nothing is synced until the admin picks folders.
    return RedirectResponse(f"/#integrations?connected={provider}&choose=1")


@app.post("/api/integrations/{provider}/sync")
def integration_sync(provider: str, user=Depends(require_hr)):
    _connector(provider)
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
    except Exception:
        pass                                   # no index yet: everything shows 0

    def indexed(path):
        return chunks.get(os.path.normcase(os.path.abspath(path)), 0)

    items = []
    for d in docs_store.list_docs(root):
        if role != "hr" and d["access"] == "restricted":
            continue
        items.append({"title": d["name"], "source": "Uploaded", "access": d["access"],
                      "url": None, "modified": os.path.getmtime(d["path"]),
                      "size": d["size"], "chunks": indexed(d["path"])})

    connectors_dir = os.path.join(root, "connectors")
    for provider in sorted(os.listdir(connectors_dir)) if os.path.isdir(connectors_dir) else []:
        folder = os.path.join(connectors_dir, provider)
        try:
            with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        for name, info in manifest.items():
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            items.append({"title": info.get("title") or name,
                          "source": info.get("provider") or provider, "access": "public",
                          "url": info.get("url"), "modified": info.get("modified"),
                          "size": os.path.getsize(path), "chunks": indexed(path)})

    items.sort(key=lambda i: (i["source"] != "Uploaded", i["title"].lower()))
    return {"workspace": ws, "items": items,
            "outline": outline_on(ws)}         # searched live, so not listed here


@app.get("/api/health")
def health():
    return {"ok": True}


# ---- The website -------------------------------------------------------------
# In production (Docker) the built React app lives in frontend/dist and is served
# from here, so one container + one port serves both the site and the API.
# Locally, `npm run dev` serves the site instead and this folder doesn't exist.
_SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
if os.path.isdir(_SITE):
    from fastapi.staticfiles import StaticFiles

    class _Site(StaticFiles):
        """index.html is never cached, so a deploy shows up on the next load.
        Built assets have content hashes in their names, so they cache forever."""
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if path.replace("\\", "/").startswith("assets/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "no-cache"
            return response

    app.mount("/", _Site(directory=_SITE, html=True), name="site")
