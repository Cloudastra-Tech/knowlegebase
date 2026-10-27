"""Drafts written by the assistant (platform.md PL-31; kb/services/drafts.py), for
HR / Admin: ask for one (a source's or folder's summary, a doc for a content
gap), review and edit it, then publish it as a document, or delete it. Each of
those is audited; a draft is never published by itself."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import drafts_store
import integrations_store
from kb.audit import audited
from kb.deps import current_role, require_hr, ws_of
from kb.rate_limit import user_limit
from kb.services import access, drafts

router = APIRouter()


class SummariseIn(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    folder: str | None = Field(default=None, max_length=300)


class GapIn(BaseModel):
    gap_id: int


class DraftPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=drafts_store.MAX_CHARS)


class PublishIn(BaseModel):
    access: str
    name: str = Field(min_length=1, max_length=200)


def _draft(user, draft_id):
    draft = drafts_store.get(ws_of(user), draft_id)
    if not draft or not drafts.may_see(user, draft):
        raise HTTPException(404, "No such draft.")
    return draft


@router.get("/api/drafts")
def list_drafts(user=Depends(require_hr)):
    ws = ws_of(user)
    return {"drafts": [drafts.view(d) for d in drafts_store.list_drafts(ws)
                       if drafts.may_see(user, d)]}


@router.get("/api/drafts/{draft_id}")
def read_draft(draft_id: str, user=Depends(require_hr)):
    draft = _draft(user, draft_id)
    return drafts.view(draft, drafts_store.read_text(draft["workspace"], draft_id))


@router.post("/api/drafts/summarise")
def summarise(body: SummariseIn, user=Depends(require_hr), audit=audited("draft.summarise")):
    """Summarise a source (provider) or one of its folders: a draft, written by a job."""
    ws = ws_of(user)
    if body.provider != access.UPLOADS and body.provider not in \
            integrations_store.chunk_access_levels(ws):
        raise HTTPException(404, "This workspace has no such source.")
    if not drafts.scope_docs(ws, user, body.provider, body.folder):
        raise HTTPException(400, "There are no documents here you can read.")
    user_limit(user, "draft")
    draft = drafts.start_summary(ws, user, body.provider, body.folder)
    audit.record("draft", draft["id"], {"kind": "summary", "provider": body.provider,
                                        "folder": body.folder}, user=user)
    return drafts.view(draft)


@router.post("/api/drafts/from-gap")
def from_gap(body: GapIn, user=Depends(require_hr), role=Depends(current_role),
             audit=audited("draft.from_gap")):
    """Draft a document for a content gap (Insights)."""
    ws = ws_of(user)
    gap = drafts.find_gap(ws, role, body.gap_id)
    if not gap:
        raise HTTPException(404, "No such content gap.")
    user_limit(user, "draft")
    draft = drafts.start_gap(ws, user, gap)
    audit.record("draft", draft["id"], {"kind": "gap", "gap_id": body.gap_id}, user=user)
    return drafts.view(draft)


@router.patch("/api/drafts/{draft_id}")
def edit_draft(draft_id: str, body: DraftPatch, user=Depends(require_hr),
               audit=audited("draft.update")):
    draft = _draft(user, draft_id)
    if draft["status"] != "draft":
        raise HTTPException(409, "Only a finished draft can be edited.")
    ws = draft["workspace"]
    if body.content is not None:
        drafts_store.write_text(ws, draft_id, body.content)
    fields = {"title": " ".join(body.title.split())} if body.title and body.title.strip() else {}
    draft = drafts_store.update(ws, draft_id, **fields)
    audit.record("draft", draft_id, {"title": bool(fields), "content": body.content is not None,
                                     "size": len(body.content or "")}, user=user)
    return drafts.view(draft)


@router.post("/api/drafts/{draft_id}/publish")
def publish_draft(draft_id: str, body: PublishIn, user=Depends(require_hr),
                  audit=audited("draft.publish")):
    """Publish it as a document at an access level no lower than its sources'."""
    draft = _draft(user, draft_id)
    try:
        draft = drafts.publish(draft["workspace"], user, draft, body.access, body.name)
    except drafts.PublishError as e:
        raise HTTPException(e.status, e.message)
    audit.record("draft", draft_id, {"published_as": draft["published_as"],
                                     "sources": len(draft["sources"])}, user=user)
    return drafts.view(draft)


@router.delete("/api/drafts/{draft_id}")
def delete_draft(draft_id: str, user=Depends(require_hr), audit=audited("draft.delete")):
    draft = _draft(user, draft_id)
    drafts_store.delete(draft["workspace"], draft_id)
    audit.record("draft", draft_id, {"kind": draft["kind"], "status": draft["status"]},
                 user=user)
    return {"ok": True}
