"""
conversations_store.py  —  saved chats: conversations, their messages, and the
feedback people give on answers (config/app.db, see db.py; tables made by
migrations/versions/0002_conversations.py).

Only SQL lives here. Who may see what (visible_to), the history a follow-up is
answered with, and an answer's state are decided in kb/services/conversations.py.

Every read and write of a conversation names its workspace AND owner, so one
person can never reach another's chat by guessing an id.
"""

import json
import threading
import time
import uuid

import db

_lock = threading.Lock()

STATES = ("answered", "partial", "not_found", "blocked", "general_knowledge", "error",
          "stopped")
TITLE_CHARS = 80
_JSON_FIELDS = ("sources", "steps", "trace")


def _db():
    return db.connect()


def _new_id():
    return uuid.uuid4().hex


def _conversation(r):
    return dict(r) if r else None


def _message(r):
    if not r:
        return None
    m = dict(r)
    for key in _JSON_FIELDS:
        m[key] = json.loads(m.get(key) or "[]")
    return m


def title_for(question):
    """A chat's title: its first question, on one line, cut at TITLE_CHARS. Never the
    answer, so a title can't carry what only the answer's readers may see."""
    text = " ".join((question or "").split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rstrip() + "…"


# ---- Conversations --------------------------------------------------------------------
def create(workspace, owner, role_used, title=None):
    now = time.time()
    cid = _new_id()
    with _lock, _db() as conn:
        conn.execute("INSERT INTO conversations (id, workspace, owner, title, title_source, "
                     "role_used, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (cid, workspace, owner, title, "user" if title else None, role_used,
                      now, now))
    return get(workspace, owner, cid)


def get(workspace, owner, cid):
    with _lock, _db() as conn:
        return _conversation(conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND workspace = ? AND owner = ? "
            "AND deleted_at IS NULL", (cid, workspace, owner)).fetchone())


def list_for(workspace, owner, limit=50, before=None):
    """The owner's conversations with something in them, most recently used first.
    `before`: an updated_at from the previous page."""
    sql = ("SELECT * FROM conversations WHERE workspace = ? AND owner = ? "
           "AND deleted_at IS NULL AND active_leaf_id IS NOT NULL")
    args = [workspace, owner]
    if before is not None:
        sql, args = sql + " AND updated_at < ?", args + [before]
    with _lock, _db() as conn:
        return [_conversation(r) for r in conn.execute(
            sql + " ORDER BY updated_at DESC LIMIT ?", args + [limit])]


def rename(workspace, owner, cid, title):
    with _lock, _db() as conn:
        changed = conn.execute(
            "UPDATE conversations SET title = ?, title_source = 'user', updated_at = ? "
            "WHERE id = ? AND workspace = ? AND owner = ? AND deleted_at IS NULL",
            (title, time.time(), cid, workspace, owner)).rowcount
    return bool(changed)


def delete(workspace, owner, cid):
    """Gone for good: its messages and the feedback on them go with it (cascade)."""
    with _lock, _db() as conn:
        return bool(conn.execute(
            "DELETE FROM conversations WHERE id = ? AND workspace = ? AND owner = ?",
            (cid, workspace, owner)).rowcount)


# ---- Messages -------------------------------------------------------------------------
def add_message(cid, author, content, parent_id=None, state=None, max_access="public",
                standalone_question=None):
    """Add a message after `parent_id` and make it the one the chat shows. A first
    question with no title yet becomes the title."""
    now = time.time()
    mid = _new_id()
    with _lock, _db() as conn:
        conn.execute("INSERT INTO messages (id, conversation_id, parent_id, author, content, "
                     "state, max_access, standalone_question, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (mid, cid, parent_id, author, content, state, max_access,
                      standalone_question, now))
        conn.execute("UPDATE conversations SET active_leaf_id = ?, updated_at = ? WHERE id = ?",
                     (mid, now, cid))
        if author == "user":
            conn.execute("UPDATE conversations SET title = ?, title_source = 'auto' "
                         "WHERE id = ? AND title IS NULL", (title_for(content), cid))
    return mid


def finish_message(mid, content, state, sources=(), steps=(), trace=(), max_access="public",
                   standalone_question=None, latency_ms=None, model=None):
    """An answer is done (or stopped, or failed): its text, state and what it read."""
    if state not in STATES:
        raise ValueError(f"unknown answer state {state!r}")
    with _lock, _db() as conn:
        conn.execute("UPDATE messages SET content = ?, state = ?, sources = ?, steps = ?, "
                     "trace = ?, max_access = ?, standalone_question = ?, latency_ms = ?, "
                     "model = ? WHERE id = ?",
                     (content, state, json.dumps(list(sources)), json.dumps(list(steps)),
                      json.dumps(list(trace)), max_access, standalone_question, latency_ms,
                      model, mid))
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = "
                     "(SELECT conversation_id FROM messages WHERE id = ?)", (time.time(), mid))


def get_message(workspace, owner, mid):
    """A message in one of the owner's conversations (with its conversation_id)."""
    with _lock, _db() as conn:
        return _message(conn.execute(
            "SELECT m.* FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.id = ? AND c.workspace = ? AND c.owner = ? AND c.deleted_at IS NULL",
            (mid, workspace, owner)).fetchone())


def path(cid, leaf_id):
    """The messages from the first one down to `leaf_id`, in order: the chat as shown."""
    with _lock, _db() as conn:
        rows = {r["id"]: r for r in conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ?", (cid,))}
    out, seen = [], set()
    mid = leaf_id
    while mid and mid in rows and mid not in seen:
        seen.add(mid)
        out.append(_message(rows[mid]))
        mid = rows[mid]["parent_id"]
    return out[::-1]


# ---- Feedback -------------------------------------------------------------------------
REASONS = ("wrong", "wrong_source", "missing", "outdated", "refused", "unclear")


def set_feedback(mid, user, workspace, rating, reasons=(), comment=None, snapshot=None):
    now = time.time()
    with _lock, _db() as conn:
        conn.execute("""INSERT INTO message_feedback
            (message_id, user, workspace, rating, reasons, comment, snapshot, created_at,
             updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id, user) DO UPDATE SET rating = excluded.rating,
              reasons = excluded.reasons, comment = excluded.comment,
              snapshot = excluded.snapshot, updated_at = excluded.updated_at""",
            (mid, user, workspace, rating, json.dumps(list(reasons)), comment,
             json.dumps(snapshot or {}), now, now))


def delete_feedback(mid, user):
    with _lock, _db() as conn:
        return bool(conn.execute("DELETE FROM message_feedback WHERE message_id = ? AND user = ?",
                                 (mid, user)).rowcount)


def _feedback(r):
    f = dict(r)
    f["reasons"] = json.loads(f.get("reasons") or "[]")
    f["snapshot"] = json.loads(f.get("snapshot") or "{}")
    return f


def feedback_of(user, message_ids):
    """{message id: feedback} that `user` gave on these messages."""
    ids = list(message_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with _lock, _db() as conn:
        return {r["message_id"]: _feedback(r) for r in conn.execute(
            f"SELECT * FROM message_feedback WHERE user = ? AND message_id IN ({marks})",
            [user, *ids])}


def list_feedback(workspace, rating=None, limit=100):
    """A workspace's feedback, newest first (thumbs down only with rating=-1)."""
    sql, args = "SELECT * FROM message_feedback WHERE workspace = ?", [workspace]
    if rating is not None:
        sql, args = sql + " AND rating = ?", args + [rating]
    with _lock, _db() as conn:
        return [_feedback(r) for r in conn.execute(
            sql + " ORDER BY updated_at DESC LIMIT ?", args + [limit])]
