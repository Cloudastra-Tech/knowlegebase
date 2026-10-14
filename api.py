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
    GET    /auth/providers        which sign-in options the login page shows
    POST   /auth/login            {username, password} -> {token, user}
    GET    /auth/sso/{provider}/start      send the browser to Google / Microsoft
    GET    /auth/sso/{provider}/callback   they send it back here (sso.py decides who gets in)
    POST   /auth/sso/exchange     {code} -> {token, user}   the one-time code from the callback
    POST   /auth/logout
    GET    /auth/me
    POST   /chat                  {question, role} -> {answer, sources, steps}
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
Every document change re-indexes automatically; connectors re-sync every 30 minutes.

ROLES come from the account: users.json for password logins, config/sso.json
for single sign-on. An HR / Admin account can send "X-Role: public" to see the app
as a public user would; the header can never raise anyone TO HR.
"""

import contextlib
import json
import io
import logging
import os
import secrets
import threading
import time

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

import auth                          # noqa: E402  (needs the .env loaded first)
import docs_store                    # noqa: E402
import ingest                        # noqa: E402
import integrations_store            # noqa: E402
import sessions_store                # noqa: E402
import sso                           # noqa: E402
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
        access = (sso.access_for(user["username"], user.get("org")) if user.get("provider")
                  else auth.access_for(user["username"]))
    except (OSError, ValueError):            # e.g. config/sso.json isn't valid JSON
        log.exception("Couldn't read the sign-in settings")
        raise HTTPException(503, "Sign-in settings can't be read. Please tell your admin.")
    if not access:
        sessions_store.delete(token)
        raise HTTPException(401, "Please sign in.")
    return {**user, **access}


def current_role(user=Depends(current_user), x_role: str = Header(default="hr")):
    """HR only if the ACCOUNT is HR; X-Role can only lower it ("view as public")."""
    return "hr" if user["role"] == "hr" and x_role == "hr" else "public"


def require_hr(user=Depends(current_user), role=Depends(current_role)):
    if role != "hr":
        raise HTTPException(403, "Switch to HR / Admin to manage documents and integrations."
                            if user["role"] == "hr" else
                            "Only HR / Admin accounts can manage documents and integrations.")
    return user


def ws_of(user):
    ws = user.get("workspace") or workspaces.DEFAULT
    if not workspaces.valid(ws):
        raise HTTPException(400, "Invalid workspace on this account.")
    return ws


@app.get("/api/auth/providers")
def auth_providers():
    return {"providers": [{"id": p, "name": sso.LABELS[p]} for p in sso.configured()],
            "password": auth.password_login_on()}


@app.post("/api/auth/login")
def login(body: LoginIn):
    if not auth.password_login_on():
        raise HTTPException(403, "Password sign-in is turned off. Use single sign-on.")
    key = body.username.strip().lower()
    count, locked_until = _failed_logins.get(key, (0, 0))
    if time.time() < locked_until:
        raise HTTPException(429, f"Too many failed attempts. Try again in "
                                 f"{int(locked_until - time.time())} seconds.")
    if not auth.load_users():
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
            **sso.AUTHORIZE_PARAMS)
    except Exception:              # couldn't reach the provider's discovery document
        log.exception("SSO %s: couldn't start sign-in", provider)
        return RedirectResponse("/#login?error=sso_failed")


@app.get("/api/auth/sso/{provider}/callback")
async def sso_callback(provider: str, request: Request):
    c = sso.client(provider)
    if not c:
        return RedirectResponse("/#login?error=sso_unavailable")
    error = request.query_params.get("error")
    if error:                      # the provider refused; log why (e.g. admin consent needed)
        log.warning("SSO %s: provider returned %s: %s", provider, error,
                    request.query_params.get("error_description", ""))
        return RedirectResponse("/#login?error="
                                + ("cancelled" if error == "access_denied" else "sso_failed"))
    try:
        token = await c.authorize_access_token(request)    # checks state + the ID token
    except Exception:
        log.exception("SSO %s: sign-in callback failed", provider)
        return RedirectResponse("/#login?error=sso_failed")
    try:
        user = sso.user_for(provider, token.get("userinfo") or {})
    except sso.NotAllowed as e:
        return RedirectResponse(f"/#login?error={e.reason}")
    except Exception:              # e.g. config/sso.json isn't valid JSON
        log.exception("SSO %s: couldn't decide who gets in", provider)
        return RedirectResponse("/#login?error=sso_failed")
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
    steps = [{"tool": t["tool"], "input": t["input"],
              "output": t["output"][:STEP_PREVIEW_CHARS]
              + (" …(trimmed)" if len(t["output"]) > STEP_PREVIEW_CHARS else "")}
             for t in trace]
    return {"answer": answer, "sources": sources, "steps": steps}


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
