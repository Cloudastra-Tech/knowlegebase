"""
email_in_store.py  —  email-in's rows in config/app.db (migration 0015).

SETTINGS (email_in_settings, one per workspace): email-in on or off. No row, or
enabled = 0, is off: a workspace answers email only once an admin turns it on.

MESSAGES (email_in_messages): every email taken in, by its Message-ID, so one
the provider delivers twice is answered once; who sent it, what became of it
(queued, answered, refused, dropped, failed, with why), the conversation it was
answered in, and the Message-ID of our reply: a reply to that from the same
person continues the same conversation. Kept KEEP_DAYS.
"""

import secrets
import sqlite3
import threading
import time

import db

_lock = threading.Lock()

KEEP_DAYS = 90
STATUSES = ("queued", "answered", "refused", "dropped", "failed")


def _db():
    return db.connect()


# ---- Settings ------------------------------------------------------------------------------
def settings(workspace):
    """{enabled, updated_by, updated_at} of a workspace (off when there's no row)."""
    with _db() as conn:
        r = conn.execute("SELECT enabled, updated_by, updated_at FROM email_in_settings "
                         "WHERE workspace = ?", (workspace,)).fetchone()
    if not r:
        return {"enabled": False, "updated_by": None, "updated_at": None}
    return {"enabled": bool(r["enabled"]), "updated_by": r["updated_by"],
            "updated_at": r["updated_at"]}


def enabled(workspace):
    return settings(workspace)["enabled"]


def set_enabled(workspace, on, user):
    with _lock, _db() as conn:
        conn.execute("INSERT OR IGNORE INTO workspaces (slug, name, created_at) VALUES (?, ?, ?)",
                     (workspace, workspace, time.time()))
        conn.execute("INSERT INTO email_in_settings (workspace, enabled, updated_by, updated_at) "
                     "VALUES (?, ?, ?, ?) ON CONFLICT (workspace) DO UPDATE SET "
                     "enabled = excluded.enabled, updated_by = excluded.updated_by, "
                     "updated_at = excluded.updated_at",
                     (workspace, int(bool(on)), user, time.time()))


def remove(workspace):
    """Email-in off and forgotten for a workspace (purging it): its setting and the
    record of every email taken in. Returns how many emails were forgotten."""
    with _lock, _db() as conn:
        conn.execute("DELETE FROM email_in_settings WHERE workspace = ?", (workspace,))
        return conn.execute("DELETE FROM email_in_messages WHERE workspace = ?",
                            (workspace,)).rowcount


# ---- Messages ------------------------------------------------------------------------------
def first_time(message_id, workspace, sender, status="queued", reason=None):
    """Record an email taken in. True the first time its Message-ID is seen; False
    for one delivered again (then nothing changes). Old rows are pruned as it goes."""
    now = time.time()
    with _lock, _db() as conn:
        if secrets.randbelow(50) == 0:
            conn.execute("DELETE FROM email_in_messages WHERE received_at < ?",
                         (now - KEEP_DAYS * 86400,))
        try:
            conn.execute("INSERT INTO email_in_messages (message_id, workspace, sender, "
                         "received_at, status, reason) VALUES (?, ?, ?, ?, ?, ?)",
                         (message_id, workspace, sender, now, status, reason))
        except sqlite3.IntegrityError:
            return False
    return True


def set_status(message_id, status, reason=None, conversation_id=None, reply_message_id=None):
    if status not in STATUSES:
        raise ValueError(status)
    with _lock, _db() as conn:
        conn.execute("UPDATE email_in_messages SET status = ?, reason = COALESCE(?, reason), "
                     "conversation_id = COALESCE(?, conversation_id), "
                     "reply_message_id = COALESCE(?, reply_message_id) WHERE message_id = ?",
                     (status, reason, conversation_id, reply_message_id, message_id))


def get(message_id):
    with _db() as conn:
        r = conn.execute("SELECT * FROM email_in_messages WHERE message_id = ?",
                         (message_id,)).fetchone()
    return dict(r) if r else None


def conversation_replied_to(workspace, sender, message_ids):
    """The conversation of our reply among `message_ids` (In-Reply-To, References)
    sent to this sender in this workspace, newest first; or None."""
    ids = [m for m in message_ids if m][:50]
    if not ids:
        return None
    with _db() as conn:
        r = conn.execute(
            f"SELECT conversation_id FROM email_in_messages WHERE reply_message_id IN "
            f"({','.join('?' * len(ids))}) AND workspace = ? AND sender = ? AND "
            "conversation_id IS NOT NULL ORDER BY received_at DESC LIMIT 1",
            (*ids, workspace, sender)).fetchone()
    return r["conversation_id"] if r else None
