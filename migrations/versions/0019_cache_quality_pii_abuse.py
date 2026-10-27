"""answer cache, quality signals, PII policy, usage and abuse alerts

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-27

Additive: new columns have defaults (existing rows keep their meaning) and the
rest is new tables.

    question_log.cache_hit       NULL, or exact / semantic: the answer came from the
                                 answer cache (pipeline.md P2-2)
    question_log.unsupported     how many figures the faithfulness check didn't find
                                 in what the answer cited (NULL: not checked)
    question_log.language        the question's language as detected (language.py)
    assistant_settings.answer_cache   off | exact | semantic (the default: exact)
    assistant_settings.cross_lingual  1: a question in another language than the
                                 documents' is also searched translated (P2-4)
    assistant_settings.pii_policy     JSON (pii.py), NULL: the defaults (PL-13)
    eval_candidates.origin       admin (promoted by hand, as before), feedback
                                 (a thumbs-down) or not_found (a content gap), the
                                 last two suggested by the weekly job (P2-3)
    answer_cache      answers kept a few minutes for the same question from the same
                      set of principals, while the index and settings are unchanged
    doc_pii           what the ingest-time scan found per document (counts per
                      entity type, never the values)
    usage_daily       questions, tokens and cost per workspace and day (PL-36)
    abuse_alerts      what the abuse and cost monitor found, for the operator console
    workspace_limits  a workspace's daily spend caps, set by an operator (PL-02)
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

COLUMNS = (
    ("question_log", "cache_hit", "TEXT"),
    ("question_log", "unsupported", "INTEGER"),
    ("question_log", "language", "TEXT"),
    ("assistant_settings", "answer_cache", "TEXT NOT NULL DEFAULT 'exact'"),
    ("assistant_settings", "cross_lingual", "INTEGER NOT NULL DEFAULT 0"),
    ("assistant_settings", "pii_policy", "TEXT"),
    ("eval_candidates", "origin", "TEXT NOT NULL DEFAULT 'admin'"),
)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS answer_cache (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        key         TEXT NOT NULL,               -- sha256: partition + the question, normalised
        partition   TEXT NOT NULL,               -- sha256: workspace, principals, index,
                                                 --   settings, verified answers, space, release
        fingerprint TEXT NOT NULL,               -- sha256 of the asker's principals and role
        username    TEXT,                        -- who asked first (erasure: dsar.py)
        index_version TEXT NOT NULL,             -- collection:version (indexing.current)
        settings_version INTEGER NOT NULL,       -- assistant_store.version
        verified_stamp TEXT NOT NULL,            -- verified_store.stamp
        question    TEXT NOT NULL,               -- normalised
        embedding   BLOB,                        -- float32 unit vector (semantic cache only)
        answer      TEXT NOT NULL,
        sources     TEXT NOT NULL DEFAULT '[]',
        state       TEXT NOT NULL,
        max_access  TEXT NOT NULL,
        acl         TEXT,                        -- JSON: the answer's audience (access.clauses)
        trace       TEXT NOT NULL DEFAULT '[]',
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL,
        hits        INTEGER NOT NULL DEFAULT 0,
        UNIQUE (workspace, key))""",
    "CREATE INDEX IF NOT EXISTS answer_cache_by_partition ON answer_cache(workspace, partition)",
    """CREATE TABLE IF NOT EXISTS doc_pii (
        workspace   TEXT NOT NULL,
        doc_key     TEXT NOT NULL,
        doc_hash    TEXT,                        -- the document's content hash when scanned
        counts      TEXT NOT NULL DEFAULT '{}',  -- JSON: {entity type: distinct values found}
        scanned_at  REAL NOT NULL,
        PRIMARY KEY (workspace, doc_key))""",
    """CREATE TABLE IF NOT EXISTS usage_daily (
        workspace   TEXT NOT NULL,
        day         TEXT NOT NULL,               -- YYYY-MM-DD (UTC)
        questions   INTEGER NOT NULL DEFAULT 0,
        users       INTEGER NOT NULL DEFAULT 0,
        cached      INTEGER NOT NULL DEFAULT 0,  -- served from the answer cache
        tokens_in   INTEGER NOT NULL DEFAULT 0,
        tokens_out  INTEGER NOT NULL DEFAULT 0,
        cost_usd    REAL NOT NULL DEFAULT 0,
        computed_at REAL NOT NULL,
        PRIMARY KEY (workspace, day))""",
    """CREATE TABLE IF NOT EXISTS abuse_alerts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,               -- _platform for sign-ups by address
        kind        TEXT NOT NULL,               -- questions_spike, user_burst, failed_sign_ins,
                                                 --   signups_per_ip, spend_soft, spend_hard
        dedupe      TEXT NOT NULL,               -- one alert per kind, subject and period
        subject     TEXT,                        -- the member or address it is about, if any
        details     TEXT NOT NULL DEFAULT '{}',  -- JSON: the numbers that tripped it
        created_at  REAL NOT NULL,
        notified    TEXT NOT NULL DEFAULT '[]',  -- JSON: who was emailed
        acknowledged_by TEXT,
        acknowledged_at REAL,
        UNIQUE (workspace, kind, dedupe))""",
    "CREATE INDEX IF NOT EXISTS abuse_alerts_by_time ON abuse_alerts(created_at)",
    """CREATE TABLE IF NOT EXISTS workspace_limits (
        workspace   TEXT PRIMARY KEY,
        spend_soft_usd REAL,                     -- NULL: the server's SPEND_SOFT_USD_PER_DAY
        spend_hard_usd REAL,                     -- NULL: the server's SPEND_HARD_USD_PER_DAY
        updated_by  TEXT,
        updated_at  REAL NOT NULL)""",
)


def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade():
    conn = op.get_bind()
    for table, column, kind in COLUMNS:
        if not _has_column(conn, table, column):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = op.get_bind()
    for table in ("workspace_limits", "abuse_alerts", "usage_daily", "doc_pii", "answer_cache"):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    # question_log keeps its new columns (empty for older code): copying it again
    # would drop the trigger that clears a deleted chat's words (0007, 0015).
    for table, column, _ in reversed(COLUMNS):
        if table != "question_log":
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
