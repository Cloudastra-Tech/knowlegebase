"""verified answers, source rules, assistant settings and glossary

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-27

Additive only: new tables, and one new column (NULL on existing rows).

    verified_answers          canonical answers an HR person or admin approved
                              (platform.md PL-27, chat.md C18): their question
                              variants, the documents they rest on, who sees them,
                              who owns them and when they're due for review
    verified_answer_vectors   each answer's question variants, embedded: the small
                              per-workspace "collection" a question is matched
                              against (kept here, not in the search index)
    doc_rules                 boost, demote or block documents by provider, folder
                              or document (PL-28)
    assistant_settings        a workspace's assistant: name, tone, extra
                              instructions, answer language, outbound tools, the
                              steps panel (PL-29). No row = today's behaviour
    glossary                  the workspace's terms, definitions and synonyms
    messages.verified         an answer served from a verified answer: which one,
                              by whom, when (JSON), for the badge on reload
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS verified_answers (
        id          TEXT PRIMARY KEY,
        workspace   TEXT NOT NULL,
        title       TEXT NOT NULL,
        variants    TEXT NOT NULL DEFAULT '[]',     -- JSON: the questions it answers
        answer_md   TEXT NOT NULL,
        sources     TEXT NOT NULL DEFAULT '[]',     -- JSON: document keys (ingest.doc_files)
        access      TEXT NOT NULL DEFAULT 'restricted',   -- public | restricted: who it's for
        min_access  TEXT NOT NULL DEFAULT 'public',   -- never lower: what it was made from
        owner       TEXT NOT NULL,                  -- the member who keeps it current
        verified_by TEXT NOT NULL,
        verified_at REAL NOT NULL,
        review_days INTEGER NOT NULL DEFAULT 90,
        status      TEXT NOT NULL DEFAULT 'current',  -- current | due | expired (nightly job)
        notified    TEXT,                           -- the status its owner was last emailed about
        from_message TEXT,                          -- made from this chat answer
        from_gap    INTEGER,                        -- ... or this content gap
        created_by  TEXT NOT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS verified_answers_by_workspace ON verified_answers(workspace)",
    """CREATE TABLE IF NOT EXISTS verified_answer_vectors (
        answer_id   TEXT NOT NULL REFERENCES verified_answers(id) ON DELETE CASCADE,
        n           INTEGER NOT NULL,               -- 0: the title, then each variant
        text        TEXT NOT NULL,
        model       TEXT NOT NULL,
        embedding   BLOB NOT NULL,                  -- float32
        PRIMARY KEY (answer_id, n))""",
    """CREATE TABLE IF NOT EXISTS doc_rules (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        match_type  TEXT NOT NULL,                  -- provider | folder | doc_key
        match_value TEXT NOT NULL,
        action      TEXT NOT NULL,                  -- boost | demote | block
        weight      REAL NOT NULL DEFAULT 1,        -- multiplies the fused score
        created_by  TEXT NOT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        UNIQUE (workspace, match_type, match_value))""",
    """CREATE TABLE IF NOT EXISTS assistant_settings (
        workspace   TEXT PRIMARY KEY,
        assistant_name TEXT,
        tone        TEXT NOT NULL DEFAULT 'default',
        instructions TEXT NOT NULL DEFAULT '',
        language    TEXT NOT NULL DEFAULT 'auto',
        allow_wikipedia INTEGER,                    -- NULL: the server's ALLOW_WIKIPEDIA
        allow_currency  INTEGER,                    -- NULL: the server's ALLOW_CURRENCY
        show_steps  INTEGER NOT NULL DEFAULT 1,
        adapt_verified INTEGER NOT NULL DEFAULT 0,  -- the FAST model may reword a verified answer
        version     INTEGER NOT NULL DEFAULT 0,     -- changes with settings, glossary or rules
        updated_by  TEXT,
        updated_at  REAL)""",
    """CREATE TABLE IF NOT EXISTS glossary (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        term        TEXT NOT NULL,
        definition  TEXT NOT NULL,
        synonyms    TEXT NOT NULL DEFAULT '[]',     -- JSON
        created_by  TEXT NOT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS glossary_term ON glossary(workspace, term COLLATE NOCASE)",
)


def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade():
    conn = op.get_bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)
    if not _has_column(conn, "messages", "verified"):
        conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN verified TEXT")


def downgrade():
    conn = op.get_bind()
    for table in ("glossary", "assistant_settings", "doc_rules", "verified_answer_vectors",
                  "verified_answers"):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("verified")
