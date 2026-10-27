"""connector sync state: sync_state, source_items and sync_runs

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27

Three new tables for the connector framework (sync_store.py), nothing else
touched. A connection is still keyed (workspace, provider), as in `integrations`.
source_items replaces each connector folder's manifest.json: sync_store imports
an existing manifest the first time it meets one (it needs the documents
folder, which only the app knows), and leaves the file where it is.
IF NOT EXISTS: a database whose version table was lost (and so is adopted
again by migrations/legacy.py) may have them already.
"""

from alembic import op

from migrations import dialect

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sync_state (
        workspace   TEXT NOT NULL,
        provider    TEXT NOT NULL,
        cursor      TEXT,                  -- opaque to the framework: a Drive page token,
                                           -- a Notion / Outline watermark, ...
        last_full_sync_at    REAL,         -- the last run that listed everything
        last_incremental_at  REAL,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        pending_deletes      INTEGER,      -- deletes held back by the mass-delete guard
        PRIMARY KEY (workspace, provider))""",
    """CREATE TABLE IF NOT EXISTS source_items (
        workspace   TEXT NOT NULL,
        provider    TEXT NOT NULL,
        source_id   TEXT NOT NULL,         -- the provider's own id (a Drive file id, a URL)
        file_name   TEXT,                  -- its copy in <docs>/connectors/<provider>/;
                                           -- NULL when skipped
        title       TEXT,
        url         TEXT,
        kind        TEXT,                  -- file | page | document | ...
        parent_id   TEXT,                  -- the folder / page / collection it sits in
        version     TEXT,                  -- modified time, etag: unchanged = not fetched again
        content_hash TEXT,                 -- sha256 of the text as written
        mime        TEXT,
        acl         TEXT,                  -- JSON: who can read it at the source (not enforced yet)
        attrs       TEXT,                  -- JSON: provider extras (a crawled page's links, ...)
        modified    TEXT,                  -- as the provider says
        size        INTEGER,
        skipped_reason TEXT,               -- too_large, unsupported, no_access, empty, ...
        synced_at   REAL,
        PRIMARY KEY (workspace, provider, source_id))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS source_items_by_file
        ON source_items(workspace, provider, file_name) WHERE file_name IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS sync_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        provider    TEXT NOT NULL,
        kind        TEXT NOT NULL,          -- full | incremental | imported
        status      TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'done', 'failed', 'held')),
        started_at  REAL NOT NULL,
        finished_at REAL,
        upserted    INTEGER NOT NULL DEFAULT 0,
        deleted     INTEGER NOT NULL DEFAULT 0,
        skipped     INTEGER NOT NULL DEFAULT 0,
        unchanged   INTEGER NOT NULL DEFAULT 0,
        held_deletes INTEGER NOT NULL DEFAULT 0,  -- refused by the mass-delete guard
        limit_reached INTEGER NOT NULL DEFAULT 0, -- stopped at the item limit
        error_code  TEXT,                   -- connectors/base.py ERRORS, never raw text
        error_detail TEXT)                  -- for the server log / admins: no secrets""",
    "CREATE INDEX IF NOT EXISTS sync_runs_by_connection ON sync_runs(workspace, provider, id)",
)


def upgrade():
    conn = dialect.bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)


def downgrade():
    op.drop_table("sync_runs")
    op.drop_table("source_items")
    op.drop_table("sync_state")
