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
    POST   /auth/login            {username, password} -> {token, user}
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
    POST   /integrations/google_drive/connect   -> {url} of Google's consent screen (HR / Admin)
    GET    /integrations/google_drive/callback  Google redirects the browser back here
    GET    /integrations/google_drive/folders   browse folders to pick         (HR / Admin)
    PUT    /integrations/google_drive/folders   {folders} choose what to sync  (HR / Admin)
    POST   /integrations/google_drive/sync      sync now                      (HR / Admin)
    DELETE /integrations/google_drive           disconnect + delete copies    (HR / Admin)
Every document change re-indexes automatically; Drive re-syncs every 30 minutes.

The role (public / hr) is chosen in the UI and sent as "X-Role", exactly like
the Streamlit dropdown — it is a demo switch, not a security boundary.
"""

import contextlib
import json
import io
import os
import secrets
import threading
import time

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

load_dotenv()

import auth                          # noqa: E402  (needs the .env loaded first)
import docs_store                    # noqa: E402
import ingest                        # noqa: E402
import integrations_store            # noqa: E402
import sessions_store                # noqa: E402
import workspaces                    # noqa: E402
from agent import build_agent        # noqa: E402
from connectors import google_drive  # noqa: E402

app = FastAPI(title="Knowledge Assistant API")

SESSION_SECONDS = 12 * 60 * 60
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60
STEP_PREVIEW_CHARS = 600
DRIVE_SYNC_EVERY = 30 * 60        # seconds between automatic Drive syncs
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
_syncing = set()          # workspaces whose Drive sync is running
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
    user = sessions_store.get(authorization.removeprefix("Bearer ").strip())
    if not user:
        raise HTTPException(401, "Please sign in.")
    return user


def current_role(x_role: str = Header(default="public")):
    return "hr" if x_role == "hr" else "public"


def require_hr(user=Depends(current_user), role=Depends(current_role)):
    if role != "hr":
        raise HTTPException(403, "Switch to HR / Admin to manage documents and integrations.")
    return user


def ws_of(user):
    ws = user.get("workspace") or workspaces.DEFAULT
    if not workspaces.valid(ws):
        raise HTTPException(400, "Invalid workspace on this account.")
    return ws


@app.post("/api/auth/login")
def login(body: LoginIn):
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
def _sync_drive(ws):
    """Sync one workspace's Google Drive (background); re-index if anything changed."""
    with _sync_lock:
        if ws in _syncing:
            return
        _syncing.add(ws)
    try:
        integrations_store.update(ws, google_drive.PROVIDER, status="syncing", last_error=None)
        count, changed = google_drive.sync(ws)
        integrations_store.update(ws, google_drive.PROVIDER, status="connected",
                                  last_sync_at=time.time(), doc_count=count)
        if changed:
            _schedule_index(ws)
    except Exception as e:
        integrations_store.update(ws, google_drive.PROVIDER, status="error",
                                  last_error=f"{type(e).__name__}: {e}"[:500])
    finally:
        with _sync_lock:
            _syncing.discard(ws)


def _start_drive_sync(ws):
    threading.Thread(target=_sync_drive, args=(ws,), daemon=True).start()


def _drive_scheduler():
    """Re-sync every connected Drive every DRIVE_SYNC_EVERY seconds."""
    while True:
        time.sleep(DRIVE_SYNC_EVERY)
        if not google_drive.configured():
            continue
        for row in integrations_store.all_connected(google_drive.PROVIDER):
            if row["status"] in ("connected", "error"):
                _start_drive_sync(row["workspace"])


@app.on_event("startup")
def _start_background_jobs():
    threading.Thread(target=_drive_scheduler, daemon=True).start()


def _redirect_uri(request: Request):
    base = os.getenv("PUBLIC_URL") or str(request.base_url)
    return base.rstrip("/") + "/api/integrations/google_drive/callback"


@app.get("/api/integrations")
def list_integrations(user=Depends(current_user)):
    ws = ws_of(user)
    drive = integrations_store.get(ws, google_drive.PROVIDER)
    if drive:
        drive_status = drive["status"]
    else:
        drive_status = "disconnected" if google_drive.configured() else "not_configured"
    return {"workspace": ws, "integrations": [
        {"id": "uploads", "name": "Uploaded files", "status": "connected",
         "doc_count": len(docs_store.list_docs(workspaces.docs_root(ws)))},
        {"id": "google_drive", "name": "Google Drive", "status": drive_status,
         "account": drive and drive["account"], "connected_by": drive and drive["connected_by"],
         "last_sync_at": drive and drive["last_sync_at"],
         "doc_count": (drive and drive["doc_count"]) or 0,
         "last_error": drive and drive["last_error"],
         "folders": ((drive and drive.get("settings")) or {}).get("folders", [])},
        {"id": "outline", "name": "Outline wiki",
         "status": "connected" if outline_on(ws) else "unavailable"},
    ]}


@app.post("/api/integrations/google_drive/connect")
def drive_connect(request: Request, user=Depends(require_hr)):
    if not google_drive.configured():
        raise HTTPException(400, "Google Drive isn't set up on this server yet "
                                 "(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing).")
    now = time.time()
    for s in [k for k, v in _oauth_states.items() if v["expires"] < now]:
        del _oauth_states[s]
    state = secrets.token_urlsafe(24)
    redirect_uri = _redirect_uri(request)
    _oauth_states[state] = {"workspace": ws_of(user), "user": user["username"],
                            "expires": now + OAUTH_STATE_SECONDS, "redirect_uri": redirect_uri}
    return {"url": google_drive.auth_url(redirect_uri, state)}


@app.get("/api/integrations/google_drive/callback")
def drive_callback(state: str = "", code: str = "", error: str = ""):
    """Google sends the browser back here. The one-time `state` ties it to the
    workspace and user who clicked Connect (a redirect carries no session header)."""
    pending = _oauth_states.pop(state, None)
    if not pending or pending["expires"] < time.time():
        return RedirectResponse("/#integrations?error=expired")
    if error or not code:
        return RedirectResponse("/#integrations?error=" + (error or "cancelled"))
    try:
        google_drive.finish_connect(pending["workspace"], code, pending["redirect_uri"],
                                    pending["user"])
    except Exception:
        return RedirectResponse("/#integrations?error=connect_failed")
    drive = integrations_store.get(pending["workspace"], google_drive.PROVIDER) or {}
    if (drive.get("settings") or {}).get("folders"):
        _start_drive_sync(pending["workspace"])   # reconnect: keep syncing the same folders
        return RedirectResponse("/#integrations?connected=google_drive")
    # New connection: nothing is synced until the admin picks folders.
    return RedirectResponse("/#integrations?connected=google_drive&choose=1")


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
    _start_drive_sync(ws)
    return {"folders": folders}


@app.post("/api/integrations/google_drive/sync")
def drive_sync(user=Depends(require_hr)):
    ws = ws_of(user)
    if not integrations_store.get(ws, google_drive.PROVIDER):
        raise HTTPException(400, "Google Drive isn't connected.")
    _start_drive_sync(ws)
    return {"ok": True}


@app.delete("/api/integrations/google_drive")
def drive_disconnect(user=Depends(require_hr)):
    ws = ws_of(user)
    google_drive.disconnect(ws)
    _schedule_index(ws)            # drop the Drive files from the search index
    return {"ok": True}


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
