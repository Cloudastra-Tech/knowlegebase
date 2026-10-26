"""Saved chats: a person's own conversations, and the thumbs up / down they give
answers. Questions and answers are added by the chat stream (kb/routers/chat.py,
with a conversation_id). Every read checks the reader's CURRENT role against what
each answer was built from (kb/services/conversations.visible_to)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import conversations_store as store
from kb.audit import audited
from kb.deps import current_role, current_user, require_hr, ws_of
from kb.services import conversations

router = APIRouter()

COMMENT_CHARS = 1000


class NewConversationIn(BaseModel):
    title: str | None = None


class ConversationEdit(BaseModel):
    title: str


class FeedbackIn(BaseModel):
    rating: int                      # 1 (thumbs up) or -1 (thumbs down)
    reasons: list[str] = []          # thumbs down: why (conversations_store.REASONS)
    comment: str | None = None


def _summary(conv, role):
    return {"id": conv["id"], "title": conv["title"] or "New chat",
            "role_used": conv["role_used"], "role_changed": conv["role_used"] != role,
            "created_at": conv["created_at"], "updated_at": conv["updated_at"]}


def _own(user, cid):
    conv = store.get(ws_of(user), user["username"], cid)
    if not conv:
        raise HTTPException(404, "That conversation doesn't exist.")
    return conv


@router.get("/api/conversations")
def list_conversations(limit: int = Query(50, ge=1, le=200), before: float | None = None,
                       user=Depends(current_user), role=Depends(current_role)):
    """The asker's own conversations, most recently used first (empty ones left out)."""
    convs = store.list_for(ws_of(user), user["username"], limit=limit, before=before)
    return {"conversations": [_summary(c, role) for c in convs]}


@router.post("/api/conversations")
def create_conversation(body: NewConversationIn, user=Depends(current_user),
                        role=Depends(current_role), audit=audited("conversation.create")):
    """A new, empty conversation under the role the app is viewed as now."""
    title = " ".join((body.title or "").split())[:store.TITLE_CHARS] or None
    conv = store.create(ws_of(user), user["username"], role, title)
    audit.record("conversation", conv["id"], {"role": role}, user=user)
    return _summary(conv, role)


@router.get("/api/conversations/{cid}")
def get_conversation(cid: str, user=Depends(current_user), role=Depends(current_role)):
    """A conversation as shown: its messages from the first to the current one. An
    answer built from documents the reader's role can't see now is withheld."""
    conv = _own(user, cid)
    path = store.path(cid, conv["active_leaf_id"])
    feedback = store.feedback_of(user["username"], [m["id"] for m in path])
    admin = user["role"] == "admin"
    return {**_summary(conv, role),
            "messages": [conversations.present(m, role, admin, feedback.get(m["id"]))
                         for m in path]}


@router.patch("/api/conversations/{cid}")
def rename_conversation(cid: str, body: ConversationEdit, user=Depends(current_user),
                        role=Depends(current_role), audit=audited("conversation.rename")):
    title = " ".join(body.title.split())[:store.TITLE_CHARS]
    if not title:
        raise HTTPException(400, "Please give the conversation a name.")
    _own(user, cid)
    store.rename(ws_of(user), user["username"], cid, title)
    audit.record("conversation", cid, user=user)          # not the title: the asker's words
    return _summary(_own(user, cid), role)


@router.delete("/api/conversations/{cid}")
def delete_conversation(cid: str, user=Depends(current_user),
                        audit=audited("conversation.delete")):
    """Deletes it for good, with its messages and the feedback on them."""
    if not store.delete(ws_of(user), user["username"], cid):
        raise HTTPException(404, "That conversation doesn't exist.")
    audit.record("conversation", cid, user=user)
    return {"ok": True}


def _answer(user, role, mid):
    """An answer in one of the asker's conversations that they may read now."""
    m = store.get_message(ws_of(user), user["username"], mid)
    if not m or m["author"] != "assistant" or m["state"] is None:
        raise HTTPException(404, "That answer doesn't exist.")
    if not conversations.visible_to(role, m):
        raise HTTPException(403, "That answer used documents you can't see any more.")
    return m


@router.put("/api/messages/{mid}/feedback")
def give_feedback(mid: str, body: FeedbackIn, user=Depends(current_user),
                  role=Depends(current_role), audit=audited("feedback.set")):
    """Thumbs up or down on an answer, with reasons and a note for thumbs down. HR and
    admins see it with the question and answer (GET /api/admin/feedback)."""
    m = _answer(user, role, mid)
    if body.rating not in (1, -1):
        raise HTTPException(400, "A rating is 1 (helpful) or -1 (not helpful).")
    reasons = list(dict.fromkeys(body.reasons)) if body.rating == -1 else []
    if any(r not in store.REASONS for r in reasons):
        raise HTTPException(400, "Unknown reason. Choose from: " + ", ".join(store.REASONS))
    comment = (body.comment or "").strip()[:COMMENT_CHARS] or None
    question = store.get_message(ws_of(user), user["username"], m["parent_id"])
    snapshot = {"question": question["content"] if question else None,
                "standalone_question": m["standalone_question"], "answer": m["content"],
                "state": m["state"], "sources": m["sources"], "max_access": m["max_access"]}
    store.set_feedback(mid, user["username"], ws_of(user), body.rating, reasons, comment,
                       snapshot)
    audit.record("message", mid, {"rating": body.rating, "reasons": reasons}, user=user)
    return {"rating": body.rating, "reasons": reasons, "comment": comment}


@router.delete("/api/messages/{mid}/feedback")
def remove_feedback(mid: str, user=Depends(current_user), role=Depends(current_role),
                    audit=audited("feedback.delete")):
    _answer(user, role, mid)
    store.delete_feedback(mid, user["username"])
    audit.record("message", mid, user=user)
    return {"ok": True}


@router.get("/api/admin/feedback")
def list_feedback(rating: int | None = Query(None), limit: int = Query(100, ge=1, le=500),
                  user=Depends(require_hr), role=Depends(current_role)):
    """The workspace's answer feedback, newest first, for HR and admins (viewing the
    app as HR): the question, the answer and its sources as they were rated."""
    items = []
    for f in store.list_feedback(ws_of(user), rating=rating, limit=limit):
        snap = f["snapshot"]
        if not conversations.visible_to(role, {"author": "assistant", **snap}):
            continue
        items.append({"message_id": f["message_id"], "user": f["user"],
                      "rating": f["rating"], "reasons": f["reasons"], "comment": f["comment"],
                      "question": snap.get("question"),
                      "standalone_question": snap.get("standalone_question"),
                      "answer": snap.get("answer"), "state": snap.get("state"),
                      "sources": snap.get("sources") or [],
                      "created_at": f["created_at"], "updated_at": f["updated_at"]})
    return {"feedback": items}
