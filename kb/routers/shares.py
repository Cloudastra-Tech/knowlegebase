"""Links that share a chat or one answer with the rest of the workspace (C16). The
checks made on every open are in kb/services/shares.py."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

import conversations_store as store
from kb.audit import audited
from kb.deps import current_role, current_user, ws_of
from kb.services import conversations, shares

router = APIRouter()

TOKEN = Path(pattern=r"^[A-Za-z0-9_-]{20,100}$")


class ShareIn(BaseModel):
    conversation_id: str
    message_id: str | None = None        # one answer; None: the whole chat as shown now
    expires_in_days: int | None = 7      # 1, 7 or 30; None: as long as the chat is kept


def _link(share, token=None):
    out = {"id": share["id"], "kind": "answer" if share["message_id"] else "chat",
           "message_id": share["message_id"], "created_at": share["created_at"],
           "expires_at": share["expires_at"]}
    if token:                              # shown once: only its hash is kept
        out.update(token=token, url=f"/s/{token}")
    return out


@router.post("/api/shares")
def create_share(body: ShareIn, user=Depends(current_user), role=Depends(current_role),
                 audit=audited("share.create")):
    """A link to one of the asker's chats, or to one answer in it, for anyone in the
    workspace. Returns the link once, and whether some of what it shares is
    restricted (only HR / Admin will be able to read those answers)."""
    ws, me = ws_of(user), user["username"]
    if not store.chat_settings(ws)["sharing_enabled"]:
        raise HTTPException(403, "Your admin has turned off sharing chats.")
    conv = store.get(ws, me, body.conversation_id)
    if not conv or not conv["active_leaf_id"]:
        raise HTTPException(404, "That conversation doesn't exist.")
    leaf = conv["active_leaf_id"]
    if body.message_id:
        m = store.get_message(ws, me, body.message_id)
        if (not m or m["conversation_id"] != conv["id"] or m["author"] != "assistant"
                or conversations.present(m, role)["state"] in ("pending", "error")):
            raise HTTPException(400, "Only a finished answer in this chat can be shared.")
        leaf = m["id"]
    expires_at = shares.expiry(ws, body.expires_in_days)
    token, share = store.create_share(ws, conv["id"], me, role, leaf, body.message_id,
                                      expires_at)
    audit.record("share", share["id"], {"conversation": conv["id"], "kind": _link(share)["kind"],
                                        "expires_at": expires_at}, user=user)
    return {**_link(share, token),
            "restricted": shares.is_restricted(shares.shared_messages(share))}


@router.get("/api/shares")
def list_shares(conversation_id: str = Query(...), user=Depends(current_user)):
    """The links the asker made to one of their chats that still work."""
    return {"shares": [_link(s) for s in store.shares_of(ws_of(user), user["username"],
                                                         conversation_id)]}


@router.delete("/api/shares/{sid}")
def revoke_share(sid: str, user=Depends(current_user), audit=audited("share.revoke")):
    """The link stops working at once."""
    if not store.revoke_share(ws_of(user), user["username"], sid):
        raise HTTPException(404, "That link doesn't exist.")
    audit.record("share", sid, user=user)
    return {"ok": True}


@router.post("/api/shared/{token}/continue")
def continue_share(token: str = TOKEN, user=Depends(current_user), role=Depends(current_role),
                   audit=audited("share.continue")):
    """"Continue in my chats": a new chat of the viewer's own with a copy of the
    shared one, holding only what the link shows them now. A withheld answer is
    left out with its question; they can ask it themselves."""
    conv, skipped = shares.continue_share(token, user, role)
    audit.record("conversation", conv["id"], {"from_share": True, "skipped": skipped},
                 user=user)
    return {"id": conv["id"], "title": conv["title"] or "New chat", "skipped": skipped}


@router.get("/api/shared/{token}")
def open_share(token: str = TOKEN, user=Depends(current_user), role=Depends(current_role)):
    """A shared chat or answer, as the signed-in viewer may see it: an answer built
    from anything their role (or the sharer's, now) can't read is withheld whole."""
    return shares.open_share(token, user, role)
