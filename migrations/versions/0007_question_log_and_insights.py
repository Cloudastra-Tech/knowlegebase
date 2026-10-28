"""question log and admin insights

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27

Two new settings on `workspaces` (with defaults, so existing rows keep working)
and new tables; nothing existing changes otherwise.

    workspaces.question_log_mode   full | redacted | metadata: how much of a
                                   question's and answer's TEXT the log keeps
    workspaces.question_log_days   how long that text is kept
    question_log      one row per question asked, from any surface (the web chat,
                      the API, later Slack and MCP), saved in a conversation or
                      not (question_log_store.py)
    insights_daily    the log rolled up per workspace and day (nightly job)
    source_stats      how often each document was cited, per day
    insights_docs     the documents the assistant can search, as last seen,
                      with when each was first seen (never-cited and stale)
    content_gaps      unanswered questions clustered by meaning (weekly job)
    eval_candidates   answers an admin promoted from feedback to the test set

The log is its own table rather than more columns on `messages`: questions
asked outside a saved conversation have no message, the log's text follows the
workspace's retention mode (a redacted copy) while the person's own chat keeps
what they typed, and its metadata outlives a deleted chat for the counts. When
a chat is deleted, the trigger below clears the text logged for its messages,
so deleting a conversation removes its words from the log too.
"""

from alembic import op

from migrations import dialect

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

COLUMNS = (
    ("workspaces", "question_log_mode",
     "TEXT NOT NULL DEFAULT 'redacted' "
     "CHECK (question_log_mode IN ('full', 'redacted', 'metadata'))"),
    ("workspaces", "question_log_days", "INTEGER NOT NULL DEFAULT 90"),
)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS question_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        at          REAL NOT NULL,
        day         TEXT NOT NULL,               -- YYYY-MM-DD (UTC) of `at`
        user        TEXT NOT NULL,               -- members.username (never shown in insights)
        surface     TEXT NOT NULL CHECK (surface IN ('web', 'api', 'slack', 'mcp')),
        message_id  TEXT,                        -- messages.id when saved in a conversation
        role        TEXT NOT NULL DEFAULT 'public',  -- the role it was asked as
        text_mode   TEXT NOT NULL CHECK (text_mode IN ('full', 'redacted', 'metadata')),
        question    TEXT,                        -- per text_mode; NULL under metadata,
        answer      TEXT,                        --   and once question_log_days have passed
        outcome     TEXT NOT NULL CHECK (outcome IN ('answered', 'partial', 'not_found',
                    'blocked', 'general_knowledge', 'error', 'stopped')),
        block_reason TEXT,                       -- injection | restricted_topic | policy | other
        sources     TEXT NOT NULL DEFAULT '[]',  -- JSON: [{doc_id, doc_key, title, provider,
                                                 --   url, access, kind}] as cited
        tools       TEXT NOT NULL DEFAULT '[]',  -- JSON: the tools it used, in order, once each
        max_access  TEXT NOT NULL DEFAULT 'restricted'
                    CHECK (max_access IN ('public', 'restricted')),
        latency_ms  INTEGER,
        tokens_in   INTEGER,
        tokens_out  INTEGER,
        model       TEXT,
        embedding   BLOB)                        -- float32 vector of the question (gaps)""",
    "CREATE INDEX IF NOT EXISTS question_log_by_day ON question_log(workspace, day)",
    "CREATE INDEX IF NOT EXISTS question_log_by_message ON question_log(message_id) "
    "WHERE message_id IS NOT NULL",
    # Deleting a chat (or one of its messages) clears what was logged of its words.
    """CREATE TRIGGER IF NOT EXISTS question_log_forget_message
        AFTER DELETE ON messages BEGIN
            UPDATE question_log SET question = NULL, answer = NULL, embedding = NULL,
                                    message_id = NULL
             WHERE message_id = OLD.id;
        END""",
    """CREATE TABLE IF NOT EXISTS insights_daily (
        workspace   TEXT NOT NULL,
        day         TEXT NOT NULL,
        questions   INTEGER NOT NULL DEFAULT 0,
        active_users INTEGER NOT NULL DEFAULT 0,
        answered    INTEGER NOT NULL DEFAULT 0,
        partial     INTEGER NOT NULL DEFAULT 0,
        not_found   INTEGER NOT NULL DEFAULT 0,
        blocked     INTEGER NOT NULL DEFAULT 0,
        general_knowledge INTEGER NOT NULL DEFAULT 0,
        error       INTEGER NOT NULL DEFAULT 0,
        stopped     INTEGER NOT NULL DEFAULT 0,
        latency_hist TEXT NOT NULL DEFAULT '[]', -- JSON: counts per insights.LATENCY_BUCKETS
        tokens_in   INTEGER NOT NULL DEFAULT 0,
        tokens_out  INTEGER NOT NULL DEFAULT 0,
        cost_usd    REAL NOT NULL DEFAULT 0,
        computed_at REAL NOT NULL,
        PRIMARY KEY (workspace, day))""",
    """CREATE TABLE IF NOT EXISTS source_stats (
        workspace   TEXT NOT NULL,
        doc_key     TEXT NOT NULL,               -- ingest's key, or provider:title when unknown
        day         TEXT NOT NULL,
        title       TEXT,
        provider    TEXT,
        url         TEXT,
        access      TEXT NOT NULL DEFAULT 'restricted',
        cited       INTEGER NOT NULL DEFAULT 0,  -- questions that cited it that day
        PRIMARY KEY (workspace, doc_key, day))""",
    """CREATE TABLE IF NOT EXISTS insights_docs (
        workspace   TEXT NOT NULL,
        doc_key     TEXT NOT NULL,               -- public/handbook.txt, connectors/notion/x.txt
        title       TEXT,
        provider    TEXT,
        url         TEXT,
        access      TEXT NOT NULL DEFAULT 'restricted',
        modified    REAL,                        -- when the document last changed
        first_seen  REAL NOT NULL,               -- first time it was searchable (as far as known)
        last_seen   REAL NOT NULL,
        PRIMARY KEY (workspace, doc_key))""",
    """CREATE TABLE IF NOT EXISTS content_gaps (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        computed_at REAL NOT NULL,
        since       REAL NOT NULL,               -- the questions clustered: asked since
        label       TEXT,                        -- one cheap model call over its samples
        size        INTEGER NOT NULL,
        not_found   INTEGER NOT NULL DEFAULT 0,
        general_knowledge INTEGER NOT NULL DEFAULT 0,
        max_access  TEXT NOT NULL DEFAULT 'restricted',  -- of every question in it
        log_ids     TEXT NOT NULL DEFAULT '[]')  -- JSON: question_log ids, nearest first""",
    "CREATE INDEX IF NOT EXISTS content_gaps_by_workspace ON content_gaps(workspace, computed_at)",
    """CREATE TABLE IF NOT EXISTS eval_candidates (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        message_id  TEXT NOT NULL,               -- the rated answer (it may be deleted since)
        question    TEXT NOT NULL,
        answer      TEXT,                        -- what the assistant said
        expected_answer TEXT,                    -- what it should have said, if given
        sources     TEXT NOT NULL DEFAULT '[]',  -- JSON: the answer's sources
        reasons     TEXT NOT NULL DEFAULT '[]',  -- JSON: the thumbs-down reasons
        comment     TEXT,
        role        TEXT NOT NULL CHECK (role IN ('public', 'hr')),
        max_access  TEXT NOT NULL DEFAULT 'restricted',
        created_by  TEXT NOT NULL,
        created_at  REAL NOT NULL,
        UNIQUE (workspace, message_id))""",
)


def _has_column(conn, table, column):
    return dialect.has_column(conn, table, column)


def upgrade():
    conn = dialect.bind()
    for table, column, definition in COLUMNS:
        if not _has_column(conn, table, column):    # adopted again by migrations/legacy.py
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    postgres = dialect.postgres(conn)
    for statement in SCHEMA:
        if postgres and statement.lstrip().startswith("CREATE TRIGGER"):
            for pg_statement in PG_TRIGGER:
                dialect.native(conn).exec_driver_sql(pg_statement)
            continue
        conn.exec_driver_sql(statement)


# The same trigger on Postgres: a function, run after each deleted message.
PG_TRIGGER = (
    """CREATE OR REPLACE FUNCTION question_log_forget_message() RETURNS trigger
       LANGUAGE plpgsql AS $$
       BEGIN
           UPDATE question_log SET question = NULL, answer = NULL, embedding = NULL,
                                   message_id = NULL
            WHERE message_id = OLD.id;
           RETURN OLD;
       END $$""",
    "DROP TRIGGER IF EXISTS question_log_forget_message ON messages",
    """CREATE TRIGGER question_log_forget_message AFTER DELETE ON messages
       FOR EACH ROW EXECUTE FUNCTION question_log_forget_message()""",
)


def downgrade():
    conn = dialect.bind()
    if dialect.postgres(conn):
        dialect.native(conn).exec_driver_sql(
            "DROP TRIGGER IF EXISTS question_log_forget_message ON messages")
    else:
        conn.exec_driver_sql("DROP TRIGGER IF EXISTS question_log_forget_message")
    for table in ("eval_candidates", "content_gaps", "insights_docs", "source_stats",
                  "insights_daily", "question_log"):
        conn.exec_driver_sql(f"DROP TABLE {table}")
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_column("question_log_days")
        batch.drop_column("question_log_mode")
