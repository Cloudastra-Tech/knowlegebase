"""Documents (HR / Admin): the workspace's uploaded files (docs_store.py). Every
change queues a re-index of the documents it touched.

WHO MAY SEE an upload (its ACL, access.py): everyone, groups and members picked
when it's added or later (PUT .../acl), kept in doc_acl (groups_store.py). Its
folder follows: public/ when everyone may see it, else restricted/. With no ACL
picked, the folder decides, as before groups: public/ everyone, restricted/ the
hr group. Changing the ACL re-tags its chunks; nothing is embedded again. The
screen lists every upload (it's the HR / Admin screen), but a document's text
is only sent to someone its ACL names."""

import json
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import access as acl_check        # access.py (the routes have an `access` of their own)
import docs_store
import groups_store
import workspaces
from kb.audit import audited
from kb.deps import current_viewer, require_hr, ws_of
from kb.rate_limit import user_limit
from kb.services import indexing, permissions
from kb.settings import UPLOAD_MAX_FILE_MB, UPLOAD_MAX_REQUEST_MB, env_int

router = APIRouter()


class NewDocIn(BaseModel):
    name: str
    access: str
    content: str
    acl: list[str] | None = Field(None, max_length=200)   # principals; None: the folder's


class SaveDocIn(BaseModel):
    content: str


class AclIn(BaseModel):
    acl: list[str] = Field(max_length=200)


def _existing_path(ws, access, name):
    path = docs_store.doc_path(access, name, workspaces.docs_root(ws))
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Document not found.")
    return path


def _picked(ws, acl):
    """The ACL picked on the screen, checked (400 when it names someone unknown)."""
    try:
        return permissions.check_upload_acl(ws, acl)
    except groups_store.Invalid as e:
        raise HTTPException(400, str(e))


def _folder(acl):
    return "public" if acl_check.EVERYONE in acl else "restricted"


def _keep_acl(ws, key, acl, user):
    """Store a document's ACL: none when it's just its folder's default."""
    folder = key.split("/", 1)[0]
    same = acl is None or acl == acl_check.level_acl(folder)
    groups_store.set_doc_acl(ws, key, None if same else acl, user["username"])


def _readable(ws, key, viewer):
    if not acl_check.can_see(viewer, permissions.upload_acl(ws, key)):
        raise HTTPException(403, "Only the people this document is shared with can open it.")


@router.get("/api/docs")
def list_docs(user=Depends(require_hr), viewer=Depends(current_viewer)):
    """Every upload, with who may see it (acl, who) and whether the asker may open
    it themselves (readable)."""
    ws = ws_of(user)
    root = workspaces.docs_root(ws)
    picked, names = groups_store.all_doc_acls(ws), permissions.names(ws)
    docs = []
    for d in docs_store.list_docs(root):
        key = f"{d['access']}/{d['name']}"
        acl = picked.get(key) or acl_check.level_acl(d["access"])
        docs.append({**{k: d[k] for k in ("access", "name", "size")}, "acl": acl,
                     "who": permissions.labels(ws, acl, names),
                     "readable": acl_check.can_see(viewer, acl)})
    return {"docs": docs, "upload_limits": _upload_limits()}


@router.put("/api/docs/{access}/{name}/acl")
def set_doc_acl(access: str, name: str, body: AclIn, user=Depends(require_hr),
                audit=audited("doc.acl")):
    """Who may see an uploaded document. Its chunks are re-tagged (no embedding)."""
    ws = ws_of(user)
    _existing_path(ws, access, name)
    key = f"{access}/{name}"
    acl = _picked(ws, body.acl)
    before = permissions.upload_acl(ws, key)
    _keep_acl(ws, key, acl, user)
    audit.record("doc", key, {"acl": [before, acl]}, user=user)
    indexing.schedule_index(ws, docs=[key])
    return {"acl": acl, "who": permissions.labels(ws, acl)}


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
def read_doc(access: str, name: str, user=Depends(require_hr), viewer=Depends(current_viewer)):
    path = _existing_path(ws_of(user), access, name)
    _readable(ws_of(user), f"{access}/{name}", viewer)
    ext = os.path.splitext(name.lower())[1]
    if ext in _FILE_TYPES:            # an uploaded page is never served as HTML here
        return FileResponse(path, media_type=_FILE_TYPES[ext], filename=name)
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"access": access, "name": name, "content": f.read()}


@router.post("/api/docs")
def create_doc(body: NewDocIn, user=Depends(require_hr), audit=audited("doc.create")):
    """A new text document, for everyone (public), HR / Admin (restricted), or the
    groups and members picked (acl: its folder then follows from it)."""
    ws = ws_of(user)
    root = workspaces.docs_root(ws)
    name = docs_store.safe_name(body.name)
    acl = _picked(ws, body.acl) if body.acl is not None else None
    folder = _folder(acl) if acl else body.access
    if folder not in docs_store.ACCESS_LEVELS:
        raise HTTPException(400, "Access must be public or restricted.")
    if not name or not body.content.strip():
        raise HTTPException(400, "Please give a file name and some content.")
    if os.path.exists(docs_store.doc_path(folder, name, root)):
        raise HTTPException(409, f"{name} already exists. Edit it instead.")
    docs_store.save(folder, name, body.content.encode("utf-8"), root)
    _keep_acl(ws, f"{folder}/{name}", acl, user)
    audit.record("doc", f"{folder}/{name}", {"size": len(body.content.encode("utf-8")),
                                             **({"acl": acl} if acl else {})}, user=user)
    indexing.schedule_index(ws, docs=[f"{folder}/{name}"])
    return {"access": folder, "name": name}


@router.put("/api/docs/{access}/{name}")
def save_doc(access: str, name: str, body: SaveDocIn, user=Depends(require_hr),
             viewer=Depends(current_viewer), audit=audited("doc.update")):
    ws = ws_of(user)
    _existing_path(ws, access, name)
    _readable(ws, f"{access}/{name}", viewer)
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
    groups_store.set_doc_acl(ws, f"{access}/{name}", None, user["username"])
    audit.record("doc", f"{access}/{name}", user=user)
    indexing.schedule_index(ws, docs=[f"{access}/{name}"])
    return {"ok": True}


@router.post("/api/docs/upload")
def upload_docs(files: list[UploadFile] = File(...), access: str = Form("public"),
                acl: str | None = Form(None), user=Depends(require_hr),
                audit=audited("doc.upload")):
    """The files arrive already on disk (Starlette spools big ones to temporary
    files), with the whole request capped by middleware.BodyLimit. Each is checked
    (size, and that it is what its name says) before any is saved.
    acl: who may see them, a JSON list of principals (everyone, group:<id>,
    user:<username>); the folder then follows from it. Without one, `access`."""
    user_limit(user, "upload")
    ws = ws_of(user)
    picked_acl = None
    if acl:
        try:
            principals = json.loads(acl)
        except ValueError:
            principals = None
        if not isinstance(principals, list):
            raise HTTPException(400, "acl is a JSON list of who may see the files.")
        picked_acl = _picked(ws, principals)
        access = _folder(picked_acl)
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
        _keep_acl(ws, f"{access}/{name}", picked_acl, user)
    audit.record("doc", access, {"files": [name for name, _ in picked],
                                 **({"acl": picked_acl} if picked_acl else {})}, user=user)
    indexing.schedule_index(ws, docs=[f"{access}/{name}" for name, _ in picked])
    return {"saved": [name for name, _ in picked]}
