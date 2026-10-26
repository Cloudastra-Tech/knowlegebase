"""Documents (HR / Admin): the workspace's uploaded files (docs_store.py). Every
change queues a re-index of the documents it touched."""

import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import docs_store
import workspaces
from kb.audit import audited
from kb.deps import require_hr, ws_of
from kb.rate_limit import user_limit
from kb.services import indexing
from kb.settings import UPLOAD_MAX_FILE_MB, UPLOAD_MAX_REQUEST_MB, env_int

router = APIRouter()


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


@router.get("/api/docs")
def list_docs(user=Depends(require_hr)):
    root = workspaces.docs_root(ws_of(user))
    docs = [{k: d[k] for k in ("access", "name", "size")} for d in docs_store.list_docs(root)]
    return {"docs": docs, "upload_limits": _upload_limits()}


def _upload_limits():
    """For the upload screen, so it can say so before sending a file that's too big."""
    return {"file_mb": env_int("UPLOAD_MAX_FILE_MB", UPLOAD_MAX_FILE_MB),
            "request_mb": env_int("UPLOAD_MAX_REQUEST_MB", UPLOAD_MAX_REQUEST_MB)}


# Files that are sent as they are (downloaded), not as editable text.
_FILE_TYPES = {".pdf": "application/pdf", ".md": "text/markdown", ".html": "text/plain",
               ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
               ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


@router.get("/api/docs/{access}/{name}")
def read_doc(access: str, name: str, user=Depends(require_hr)):
    path = _existing_path(ws_of(user), access, name)
    ext = os.path.splitext(name.lower())[1]
    if ext in _FILE_TYPES:            # an uploaded page is never served as HTML here
        return FileResponse(path, media_type=_FILE_TYPES[ext], filename=name)
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"access": access, "name": name, "content": f.read()}


@router.post("/api/docs")
def create_doc(body: NewDocIn, user=Depends(require_hr), audit=audited("doc.create")):
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
    audit.record("doc", f"{body.access}/{name}", {"size": len(body.content.encode("utf-8"))},
                 user=user)
    indexing.schedule_index(ws, docs=[f"{body.access}/{name}"])
    return {"access": body.access, "name": name}


@router.put("/api/docs/{access}/{name}")
def save_doc(access: str, name: str, body: SaveDocIn, user=Depends(require_hr),
             audit=audited("doc.update")):
    ws = ws_of(user)
    _existing_path(ws, access, name)
    if not name.lower().endswith(".txt"):
        raise HTTPException(400, "Only .txt documents can be edited here.")
    docs_store.save(access, name, body.content.encode("utf-8"), workspaces.docs_root(ws))
    audit.record("doc", f"{access}/{name}", {"size": len(body.content.encode("utf-8"))},
                 user=user)
    indexing.schedule_index(ws, docs=[f"{access}/{name}"])
    return {"ok": True}


@router.delete("/api/docs/{access}/{name}")
def delete_doc(access: str, name: str, user=Depends(require_hr), audit=audited("doc.delete")):
    ws = ws_of(user)
    os.remove(_existing_path(ws, access, name))
    audit.record("doc", f"{access}/{name}", user=user)
    indexing.schedule_index(ws, docs=[f"{access}/{name}"])
    return {"ok": True}


@router.post("/api/docs/upload")
def upload_docs(files: list[UploadFile] = File(...), access: str = Form("public"),
                user=Depends(require_hr), audit=audited("doc.upload")):
    """The files arrive already on disk (Starlette spools big ones to temporary
    files), with the whole request capped by middleware.BodyLimit. Each is checked
    (size, and that it is what its name says) before any is saved."""
    user_limit(user, "upload")
    ws = ws_of(user)
    if access not in docs_store.ACCESS_LEVELS:
        raise HTTPException(400, "Access must be public or restricted.")
    max_mb = env_int("UPLOAD_MAX_FILE_MB", UPLOAD_MAX_FILE_MB)
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
            ext = os.path.splitext(name)[1]
            kind = {".pdf": "a PDF", ".docx": "a Word", ".pptx": "a PowerPoint",
                    ".xlsx": "an Excel"}.get(ext, "a plain text")
            raise HTTPException(415, f"{f.filename} isn't {kind} file, though its name says "
                                     "so. Nothing was uploaded.")
        picked.append((name, f))
    if not picked:
        raise HTTPException(400, "Please choose at least one text, PDF, Word, PowerPoint, "
                                 "Excel, Markdown or HTML file.")
    for name, f in picked:
        docs_store.save_file(access, name, f.file, workspaces.docs_root(ws))
    audit.record("doc", access, {"files": [name for name, _ in picked]}, user=user)
    indexing.schedule_index(ws, docs=[f"{access}/{name}" for name, _ in picked])
    return {"saved": [name for name, _ in picked]}
