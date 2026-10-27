"""
attachments_store.py  —  files attached to a chat, or kept in a personal space,
as text (config/app.db, tables made by
migrations/versions/0018_spaces_attachments_prompts.py):

    attachments         one row per file: whose, which chat (or space), its name,
                        type and size, and when it expires. The file itself is
                        never kept, only its extracted text
    attachment_chunks   that text, split for searching

A chat's attachments are deleted with it (ON DELETE CASCADE), a space's with
the space. Every read names the workspace AND the owner: only the person who
attached a file ever reads it back. Only SQL lives here; extracting, splitting
and searching is kb/services/attachments.py.
"""

import contextlib
import time
import uuid

import db


@contextlib.contextmanager
def _db():
    conn = db.connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


COLUMNS = ("id, workspace, owner, conversation_id, space_id, filename, mime, kind, size, "
           "text_chars, chunks, tables, flagged, created_at, expires_at")


def add(workspace, owner, filename, mime, kind, size, chunks, *, conversation_id=None,
        space_id=None, tables=0, flagged=False, expires_at=None):
    """Save an attachment and its chunks: [{text, char_start, char_end, heading_path,
    kind}]. Returns the row."""
    aid = uuid.uuid4().hex
    text_chars = sum(len(c["text"]) for c in chunks)
    with _db() as conn:
        conn.execute(f"INSERT INTO attachments ({COLUMNS}) VALUES "
                     "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (aid, workspace, owner, conversation_id, space_id, filename, mime, kind,
                      size, text_chars, len(chunks), tables, int(bool(flagged)), time.time(),
                      expires_at))
        conn.executemany(
            "INSERT INTO attachment_chunks (attachment_id, workspace, n, text, char_start, "
            "char_end, heading_path, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(aid, workspace, n, c["text"], c["char_start"], c["char_end"],
              c.get("heading_path") or None, c.get("kind") or "text")
             for n, c in enumerate(chunks)])
    return get(workspace, owner, aid)


def get(workspace, owner, attachment_id):
    with _db() as conn:
        row = conn.execute(f"SELECT {COLUMNS} FROM attachments WHERE id = ? AND workspace = ? "
                           "AND owner = ?", (attachment_id, workspace, owner)).fetchone()
    return dict(row) if row else None


def of_conversation(workspace, owner, conversation_id, now=None):
    """A chat's attachments that haven't expired, oldest first."""
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {COLUMNS} FROM attachments WHERE workspace = ? AND owner = ? AND "
            "conversation_id = ? AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at",
            (workspace, owner, conversation_id, now or time.time()))]


def of_space(workspace, owner, space_id):
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {COLUMNS} FROM attachments WHERE workspace = ? AND owner = ? AND "
            "space_id = ? ORDER BY filename COLLATE NOCASE", (workspace, owner, space_id))]


def chunks(workspace, attachment_ids):
    """{attachment id: [chunk rows in order]} of these attachments."""
    ids = list(attachment_ids)
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    out = {aid: [] for aid in ids}
    with _db() as conn:
        for r in conn.execute(f"SELECT * FROM attachment_chunks WHERE workspace = ? AND "
                              f"attachment_id IN ({marks}) ORDER BY attachment_id, n",
                              (workspace, *ids)):
            out[r["attachment_id"]].append(dict(r))
    return out


def chunk(workspace, owner, attachment_id, n):
    """One chunk, with its neighbours' text, if the attachment is the owner's."""
    if not get(workspace, owner, attachment_id):
        return None
    with _db() as conn:
        rows = {r["n"]: dict(r) for r in conn.execute(
            "SELECT * FROM attachment_chunks WHERE attachment_id = ? AND n BETWEEN ? AND ?",
            (attachment_id, n - 1, n + 1))}
    if n not in rows:
        return None
    return {**rows[n], "before": (rows.get(n - 1) or {}).get("text", ""),
            "after": (rows.get(n + 1) or {}).get("text", "")}


def delete(workspace, owner, attachment_id):
    with _db() as conn:
        return conn.execute("DELETE FROM attachments WHERE id = ? AND workspace = ? AND "
                            "owner = ?", (attachment_id, workspace, owner)).rowcount > 0


def count_for(workspace, owner, conversation_id=None, space_id=None):
    with _db() as conn:
        if conversation_id:
            return conn.execute("SELECT COUNT(*) FROM attachments WHERE workspace = ? AND "
                                "owner = ? AND conversation_id = ?",
                                (workspace, owner, conversation_id)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM attachments WHERE workspace = ? AND owner = ? "
                            "AND space_id = ?", (workspace, owner, space_id)).fetchone()[0]


def purge_expired(now=None):
    """Delete every attachment past its expiry. Returns {workspace: count}."""
    now = now or time.time()
    with _db() as conn:
        rows = conn.execute("SELECT workspace, COUNT(*) FROM attachments WHERE expires_at IS NOT "
                            "NULL AND expires_at <= ? GROUP BY workspace", (now,)).fetchall()
        conn.execute("DELETE FROM attachments WHERE expires_at IS NOT NULL AND expires_at <= ?",
                     (now,))
    return {r[0]: r[1] for r in rows}
