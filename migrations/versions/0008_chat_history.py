"""chat history: folders, search, follow-ups, retention and share links

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27

Additive only: new tables, one new column on `messages` (with a default), and
the search index filled from the rows already there. Nothing existing changes.

    conversation_folders   a person's folders for their chats (conversations.folder_id)
    conversation_search    FTS5 over chat titles, questions and answers. Each row
                           keeps the access level of what it was written from, so a
                           search never matches an answer the searcher's role can't
                           read now (conversations_store.search)
    messages.followups     JSON: the follow-up questions suggested after an answer
    chat_settings          per workspace: how long chats are kept (NULL: forever),
                           and whether chats may be shared by link
    shares                 links to a chat or one answer. Only the token's sha256
                           is stored; the link itself is shown once
"""

import json

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS conversation_folders (
        id          TEXT PRIMARY KEY,            -- uuid
        workspace   TEXT NOT NULL,
        owner       TEXT NOT NULL,               -- members.username
        name        TEXT NOT NULL,
        sort        INTEGER NOT NULL DEFAULT 0,
        created_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS conversation_folders_by_owner ON conversation_folders(workspace, owner)",
    # One row per searchable text: a chat's title (message_id NULL), a question or
    # an answer. `access` is "public" for titles and questions (the asker's own
    # words) and an answer's highest access level otherwise.
    """CREATE VIRTUAL TABLE IF NOT EXISTS conversation_search USING fts5(
        body, conversation_id UNINDEXED, message_id UNINDEXED, access UNINDEXED,
        tokenize = 'porter unicode61')""",
    """CREATE TABLE IF NOT EXISTS chat_settings (
        workspace   TEXT PRIMARY KEY,
        retention_days INTEGER,                  -- NULL: kept until deleted
        sharing_enabled INTEGER NOT NULL DEFAULT 1,
        updated_by  TEXT,
        updated_at  REAL)""",
    """CREATE TABLE IF NOT EXISTS shares (
        id          TEXT PRIMARY KEY,            -- uuid: how its owner revokes it
        token_hash  TEXT NOT NULL UNIQUE,        -- sha256 of the token in the link
        workspace   TEXT NOT NULL,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        message_id  TEXT,                        -- one answer; NULL: the chat up to leaf_id
        leaf_id     TEXT NOT NULL,               -- the last message shared
        created_by  TEXT NOT NULL,               -- members.username
        role_used   TEXT NOT NULL CHECK (role_used IN ('public', 'hr')),
        audience    TEXT NOT NULL DEFAULT 'workspace' CHECK (audience IN ('workspace')),
        expires_at  REAL,
        revoked_at  REAL,
        created_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS shares_by_conversation ON shares(conversation_id)",
)


def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.exec_driver_sql(f"PRAGMA table_info({table})"))


def _answer_access(max_access, sources):
    """An answer's level for search: its own and each source's; anything not
    "public" counts as restricted (kb/services/conversations.max_access)."""
    levels = [max_access or "restricted"]
    try:
        levels += [s.get("access") for s in json.loads(sources or "[]") if s.get("access")]
    except (ValueError, AttributeError):
        levels.append("restricted")
    return "public" if all(level == "public" for level in levels) else "restricted"


def _fill_search(conn):
    """Index what's already saved (a database adopted again may have some rows)."""
    if conn.exec_driver_sql("SELECT 1 FROM conversation_search LIMIT 1").fetchone():
        return
    rows = []
    for cid, title in conn.exec_driver_sql(
            "SELECT id, title FROM conversations WHERE title IS NOT NULL"):
        rows.append((title, cid, None, "public"))
    for mid, cid, author, content, state, max_access, sources in conn.exec_driver_sql(
            "SELECT id, conversation_id, author, content, state, max_access, sources "
            "FROM messages WHERE content != ''"):
        if author == "user":
            rows.append((content, cid, mid, "public"))
        elif state not in (None, "error"):
            rows.append((content, cid, mid, _answer_access(max_access, sources)))
    for row in rows:
        conn.exec_driver_sql("INSERT INTO conversation_search (body, conversation_id, "
                             "message_id, access) VALUES (?, ?, ?, ?)", row)


def upgrade():
    conn = op.get_bind()
    if not _has_column(conn, "messages", "followups"):    # adopted again by legacy.py
        conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN followups TEXT NOT NULL DEFAULT '[]'")
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)
    _fill_search(conn)


def downgrade():
    conn = op.get_bind()
    for table in ("shares", "chat_settings", "conversation_search", "conversation_folders"):
        conn.exec_driver_sql(f"DROP TABLE {table}")
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("followups")
