"""
Sharing a chat, or one answer, by link (chat.md C16), without leaking what the
viewer may not read.

A link is for members of the sharer's workspace only, expires (never later than
the workspace's chat retention) and can be revoked; only its token's sha256 is
stored. Every time it's opened:

  - the viewer must be signed in to the same workspace (anyone else: 404);
  - sharing must still be on for the workspace, the link not revoked or expired,
    and the person who made it still a member;
  - each answer is checked with visible_to() against BOTH the viewer and the
    sharer, each with their principals now (groups too: access.py) at the lower
    of their role now and the role it was shared as. A link can never show more
    than its sharer may see now (a sharer moved out of HR, or out of a group,
    stops sharing its answers), nor more than the viewer may.

An answer that fails is WITHHELD whole, its text and its sources (decision D1):
the text was written from the documents, so hiding only the citations would still
leak them. The viewer gets the question and a prompt to ask it themselves, which
runs with their own permissions.
"""

import time

from fastapi import HTTPException

import access
import conversations_store as store
import groups_store
import members_store
import workspaces
from kb.deps import ws_of
from kb.services import conversations

EXPIRY_CHOICES = (1, 7, 30, None)       # days; None: as long as the chat is kept
GONE = "This link doesn't work any more."
NOT_FOUND = "That link doesn't exist, or it isn't for your workspace."
_RANK = {"public": 0, "hr": 1}


def lowest(*roles):
    return min(roles, key=lambda r: _RANK.get(r, 0)) if roles else "public"


def member_role(ws, username):
    """The role someone has in `ws` now ("hr" or "public"), or None if they're no
    longer an enabled member of it."""
    member = members_store.check_group_admin(members_store.get(username))
    if not member or member["disabled"] or (member["workspace"] or workspaces.DEFAULT) != ws:
        return None
    return "hr" if member["role"] in members_store.STAFF_ROLES else "public"


def expiry(ws, days, now=None):
    """When a new link stops working: `days` from now, but never after the chat
    could be deleted by retention."""
    if days not in EXPIRY_CHOICES:
        raise HTTPException(400, "A link lasts 1, 7 or 30 days, or as long as the chat is kept.")
    now = now or time.time()
    keep = store.chat_settings(ws)["retention_days"]
    limits = [d for d in (days, keep) if d]
    return now + min(limits) * 86400 if limits else None


def shared_messages(share):
    """What a link shows, before any check: the chat from its first message down to
    the leaf it was shared at, or one answer with its question."""
    path = store.path(share["conversation_id"], share["leaf_id"])
    return path[-2:] if share["message_id"] else path


def is_restricted(messages):
    """Does any answer in it need more than being in the workspace to read?"""
    return any(m["author"] == "assistant" and access.message_clauses(m) for m in messages)


def open_share(token, viewer, viewer_role):
    """The link `token` as `viewer` (a signed-in user, viewing the app as
    viewer_role) may see it. Raises 404 / 410. An answer shows only if BOTH the
    viewer (their principals now) and the sharer (theirs now, at the lower of
    their role now and the role it was shared as) may read it."""
    ws = ws_of(viewer)
    share = store.share_by_token(token)
    if not share or share["workspace"] != ws:
        raise HTTPException(404, NOT_FOUND)
    conv = store.get_any(ws, share["conversation_id"])
    sharer_role = member_role(ws, share["created_by"])
    if (not conv or share["revoked_at"] or sharer_role is None
            or (share["expires_at"] and share["expires_at"] <= time.time())
            or not store.chat_settings(ws)["sharing_enabled"]):
        raise HTTPException(410, GONE)
    readers = (groups_store.principals_for(ws, viewer["username"],
                                           lowest(viewer_role, share["role_used"])),
               groups_store.principals_for(ws, share["created_by"],
                                           lowest(sharer_role, share["role_used"])))
    messages, question = [], None
    for m in shared_messages(share):
        shown = conversations.present(m, readers)
        if m["author"] == "user":
            question = m["content"]
        else:
            shown["question"] = question
            shown["copy"] = (conversations.markdown_of(m) if not shown["withheld"]
                             and shown["state"] not in ("pending", "error") else None)
        shown.pop("feedback", None)
        messages.append(shown)
    sharer = members_store.get(share["created_by"]) or {}
    return {"kind": "answer" if share["message_id"] else "chat",
            "title": conv["title"] or "Shared chat",
            "shared_by": sharer.get("name") or share["created_by"],
            "created_at": share["created_at"], "expires_at": share["expires_at"],
            "withheld": sum(1 for m in messages if m.get("withheld")),
            "messages": messages}
