"""
conversations_store.py  —  saved chats: conversations, their messages, and the
feedback people give on answers; folders, the search index over them, the
workspace's chat settings, and links that share them (config/app.db, see db.py;
tables made by migrations/versions/0003_conversations.py and 0007_chat_history.py).

Only SQL lives here. Who may see what (visible_to), the history a follow-up is
answered with, and an answer's state are decided in kb/services/conversations.py.

Every read and write of a conversation names its workspace AND owner, so one
person can never reach another's chat by guessing an id.
"""

import hashlib
import json
import re
import secrets
import threading
import time
import uuid

import db

_lock = threading.Lock()

STATES = ("answered", "partial", "not_found", "blocked", "general_knowledge", "error",
          "stopped")
TITLE_CHARS = 80
FOLDER_CHARS = 60
FORK_PREFIX = "↳ "
_JSON_FIELDS = ("sources", "steps", "trace", "followups")


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


# ---- The search index (conversation_search) -------------------------------------------
def answer_access(access, sources):
    """An answer's level for search: its own max_access and each source's. Anything
    that isn't "public" counts as restricted (as in conversations.max_access)."""
    levels = [access or "restricted"] + [s.get("access") for s in sources or ()
                                         if s.get("access")]
    return "public" if all(level == "public" for level in levels) else "restricted"


def _index(conn, body, cid, mid, access):
    if body:
        conn.execute("INSERT INTO conversation_search (body, conversation_id, message_id, "
                     "access) VALUES (?, ?, ?, ?)", (body, cid, mid, access))


def _index_answer(conn, m, cid, mid):
    """An answer is searchable once it's written (not while pending, nor a failure)."""
    if m["state"] not in (None, "error"):
        _index(conn, m["content"], cid, mid, answer_access(m["max_access"], m["sources"]))


def _index_title(conn, cid, title):
    conn.execute("DELETE FROM conversation_search WHERE conversation_id = ? AND "
                 "message_id IS NULL", (cid,))
    _index(conn, title, cid, None, "public")      # from the first question, never an answer


def _unindex(conn, cid):
    conn.execute("DELETE FROM conversation_search WHERE conversation_id = ?", (cid,))


_WORD = re.compile(r"\w+")


def _match_query(q):
    """What a person typed as an FTS5 query: each word a prefix ("refu" finds
    "refund"), all of them required. No FTS syntax gets through."""
    return " ".join(f'"{w}"*' for w in _WORD.findall(q or "")[:8])


def search(workspace, owner, levels, q, limit=20):
    """The owner's chats whose title, questions or answers match `q`, best match
    first, one result per chat: [{conversation_id, message_id, title, snippet,
    updated_at}]. Only rows at an access level in `levels` (the searcher's role
    now) can match, so an answer they may no longer read is never found. The
    snippet marks each match with \\x02 ... \\x03."""
    query = _match_query(q)
    if not query or not levels:
        return []
    marks = ",".join("?" * len(levels))
    with _lock, _db() as conn:
        rows = conn.execute(f"""
            SELECT s.conversation_id, s.message_id, c.title, c.updated_at,
                   snippet(conversation_search, 0, char(2), char(3), '…', 12) AS snippet
              FROM conversation_search s JOIN conversations c ON c.id = s.conversation_id
             WHERE conversation_search MATCH ? AND s.access IN ({marks})
               AND c.workspace = ? AND c.owner = ? AND c.deleted_at IS NULL
               AND c.active_leaf_id IS NOT NULL
             ORDER BY bm25(conversation_search) LIMIT ?""",
                            (query, *levels, workspace, owner, limit * 5)).fetchall()
    out, seen = [], set()
    for r in rows:
        if r["conversation_id"] not in seen:
            seen.add(r["conversation_id"])
            out.append(dict(r))
    return out[:limit]


# ---- Conversations --------------------------------------------------------------------
def create(workspace, owner, role_used, title=None, forked_from=None):
    now = time.time()
    cid = _new_id()
    with _lock, _db() as conn:
        conn.execute("INSERT INTO conversations (id, workspace, owner, title, title_source, "
                     "role_used, forked_from_message_id, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (cid, workspace, owner, title, "user" if title else None, role_used,
                      forked_from, now, now))
        _index_title(conn, cid, title)
    return get(workspace, owner, cid)


def get(workspace, owner, cid):
    with _lock, _db() as conn:
        return _conversation(conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND workspace = ? AND owner = ? "
            "AND deleted_at IS NULL", (cid, workspace, owner)).fetchone())


def get_any(workspace, cid):
    """A conversation in the workspace, whoever's it is (a share link's)."""
    with _lock, _db() as conn:
        return _conversation(conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND workspace = ? AND deleted_at IS NULL",
            (cid, workspace)).fetchone())


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
        if changed:
            _index_title(conn, cid, title)
    return bool(changed)


def set_auto_title(cid, title):
    """A better title than the first question's own words, unless the owner has
    named the chat since. True if it was set."""
    with _lock, _db() as conn:
        changed = conn.execute("UPDATE conversations SET title = ? WHERE id = ? AND "
                               "title_source = 'auto'", (title, cid)).rowcount
        if changed:
            _index_title(conn, cid, title)
    return bool(changed)


def organize(workspace, owner, cid, pinned=None, folder_id=False):
    """Pin or unpin (pinned=True / False), and move to a folder (folder_id: its id,
    or None for no folder; False leaves it). Not counted as using the chat, so its
    place in the list by date stays."""
    sets, args = [], []
    if pinned is not None:
        sets.append("pinned_at = ?")
        args.append(time.time() if pinned else None)
    if folder_id is not False:
        sets.append("folder_id = ?")
        args.append(folder_id)
    if not sets:
        return True
    with _lock, _db() as conn:
        return bool(conn.execute(
            f"UPDATE conversations SET {', '.join(sets)} WHERE id = ? AND workspace = ? "
            "AND owner = ? AND deleted_at IS NULL", (*args, cid, workspace, owner)).rowcount)


def show_branch(workspace, owner, cid, message_id):
    """Show the branch through `message_id`: the chat's active leaf becomes the
    newest message at the end of it. False if it isn't in this chat."""
    with _lock, _db() as conn:
        conv = conn.execute("SELECT id FROM conversations WHERE id = ? AND workspace = ? AND "
                            "owner = ? AND deleted_at IS NULL", (cid, workspace, owner)).fetchone()
        rows = conn.execute("SELECT id, parent_id FROM messages WHERE conversation_id = ? "
                            "ORDER BY created_at, rowid", (cid,)).fetchall()
        if not conv or message_id not in {r["id"] for r in rows}:
            return False
        children = {}
        for r in rows:
            children.setdefault(r["parent_id"], []).append(r["id"])
        leaf, seen = message_id, set()
        while children.get(leaf) and leaf not in seen:
            seen.add(leaf)
            leaf = children[leaf][-1]                 # the newest question / answer
        conn.execute("UPDATE conversations SET active_leaf_id = ? WHERE id = ?", (leaf, cid))
    return True


def delete(workspace, owner, cid):
    """Gone for good: its messages and the feedback on them go with it (cascade),
    and so do its share links and its rows in the search index."""
    with _lock, _db() as conn:
        deleted = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND workspace = ? AND owner = ?",
            (cid, workspace, owner)).rowcount
        if deleted:
            _unindex(conn, cid)
    return bool(deleted)


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
            _index(conn, content, cid, mid, "public")     # the asker's own words
            title = title_for(content)
            if conn.execute("UPDATE conversations SET title = ?, title_source = 'auto' "
                            "WHERE id = ? AND title IS NULL", (title, cid)).rowcount:
                _index_title(conn, cid, title)
    return mid


def finish_message(mid, content, state, sources=(), steps=(), trace=(), max_access="public",
                   standalone_question=None, latency_ms=None, model=None, followups=()):
    """An answer is done (or stopped, or failed): its text, state and what it read."""
    if state not in STATES:
        raise ValueError(f"unknown answer state {state!r}")
    with _lock, _db() as conn:
        conn.execute("UPDATE messages SET content = ?, state = ?, sources = ?, steps = ?, "
                     "trace = ?, max_access = ?, standalone_question = ?, latency_ms = ?, "
                     "model = ?, followups = ? WHERE id = ?",
                     (content, state, json.dumps(list(sources)), json.dumps(list(steps)),
                      json.dumps(list(trace)), max_access, standalone_question, latency_ms,
                      model, json.dumps(list(followups)), mid))
        row = conn.execute("SELECT conversation_id FROM messages WHERE id = ?", (mid,)).fetchone()
        if row:
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                         (time.time(), row[0]))
            conn.execute("DELETE FROM conversation_search WHERE message_id = ?", (mid,))
            _index_answer(conn, {"content": content, "state": state, "max_access": max_access,
                                 "sources": list(sources)}, row[0], mid)


def get_message(workspace, owner, mid):
    """A message in one of the owner's conversations (with its conversation_id)."""
    with _lock, _db() as conn:
        return _message(conn.execute(
            "SELECT m.* FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.id = ? AND c.workspace = ? AND c.owner = ? AND c.deleted_at IS NULL",
            (mid, workspace, owner)).fetchone())


def messages_of(cid):
    """Every message of a conversation, on every branch, oldest first."""
    with _lock, _db() as conn:
        return [_message(r) for r in conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
            (cid,))]


def path(cid, leaf_id, messages=None):
    """The messages from the first one down to `leaf_id`, in order: the chat as shown.
    `messages`: the conversation's (messages_of), when already read."""
    rows = {m["id"]: m for m in (messages_of(cid) if messages is None else messages)}
    out, seen = [], set()
    mid = leaf_id
    while mid and mid in rows and mid not in seen:
        seen.add(mid)
        out.append(rows[mid])
        mid = rows[mid]["parent_id"]
    return out[::-1]


def siblings(messages):
    """{message id: its own and its siblings' ids (the same parent), oldest first}:
    the versions that editing a question or regenerating an answer made."""
    by_parent = {}
    for m in messages:
        by_parent.setdefault(m["parent_id"], []).append(m["id"])
    return {m["id"]: by_parent[m["parent_id"]] for m in messages}


_COPIED = ("author", "content", "standalone_question", "state", "sources", "max_access",
           "steps", "trace", "followups", "model", "latency_ms", "index_version", "created_at")


def fork(workspace, owner, cid, message_id):
    """A new chat holding a copy of the path from the first message down to
    `message_id` ("Branch in new chat"). Rows are copied, not shared, so deleting
    either chat (or retention) never touches the other. It keeps the original's
    role, so the copied answers are read under the same rules. None if the
    message isn't in the owner's chat."""
    conv = get(workspace, owner, cid)
    copied = path(cid, message_id) if conv else []
    if not copied or copied[-1]["id"] != message_id:
        return None
    title = (FORK_PREFIX + (conv["title"] or "New chat"))[:TITLE_CHARS]
    new = create(workspace, owner, conv["role_used"], title, forked_from=message_id)
    parent = None
    with _lock, _db() as conn:
        for m in copied:
            mid = _new_id()
            row = {k: m.get(k) for k in _COPIED}
            for key in _JSON_FIELDS:
                row[key] = json.dumps(row.get(key) or [])
            conn.execute(f"INSERT INTO messages (id, conversation_id, parent_id, "
                         f"{', '.join(_COPIED)}) VALUES (?, ?, ?, {', '.join('?' * len(_COPIED))})",
                         (mid, new["id"], parent, *[row[k] for k in _COPIED]))
            if m["author"] == "user":
                _index(conn, m["content"], new["id"], mid, "public")
            else:
                _index_answer(conn, m, new["id"], mid)
            parent = mid
        conn.execute("UPDATE conversations SET active_leaf_id = ?, updated_at = ? WHERE id = ?",
                     (parent, time.time(), new["id"]))
    return get(workspace, owner, new["id"])


def conversation_of_message(workspace, owner, mid):
    """{id, title} of the owner's chat holding message `mid` (where a fork came from)."""
    with _lock, _db() as conn:
        r = conn.execute("SELECT c.id, c.title FROM messages m JOIN conversations c ON "
                         "c.id = m.conversation_id WHERE m.id = ? AND c.workspace = ? AND "
                         "c.owner = ? AND c.deleted_at IS NULL", (mid, workspace, owner)).fetchone()
    return dict(r) if r else None


# ---- Folders --------------------------------------------------------------------------
def folders(workspace, owner):
    with _lock, _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, sort, created_at FROM conversation_folders WHERE workspace = ? "
            "AND owner = ? ORDER BY sort, name COLLATE NOCASE", (workspace, owner))]


def folder(workspace, owner, fid):
    with _lock, _db() as conn:
        r = conn.execute("SELECT id, name, sort, created_at FROM conversation_folders WHERE "
                         "id = ? AND workspace = ? AND owner = ?",
                         (fid, workspace, owner)).fetchone()
    return dict(r) if r else None


def create_folder(workspace, owner, name):
    fid = _new_id()
    with _lock, _db() as conn:
        sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM conversation_folders "
                            "WHERE workspace = ? AND owner = ?", (workspace, owner)).fetchone()[0]
        conn.execute("INSERT INTO conversation_folders (id, workspace, owner, name, sort, "
                     "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (fid, workspace, owner, name, sort, time.time()))
    return folder(workspace, owner, fid)


def rename_folder(workspace, owner, fid, name):
    with _lock, _db() as conn:
        return bool(conn.execute("UPDATE conversation_folders SET name = ? WHERE id = ? AND "
                                 "workspace = ? AND owner = ?",
                                 (name, fid, workspace, owner)).rowcount)


def delete_folder(workspace, owner, fid):
    """The folder goes; the chats in it stay, in no folder."""
    with _lock, _db() as conn:
        deleted = conn.execute("DELETE FROM conversation_folders WHERE id = ? AND "
                               "workspace = ? AND owner = ?", (fid, workspace, owner)).rowcount
        if deleted:
            conn.execute("UPDATE conversations SET folder_id = NULL WHERE folder_id = ? AND "
                         "workspace = ? AND owner = ?", (fid, workspace, owner))
    return bool(deleted)


# ---- Chat settings and retention ------------------------------------------------------
DEFAULT_RETENTION_DAYS = 365              # decision D4: a year, and everyone is told
RETENTION_CHOICES = (30, 90, 365, None)   # None: kept until their owner deletes them


def chat_settings(workspace):
    """{retention_days, sharing_enabled, updated_by, updated_at}: the defaults until
    an admin changes them."""
    with _lock, _db() as conn:
        r = conn.execute("SELECT * FROM chat_settings WHERE workspace = ?",
                         (workspace,)).fetchone()
    if not r:
        return {"retention_days": DEFAULT_RETENTION_DAYS, "sharing_enabled": True,
                "updated_by": None, "updated_at": None}
    return {"retention_days": r["retention_days"], "sharing_enabled": bool(r["sharing_enabled"]),
            "updated_by": r["updated_by"], "updated_at": r["updated_at"]}


def set_chat_settings(workspace, retention_days, sharing_enabled, updated_by):
    if retention_days not in RETENTION_CHOICES:
        raise ValueError("retention_days must be 30, 90, 365 or null (forever)")
    with _lock, _db() as conn:
        conn.execute("""INSERT INTO chat_settings (workspace, retention_days, sharing_enabled,
                            updated_by, updated_at) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(workspace) DO UPDATE SET
                            retention_days = excluded.retention_days,
                            sharing_enabled = excluded.sharing_enabled,
                            updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                     (workspace, retention_days, int(bool(sharing_enabled)), updated_by,
                      time.time()))
    return chat_settings(workspace)


def workspaces_with_chats():
    with _lock, _db() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT workspace FROM conversations")]


def purge(workspace, before):
    """Delete the workspace's chats last used before `before` (a time): their
    messages, the feedback on them and their share links go too (cascade), and
    their search rows. Answers an admin promoted to the test set are copies in
    their own table, so they stay (decision D2). Returns how many chats."""
    with _lock, _db() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM conversations WHERE workspace = ? AND updated_at < ?",
            (workspace, before))]
        for cid in ids:
            conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
            _unindex(conn, cid)
    return len(ids)


# ---- Share links ----------------------------------------------------------------------
def token_hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def create_share(workspace, cid, created_by, role_used, leaf_id, message_id=None,
                 expires_at=None):
    """A link to a chat (up to leaf_id) or to one answer (message_id). Returns
    (the token, the share): only the token's hash is kept, like sessions_store."""
    token = secrets.token_urlsafe(32)
    sid = _new_id()
    with _lock, _db() as conn:
        conn.execute("INSERT INTO shares (id, token_hash, workspace, conversation_id, "
                     "message_id, leaf_id, created_by, role_used, expires_at, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (sid, token_hash(token), workspace, cid, message_id, leaf_id, created_by,
                      role_used, expires_at, time.time()))
        return token, dict(conn.execute("SELECT * FROM shares WHERE id = ?", (sid,)).fetchone())


def share_by_token(token):
    with _lock, _db() as conn:
        r = conn.execute("SELECT * FROM shares WHERE token_hash = ?",
                         (token_hash(token),)).fetchone()
    return dict(r) if r else None


def shares_of(workspace, created_by, cid):
    """The links someone made to a chat that still work, newest first."""
    with _lock, _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM shares WHERE workspace = ? AND created_by = ? AND "
            "conversation_id = ? AND revoked_at IS NULL AND (expires_at IS NULL OR "
            "expires_at > ?) ORDER BY created_at DESC",
            (workspace, created_by, cid, time.time()))]


def revoke_share(workspace, created_by, sid):
    with _lock, _db() as conn:
        return bool(conn.execute(
            "UPDATE shares SET revoked_at = ? WHERE id = ? AND workspace = ? AND "
            "created_by = ? AND revoked_at IS NULL",
            (time.time(), sid, workspace, created_by)).rowcount)


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
