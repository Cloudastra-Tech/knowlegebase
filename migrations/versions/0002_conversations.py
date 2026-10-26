"""conversations: saved chats, their messages, and feedback on answers

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27

New tables only; nothing existing changes. A conversation belongs to one person
in one workspace and records the role it was started under (a chat started as
HR never becomes context for a public question: kb/services/conversations.py).
Messages form a tree (parent_id) so editing and regenerating can add siblings
later; the one shown is the path up to conversations.active_leaf_id.

messages.max_access is the highest access level of EVERY chunk the model read
for an answer (cited or not, and the earlier turns it was given), so a reader
whose role can't see that level gets the answer withheld (visible_to()).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS conversations (
        id          TEXT PRIMARY KEY,            -- uuid
        workspace   TEXT NOT NULL,
        owner       TEXT NOT NULL,               -- members.username
        title       TEXT,                        -- from the first question, never an answer
        title_source TEXT CHECK (title_source IN ('auto', 'user')),
        folder_id   TEXT,                        -- history sidebar (later)
        space_id    TEXT,                        -- spaces (later)
        pinned_at   REAL,
        archived_at REAL,
        active_leaf_id TEXT,                     -- the message whose path is shown
        forked_from_message_id TEXT,             -- "Branch in new chat" (later)
        role_used   TEXT NOT NULL CHECK (role_used IN ('public', 'hr')),
        scope       TEXT NOT NULL DEFAULT '{}',  -- JSON: which sources to search (later)
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        deleted_at  REAL)""",
    # The owner's list, newest first; also what a retention purge walks.
    "CREATE INDEX IF NOT EXISTS conversations_by_owner ON conversations(workspace, owner, updated_at DESC)",
    """CREATE TABLE IF NOT EXISTS messages (
        id          TEXT PRIMARY KEY,            -- uuid
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        parent_id   TEXT,                        -- the message before it (NULL: the first)
        author      TEXT NOT NULL CHECK (author IN ('user', 'assistant')),
        content     TEXT NOT NULL,               -- the question, or the final checked answer
        standalone_question TEXT,                -- the follow-up rewritten on its own, as searched
        -- NULL while an answer is being written (and for questions).
        state       TEXT CHECK (state IN ('answered', 'partial', 'not_found', 'blocked',
                                          'general_knowledge', 'error', 'stopped')),
        sources     TEXT NOT NULL DEFAULT '[]',  -- JSON: [{title, provider, url, ...}]
        max_access  TEXT NOT NULL DEFAULT 'public'
                    CHECK (max_access IN ('public', 'restricted')),
        steps       TEXT NOT NULL DEFAULT '[]',  -- JSON: the steps shown [{id, label, result}]
        trace       TEXT NOT NULL DEFAULT '[]',  -- JSON: each tool's input and output (admins)
        scope       TEXT,
        attachment_ids TEXT,
        model       TEXT,
        latency_ms  INTEGER,
        tokens_in   INTEGER,
        tokens_out  INTEGER,
        index_version REAL,                      -- when the index it searched was built
        created_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, created_at)",
    """CREATE TABLE IF NOT EXISTS message_feedback (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        -- Deleting a chat deletes the feedback on it too, with its snapshot.
        message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        user        TEXT NOT NULL,
        workspace   TEXT NOT NULL,
        rating      INTEGER NOT NULL CHECK (rating IN (-1, 1)),
        reasons     TEXT NOT NULL DEFAULT '[]',  -- JSON list of reason codes
        comment     TEXT,
        snapshot    TEXT NOT NULL DEFAULT '{}',  -- JSON: {question, standalone_question,
                                                 --   answer, state, sources, max_access}
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        UNIQUE (message_id, user))""",
    "CREATE INDEX IF NOT EXISTS message_feedback_by_workspace ON message_feedback(workspace, updated_at DESC)",
)


def upgrade():
    conn = op.get_bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)       # as written: no bind-parameter parsing


def downgrade():
    conn = op.get_bind()
    for table in ("message_feedback", "messages", "conversations"):
        conn.exec_driver_sql(f"DROP TABLE {table}")
