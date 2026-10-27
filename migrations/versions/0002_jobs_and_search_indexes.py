"""jobs and search_indexes: the durable job queue, and which Chroma collection
answers each workspace's questions

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27

Two new tables, nothing else touched (kb/jobs.py, kb/services/indexing.py).
IF NOT EXISTS: a database whose version table was lost (and so is adopted
again by migrations/legacy.py) may have them already.
"""

from alembic import op

from migrations import dialect

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS jobs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        kind         TEXT NOT NULL,             -- what to run: index, sync, ...
        workspace    TEXT,                      -- NULL: not one workspace's
        lane         TEXT NOT NULL,             -- at most one job per lane runs at a time
        dedupe_key   TEXT,                      -- a queued job with the same key absorbs a new one
        payload      TEXT NOT NULL DEFAULT '{}',   -- JSON
        status       TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'done', 'failed')),
        attempts     INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        run_after    REAL NOT NULL,             -- not before (retries back off)
        locked_by    TEXT,                      -- the worker running it ...
        locked_until REAL,                      -- ... until its lease runs out
        progress     TEXT,                      -- JSON {step, total, message}
        result       TEXT,                      -- JSON, when done
        error        TEXT,                      -- the last failure
        created_at   REAL NOT NULL,
        started_at   REAL,
        finished_at  REAL)""",
    "CREATE INDEX IF NOT EXISTS jobs_to_claim ON jobs(status, run_after)",
    "CREATE INDEX IF NOT EXISTS jobs_by_lane ON jobs(lane, status)",
    "CREATE INDEX IF NOT EXISTS jobs_by_workspace ON jobs(workspace, started_at)",
    """CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_queued
        ON jobs(dedupe_key) WHERE status = 'queued' AND dedupe_key IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS search_indexes (
        workspace   TEXT PRIMARY KEY,
        collection  TEXT NOT NULL,              -- the Chroma collection questions use
        fingerprint TEXT NOT NULL,              -- chunking + embedding settings it was built with
        generation  INTEGER NOT NULL DEFAULT 0, -- full rebuilds so far (names the next one)
        version     INTEGER NOT NULL DEFAULT 0, -- bumped on every change: agents reload
        updated_at  REAL NOT NULL)""",
)


def upgrade():
    conn = dialect.bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)


def downgrade():
    op.drop_table("search_indexes")
    op.drop_table("jobs")
