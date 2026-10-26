"""Saved chats: a person's own conversations, their folders, searching them, the
thumbs up / down they give answers, and the workspace's chat settings (how long
chats are kept). Questions and answers are added by the chat stream
(kb/routers/chat.py, with a conversation_id). Every read checks the reader's
CURRENT role against what each answer was built from
(kb/services/conversations.visible_to)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import conversations_store as store
from kb.audit import audited
from kb.deps import current_role, current_user, require_admin, require_hr, ws_of
from kb.services import conversations
from rag import ROLE_ACCESS

router = APIRouter()

COMMENT_CHARS = 1000


class NewConversationIn(BaseModel):
    title: str | None = None


class ConversationEdit(BaseModel):
    """Any of: a new name, pinned or not, a folder (null: none), or the message
    whose branch to show (an edit or a regenerated answer, C10)."""
    title: str | None = None
    pinned: bool | None = None
    folder_id: str | None = None
    active_leaf_id: str | None = None


class ForkIn(BaseModel):
    message_id: str


class FolderIn(BaseModel):
    name: str


class FeedbackIn(BaseModel):
    rating: int                      # 1 (thumbs up) or -1 (thumbs down)
    reasons: list[str] = []          # thumbs down: why (conversations_store.REASONS)
    comment: str | None = None


class ChatSettingsIn(BaseModel):
    retention_days: int | None = None    # 30, 90 or 365; null: kept until deleted
    sharing_enabled: bool = True


def _summary(conv, role):
    return {"id": conv["id"], "title": conv["title"] or "New chat",
            "role_used": conv["role_used"], "role_changed": conv["role_used"] != role,
            "pinned": conv.get("pinned_at") is not None, "folder_id": conv.get("folder_id"),
            "created_at": conv["created_at"], "updated_at": conv["updated_at"]}


def _own(user, cid):
    conv = store.get(ws_of(user), user["username"], cid)
    if not conv:
        raise HTTPException(404, "That conversation doesn't exist.")
    return conv


def _name(text, limit, what):
    name = " ".join((text or "").split())[:limit]
    if not name:
        raise HTTPException(400, f"Please give the {what} a name.")
    return name


@router.get("/api/conversations")
def list_conversations(limit: int = Query(50, ge=1, le=200), before: float | None = None,
                       user=Depends(current_user), role=Depends(current_role)):
    """The asker's own conversations, most recently used first (empty ones left out),
    and their folders."""
    convs = store.list_for(ws_of(user), user["username"], limit=limit, before=before)
    return {"conversations": [_summary(c, role) for c in convs],
            "folders": store.folders(ws_of(user), user["username"])}


@router.get("/api/conversations/search")
def search_conversations(q: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=50),
                         user=Depends(current_user), role=Depends(current_role)):
    """The asker's chats whose title, questions or answers match `q`. Answers their
    role can't read now never match (a demoted HR person can't find old HR
    answers this way). Each result's snippet marks the matches with \\u0002 ... \\u0003."""
    found = store.search(ws_of(user), user["username"], ROLE_ACCESS.get(role, ["public"]), q,
                         limit)
    return {"results": [{"conversation_id": r["conversation_id"], "message_id": r["message_id"],
                         "title": r["title"] or "New chat", "snippet": r["snippet"],
                         "updated_at": r["updated_at"]} for r in found]}


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
    """A conversation as shown: its messages from the first to the current one, each
    with its versions (siblings). An answer built from documents the reader's role
    can't see now is withheld."""
    conv = _own(user, cid)
    messages = store.messages_of(cid)
    feedback = store.feedback_of(user["username"], [m["id"] for m in messages])
    admin = user["role"] == "admin"
    return {**_summary(conv, role),
            "forked_from": conversations.forked_from(ws_of(user), user["username"], conv),
            "messages": conversations.view(conv, messages, conv["active_leaf_id"], role, admin,
                                           feedback)}


@router.patch("/api/conversations/{cid}")
def edit_conversation(cid: str, body: ConversationEdit, user=Depends(current_user),
                      role=Depends(current_role), audit=audited("conversation.rename")):
    """Rename, pin, move to a folder, or show another branch (which isn't logged:
    it changes only what its owner looks at)."""
    ws, me = ws_of(user), user["username"]
    _own(user, cid)
    given = body.model_fields_set
    if "title" in given:
        store.rename(ws, me, cid, _name(body.title, store.TITLE_CHARS, "conversation"))
    if "folder_id" in given and body.folder_id and not store.folder(ws, me, body.folder_id):
        raise HTTPException(404, "That folder doesn't exist.")
    if "pinned" in given or "folder_id" in given:
        store.organize(ws, me, cid, pinned=body.pinned if "pinned" in given else None,
                       folder_id=body.folder_id if "folder_id" in given else False)
    if body.active_leaf_id and not store.show_branch(ws, me, cid, body.active_leaf_id):
        raise HTTPException(404, "That message isn't in this conversation.")
    if "title" in given:           # not the title itself: the asker's words
        audit.record("conversation", cid, user=user)
    organized = sorted(given & {"pinned", "folder_id"})
    if organized:
        audit.record("conversation", cid, {k: getattr(body, k) for k in organized}, user=user,
                     action="conversation.organize")
    return _summary(_own(user, cid), role)


@router.delete("/api/conversations/{cid}")
def delete_conversation(cid: str, user=Depends(current_user),
                        audit=audited("conversation.delete")):
    """Deletes it for good, with its messages, the feedback on them and its links."""
    if not store.delete(ws_of(user), user["username"], cid):
        raise HTTPException(404, "That conversation doesn't exist.")
    audit.record("conversation", cid, user=user)
    return {"ok": True}


@router.post("/api/conversations/{cid}/fork")
def fork_conversation(cid: str, body: ForkIn, user=Depends(current_user),
                      role=Depends(current_role), audit=audited("conversation.fork")):
    """"Branch in new chat" (C11): a new chat with a copy of this one up to the
    answer `message_id`."""
    m = store.get_message(ws_of(user), user["username"], body.message_id)
    if (not m or m["conversation_id"] != cid or m["author"] != "assistant"
            or conversations.present(m, role)["state"] == "pending"):
        raise HTTPException(400, "Only a finished answer in this chat can start a new chat.")
    conv = store.fork(ws_of(user), user["username"], cid, body.message_id)
    if not conv:
        raise HTTPException(404, "That conversation doesn't exist.")
    audit.record("conversation", conv["id"], {"forked_from": cid}, user=user)
    return _summary(conv, role)


# ---- Folders --------------------------------------------------------------------------
@router.get("/api/conversation-folders")
def list_folders(user=Depends(current_user)):
    return {"folders": store.folders(ws_of(user), user["username"])}


@router.post("/api/conversation-folders")
def create_folder(body: FolderIn, user=Depends(current_user),
                  audit=audited("conversation_folder.create")):
    folder = store.create_folder(ws_of(user), user["username"],
                                 _name(body.name, store.FOLDER_CHARS, "folder"))
    audit.record("conversation_folder", folder["id"], user=user)
    return folder


@router.patch("/api/conversation-folders/{fid}")
def rename_folder(fid: str, body: FolderIn, user=Depends(current_user),
                  audit=audited("conversation_folder.rename")):
    if not store.rename_folder(ws_of(user), user["username"], fid,
                               _name(body.name, store.FOLDER_CHARS, "folder")):
        raise HTTPException(404, "That folder doesn't exist.")
    audit.record("conversation_folder", fid, user=user)
    return store.folder(ws_of(user), user["username"], fid)


@router.delete("/api/conversation-folders/{fid}")
def delete_folder(fid: str, user=Depends(current_user),
                  audit=audited("conversation_folder.delete")):
    """The folder goes; its chats stay, in no folder."""
    if not store.delete_folder(ws_of(user), user["username"], fid):
        raise HTTPException(404, "That folder doesn't exist.")
    audit.record("conversation_folder", fid, user=user)
    return {"ok": True}


# ---- Answers --------------------------------------------------------------------------
def _answer(user, role, mid):
    """An answer in one of the asker's conversations that they may read now."""
    m = store.get_message(ws_of(user), user["username"], mid)
    if not m or m["author"] != "assistant" or m["state"] is None:
        raise HTTPException(404, "That answer doesn't exist.")
    if not conversations.visible_to(role, m):
        raise HTTPException(403, "That answer used documents you can't see any more.")
    return m


@router.get("/api/messages/{mid}/markdown")
def answer_markdown(mid: str, user=Depends(current_user), role=Depends(current_role)):
    """An answer as markdown with its citations as footnotes, for Copy (C25)."""
    return {"markdown": conversations.markdown_of(_answer(user, role, mid))}


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


# ---- Chat settings (C12) --------------------------------------------------------------
def _settings_view(ws):
    s = store.chat_settings(ws)
    return {**s, "retention_choices": list(store.RETENTION_CHOICES)}


@router.get("/api/chat-settings")
def chat_settings(user=Depends(current_user)):
    """How long this workspace keeps chats (null: until deleted) and whether they may
    be shared: everyone sees it ("Chats are kept for 1 year")."""
    return _settings_view(ws_of(user))


@router.put("/api/admin/chat-settings")
def set_chat_settings(body: ChatSettingsIn, user=Depends(require_admin),
                      audit=audited("chat_settings.update")):
    """Admins: keep chats 30, 90 or 365 days after they were last used, or until
    deleted; and allow sharing links or not (off: every link stops working)."""
    if body.retention_days not in store.RETENTION_CHOICES:
        raise HTTPException(400, "Keep chats for 30, 90 or 365 days, or until deleted.")
    before = store.chat_settings(ws_of(user))
    store.set_chat_settings(ws_of(user), body.retention_days, body.sharing_enabled,
                            user["username"])
    audit.record("workspace", ws_of(user), {
        "retention_days": [before["retention_days"], body.retention_days],
        "sharing_enabled": [before["sharing_enabled"], body.sharing_enabled]}, user=user)
    return _settings_view(ws_of(user))
