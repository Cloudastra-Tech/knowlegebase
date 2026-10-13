"""
api.py  —  the JSON API behind the React frontend (frontend/).

Same brain as the Streamlit app: the agent (agent.py), login (auth.py) and the
docs/ folder (docs_store.py). Only the screen is different.

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

The role (public / hr) is chosen in the UI and sent as "X-Role", exactly like
the Streamlit dropdown — it is a demo switch, not a security boundary.
"""

import contextlib
import io
import os
import secrets
import threading
import time

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

import auth          # noqa: E402  (needs the .env loaded first)
import docs_store    # noqa: E402
import ingest        # noqa: E402
from agent import build_agent  # noqa: E402

app = FastAPI(title="Knowledge Assistant API")

SESSION_SECONDS = 12 * 60 * 60
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60
STEP_PREVIEW_CHARS = 600

_sessions = {}            # token -> {"user": {...}, "expires": epoch}
_failed_logins = {}       # username -> (count, locked_until)
_agents = {}              # role -> agent run() function
_agents_lock = threading.Lock()
_state = {"docs_changed": False}


# ---- Auth --------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str
    password: str


def current_user(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    session = _sessions.get(token)
    if not session or session["expires"] < time.time():
        _sessions.pop(token, None)
        raise HTTPException(401, "Please sign in.")
    return session["user"]


def current_role(x_role: str = Header(default="public")):
    return "hr" if x_role == "hr" else "public"


def require_hr(user=Depends(current_user), role=Depends(current_role)):
    if role != "hr":
        raise HTTPException(403, "Switch to HR / Admin to manage documents.")
    return user


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
    _sessions[token] = {"user": user, "expires": time.time() + SESSION_SECONDS}
    return {"token": token, "user": user}


@app.post("/api/auth/logout")
def logout(authorization: str = Header(default="")):
    _sessions.pop(authorization.removeprefix("Bearer ").strip(), None)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user


# ---- Chat --------------------------------------------------------------------
class ChatIn(BaseModel):
    question: str


def get_agent(role):
    with _agents_lock:
        if role not in _agents:
            _agents[role] = build_agent(role)
        return _agents[role]


@app.post("/api/chat")
def chat(body: ChatIn, user=Depends(current_user), role=Depends(current_role)):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    answer, trace, sources = get_agent(role)(question, with_sources=True)
    steps = [{"tool": t["tool"], "input": t["input"],
              "output": t["output"][:STEP_PREVIEW_CHARS]
              + (" …(trimmed)" if len(t["output"]) > STEP_PREVIEW_CHARS else "")}
             for t in trace]
    return {"answer": answer, "sources": sources, "steps": steps}


# ---- Documents ---------------------------------------------------------------
class NewDocIn(BaseModel):
    name: str
    access: str
    content: str


class SaveDocIn(BaseModel):
    content: str


def _existing_path(access, name):
    path = docs_store.doc_path(access, name)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Document not found.")
    return path


def _changed():
    _state["docs_changed"] = True


@app.get("/api/docs")
def list_docs(user=Depends(require_hr)):
    docs = [{k: d[k] for k in ("access", "name", "size")} for d in docs_store.list_docs()]
    return {"docs": docs, "changed": _state["docs_changed"]}


@app.get("/api/docs/{access}/{name}")
def read_doc(access: str, name: str, user=Depends(require_hr)):
    path = _existing_path(access, name)
    if name.lower().endswith(".pdf"):
        return FileResponse(path, media_type="application/pdf", filename=name)
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"access": access, "name": name, "content": f.read()}


@app.post("/api/docs")
def create_doc(body: NewDocIn, user=Depends(require_hr)):
    name = docs_store.safe_name(body.name)
    if body.access not in docs_store.ACCESS_LEVELS:
        raise HTTPException(400, "Access must be public or restricted.")
    if not name or not body.content.strip():
        raise HTTPException(400, "Please give a file name and some content.")
    if os.path.exists(docs_store.doc_path(body.access, name)):
        raise HTTPException(409, f"{name} already exists. Edit it instead.")
    docs_store.save(body.access, name, body.content.encode("utf-8"))
    _changed()
    return {"access": body.access, "name": name}


@app.put("/api/docs/{access}/{name}")
def save_doc(access: str, name: str, body: SaveDocIn, user=Depends(require_hr)):
    _existing_path(access, name)
    if not name.lower().endswith(".txt"):
        raise HTTPException(400, "Only .txt documents can be edited here.")
    docs_store.save(access, name, body.content.encode("utf-8"))
    _changed()
    return {"ok": True}


@app.delete("/api/docs/{access}/{name}")
def delete_doc(access: str, name: str, user=Depends(require_hr)):
    os.remove(_existing_path(access, name))
    _changed()
    return {"ok": True}


@app.post("/api/docs/upload")
def upload_docs(files: list[UploadFile] = File(...), access: str = Form("public"),
                user=Depends(require_hr)):
    if access not in docs_store.ACCESS_LEVELS:
        raise HTTPException(400, "Access must be public or restricted.")
    saved = []
    for f in files:
        if not f.filename.lower().endswith(docs_store.EXTENSIONS):
            continue
        name = docs_store.safe_name(f.filename)
        if name:
            docs_store.save(access, name, f.file.read())
            saved.append(name)
    if not saved:
        raise HTTPException(400, "Please choose at least one .txt or .pdf file.")
    _changed()
    return {"saved": saved}


@app.post("/api/reindex")
def reindex(user=Depends(require_hr)):
    if not docs_store.list_docs():
        raise HTTPException(400, "Add at least one document first.")
    log = io.StringIO()
    with _agents_lock, contextlib.redirect_stdout(log):
        summary = ingest.main()
        _agents.clear()                # next question reloads the new index
    _state["docs_changed"] = False
    return {"summary": summary, "log": log.getvalue()}
