"""Files attached to a chat (chat.md C23): read into text, kept with the chat for
its owner only (never the workspace index), cited as "Attached file" passages,
and deleted with the chat or after ATTACHMENT_RETENTION_DAYS. Reading,
splitting and searching them: kb/services/attachments.py."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

import attachments_store as store
import conversations_store
from kb.audit import audited
from kb.deps import current_user, ws_of
from kb.rate_limit import user_limit
from kb.routers.spaces import attach_files
from kb.services import attachments

router = APIRouter()


def _own_conversation(user, cid):
    if not conversations_store.get(ws_of(user), user["username"], cid):
        raise HTTPException(404, "That conversation doesn't exist.")


@router.get("/api/attachments/limits")
def attachment_limits(user=Depends(current_user)):
    """What may be attached: the file types, the size cap (the upload cap) and how
    many per chat; images only when a model that reads them is set."""
    return attachments.limits()


@router.get("/api/conversations/{cid}/attachments")
def list_attachments(cid: str, user=Depends(current_user)):
    _own_conversation(user, cid)
    return {"attachments": [attachments.view(r) for r in
                            store.of_conversation(ws_of(user), user["username"], cid)]}


@router.post("/api/conversations/{cid}/attachments")
async def add_attachments(cid: str, files: list[UploadFile] = File(...),
                          user=Depends(current_user), audit=audited("attachment.add")):
    """Attach files to the chat: each is checked (type sniffed, size, readable)
    before any is kept."""
    user_limit(user, "upload")
    await run_in_threadpool(_own_conversation, user, cid)
    added = await attach_files(user, files, conversation_id=cid)
    # How many and what kind, never the file names: they can say what the files hold.
    audit.record("conversation", cid, {"files": len(added),
                                       "kinds": sorted({a["kind"] for a in added})}, user=user)
    return {"attachments": added}


@router.delete("/api/conversations/{cid}/attachments/{attachment_id}")
def remove_attachment(cid: str, attachment_id: str, user=Depends(current_user),
                      audit=audited("attachment.remove")):
    _own_conversation(user, cid)
    row = store.get(ws_of(user), user["username"], attachment_id)
    if not row or row["conversation_id"] != cid:
        raise HTTPException(404, "That file isn't attached to this chat.")
    store.delete(ws_of(user), user["username"], attachment_id)
    audit.record("conversation", cid, {"removed": 1}, user=user)
    return {"ok": True}
