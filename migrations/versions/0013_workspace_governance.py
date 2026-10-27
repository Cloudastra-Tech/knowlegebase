"""workspace lifecycle, DSAR, operator support access and per-workspace keys

Revision ID: 0013
Revises: 0011
Create Date: 2026-10-27

Governance (platform.md PL-05, PL-06, PL-12, PL-18). New columns on workspaces,
all empty for an existing workspace (it stays active, keeps its audit log for
good and gives support no access), and new tables:

    workspaces.suspended_*      an operator suspended it: nobody signs in, no API
                                key, SCIM token or Slack request works
    workspaces.deleting_at      an admin asked to delete it: it is purged then
                                (lifecycle_store.py); until then only admins
                                sign in, to export or cancel
    workspaces.support_until    the admin lets Cloudastra support in until then
    workspaces.audit_days       audit events older than this are deleted (at
                                least 365; NULL: kept for good)
    workspace_keys     each workspace's data keys, wrapped by the server's key
                       (secret_box.py); deleting them crypto-shreds its secrets
    workspace_exports  an admin's export of the workspace (a ZIP kept 24 h)
    audit_pseudonyms   people erased under a data request: their audit rows
                       stay as hashed, and are shown under a pseudonym
    audit_anchors      where a workspace's audit chain starts once old events
                       are deleted for retention, so it still verifies
"""

from alembic import op

revision = "0013"
down_revision = "0011"
branch_labels = None
depends_on = None

COLUMNS = (
    ("suspended_at", "REAL"),
    ("suspended_reason", "TEXT"),
    ("suspended_by", "TEXT"),
    ("deleting_at", "REAL"),
    ("delete_requested_at", "REAL"),
    ("delete_requested_by", "TEXT"),
    ("support_until", "REAL"),
    ("support_granted_by", "TEXT"),
    ("audit_days", "INTEGER"),
)

TABLES = (
    """CREATE TABLE IF NOT EXISTS workspace_keys (
        workspace    TEXT NOT NULL,
        version      INTEGER NOT NULL,      -- 1, 2, ...: the newest seals
        wrapped_dek  BLOB NOT NULL,         -- the data key, encrypted by the server's key
        created_at   REAL NOT NULL,
        retired_at   REAL,                  -- rotated out: still opens what it sealed
        PRIMARY KEY (workspace, version))""",
    """CREATE TABLE IF NOT EXISTS workspace_exports (
        id           TEXT PRIMARY KEY,      -- uuid
        workspace    TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'done', 'failed')),
        file_name    TEXT,                  -- under lifecycle_store.EXPORT_ROOT/<workspace>/
        size         INTEGER,
        counts       TEXT,                  -- JSON: rows / files in each part
        error        TEXT,
        created_at   REAL NOT NULL,
        finished_at  REAL,
        expires_at   REAL)""",
    "CREATE INDEX IF NOT EXISTS workspace_exports_by_workspace "
    "ON workspace_exports(workspace, created_at)",
    """CREATE TABLE IF NOT EXISTS audit_pseudonyms (
        workspace    TEXT NOT NULL,
        actor_hash   TEXT NOT NULL,         -- sha256 of the erased username, never the name
        pseudonym    TEXT NOT NULL,         -- erased:<hash>, shown instead
        erased_at    REAL NOT NULL,
        PRIMARY KEY (workspace, actor_hash))""",
    """CREATE TABLE IF NOT EXISTS audit_anchors (
        workspace    TEXT PRIMARY KEY,
        last_id      INTEGER NOT NULL,      -- the newest event deleted for retention
        last_hash    TEXT NOT NULL,         -- its hash: the next event's prev_hash
        purged       INTEGER NOT NULL DEFAULT 0,
        updated_at   REAL NOT NULL)""",
)


def upgrade():
    conn = op.get_bind()
    have = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(workspaces)")}
    for name, kind in COLUMNS:
        if name not in have:
            conn.exec_driver_sql(f"ALTER TABLE workspaces ADD COLUMN {name} {kind}")
    for statement in TABLES:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = op.get_bind()
    for table in ("audit_anchors", "audit_pseudonyms", "workspace_exports", "workspace_keys"):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    with op.batch_alter_table("workspaces") as batch:
        for name, _ in COLUMNS:
            batch.drop_column(name)
