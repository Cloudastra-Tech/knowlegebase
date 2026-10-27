"""Spaces (chat.md C31): a person's own space and the team spaces they're in, what
each searches (sources), who's in it, its instructions, and the files a person
keeps in their own. A chat is asked in one when it's made with its id
(POST /api/conversations {space_id}). Who may see and change what:
kb/services/spaces.py."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import attachments_store
import members_store
from kb.audit import audited
from kb.deps import current_user, current_viewer, ws_of
from kb.rate_limit import user_limit
from kb.services import attachments, spaces

router = APIRouter()


class SpaceIn(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""


class SpaceEdit(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None


class SourceIn(BaseModel):
    match_type: str                 # doc_key | folder | provider
    value: str


class MemberIn(BaseModel):
    role: str = "member"            # owner | member


@router.get("/api/spaces")
def list_spaces(user=Depends(current_user)):
    """The person's own space, then the team spaces they're in."""
    who = spaces.can_create_team()
    return {"spaces": spaces.list_for(ws_of(user), user),
            "can_create": who == "everyone" or user["role"] in members_store.STAFF_ROLES}


@router.post("/api/spaces")
def create_space(body: SpaceIn, user=Depends(current_user), audit=audited("space.create")):
    space = spaces.create_team(ws_of(user), user, body.name, body.description,
                               body.instructions)
    audit.record("space", space["id"], {"kind": "team"}, user=user)
    return spaces.summary(space, "owner")


@router.get("/api/spaces/source-options")
def source_options(user=Depends(current_user), viewer=Depends(current_viewer)):
    """What a space can be made of: the providers, folders and documents the
    asker may search (as the app is viewed now)."""
    return spaces.source_options(ws_of(user), viewer)


@router.get("/api/spaces/{space_id}")
def get_space(space_id: str, user=Depends(current_user), viewer=Depends(current_viewer)):
    return spaces.detail(ws_of(user), user, viewer, space_id)


@router.patch("/api/spaces/{space_id}")
def update_space(space_id: str, body: SpaceEdit, user=Depends(current_user),
                 audit=audited("space.update")):
    space = spaces.update(ws_of(user), user, space_id, body.name, body.description,
                          body.instructions)
    audit.record("space", space_id, {"changed": sorted(
        k for k, v in body.model_dump().items() if v is not None)}, user=user)
    return spaces.summary(space, "owner")


@router.delete("/api/spaces/{space_id}")
def delete_space(space_id: str, user=Depends(current_user), audit=audited("space.delete")):
    spaces.delete(ws_of(user), user, space_id)
    audit.record("space", space_id, user=user)
    return {"ok": True}


@router.post("/api/spaces/{space_id}/sources")
def add_space_source(space_id: str, body: SourceIn, user=Depends(current_user),
                     viewer=Depends(current_viewer), audit=audited("space.source_add")):
    source = spaces.add_source(ws_of(user), user, viewer, space_id, body.match_type,
                               body.value)
    audit.record("space", space_id, {"match_type": body.match_type}, user=user)
    return source


@router.delete("/api/spaces/{space_id}/sources/{source_id}")
def remove_space_source(space_id: str, source_id: int, user=Depends(current_user),
                        audit=audited("space.source_remove")):
    spaces.remove_source(ws_of(user), user, space_id, source_id)
    audit.record("space", space_id, {"source_id": source_id}, user=user)
    return {"ok": True}


@router.put("/api/spaces/{space_id}/members/{username}")
def set_space_member(space_id: str, username: str, body: MemberIn, user=Depends(current_user),
                     audit=audited("space.member_set")):
    spaces.set_member(ws_of(user), user, space_id, username, body.role)
    audit.record("space", space_id, {"member": username.strip().lower(), "role": body.role},
                 user=user)
    return {"ok": True}


@router.delete("/api/spaces/{space_id}/members/{username}")
def remove_space_member(space_id: str, username: str, user=Depends(current_user),
                        audit=audited("space.member_remove")):
    spaces.remove_member(ws_of(user), user, space_id, username)
    audit.record("space", space_id, {"member": username.strip().lower()}, user=user)
    return {"ok": True}


# ---- A personal space's files ---------------------------------------------------------
def _personal(user, space_id):
    space = spaces.owned(ws_of(user), user, space_id)
    if space["kind"] != "personal":
        raise HTTPException(400, "Files are kept in your own space. A team space searches the "
                                 "workspace's documents.")
    return space


@router.post("/api/spaces/{space_id}/files")
async def add_space_files(space_id: str, files: list[UploadFile] = File(...),
                          user=Depends(current_user), audit=audited("space.file_add")):
    """Files only their owner's questions in this space read (never the index)."""
    user_limit(user, "upload")
    await run_in_threadpool(_personal, user, space_id)
    added = await attach_files(user, files, space_id=space_id)
    audit.record("space", space_id, {"files": len(added),
                                     "kinds": sorted({a["kind"] for a in added})}, user=user)
    return {"files": added}


@router.delete("/api/spaces/{space_id}/files/{attachment_id}")
def remove_space_file(space_id: str, attachment_id: str, user=Depends(current_user),
                      audit=audited("space.file_remove")):
    _personal(user, space_id)
    row = attachments_store.get(ws_of(user), user["username"], attachment_id)
    if not row or row["space_id"] != space_id:
        raise HTTPException(404, "That file isn't in your space.")
    attachments_store.delete(ws_of(user), user["username"], attachment_id)
    audit.record("space", space_id, {"removed": 1}, user=user)
    return {"ok": True}


async def attach_files(user, files, **where):
    """The uploaded files read and kept (kb/services/attachments.attach_all)."""
    read = [(f.filename or "file", f.content_type or "", await f.read()) for f in files]
    try:
        return await run_in_threadpool(attachments.attach_all, ws_of(user), user, read, **where)
    except attachments.Refused as e:
        raise HTTPException(e.status, str(e))
