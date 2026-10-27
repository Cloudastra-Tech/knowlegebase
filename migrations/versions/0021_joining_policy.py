"""joining policy, join requests and workspace claim codes

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-27

Who may join a workspace by signing in with an email on one of its verified
domains (join_store.py):

    workspaces.join_policy         auto | request | invite_only. A workspace made
                                   from now on starts at 'request'; every workspace
                                   that exists now is set to 'auto', which is what
                                   it did before this migration (anyone on a
                                   verified domain was added at their first sign-in)
    workspace_domains.join_policy  the same for one verified domain; NULL: the
                                   workspace's
    join_requests      someone's sign-in under 'request': pending until an admin
                       approves (with a role) or denies it, or it expires
    claim_codes        one-time codes an operator issues so someone can claim a
                       workspace nobody can administer any more, with Google
                       Workspace (only the SHA-256 of a code is kept)

Additive only. Every new table has a `workspace` column, so deleting a workspace
deletes its rows (kb/services/lifecycle.py).
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

POLICIES = "'auto', 'request', 'invite_only'"

COLUMNS = (
    ("workspaces", "join_policy",
     f"TEXT NOT NULL DEFAULT 'request' CHECK (join_policy IN ({POLICIES}))"),
    ("workspace_domains", "join_policy", f"TEXT CHECK (join_policy IN ({POLICIES}))"),
)

TABLES = (
    """CREATE TABLE IF NOT EXISTS join_requests (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        email       TEXT NOT NULL,
        name        TEXT,
        provider    TEXT,                       -- what they signed in with (google, a
                                                -- connection's id, microsoft365, ...)
        status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
        role        TEXT,                       -- approved: the role they were given
        created_at  REAL NOT NULL,
        last_seen_at REAL NOT NULL,             -- their latest sign-in while pending
        expires_at  REAL NOT NULL,
        notified_at REAL,                       -- the admins were emailed about it
        decided_at  REAL,
        decided_by  TEXT)""",
    # One open request per person per workspace.
    "CREATE UNIQUE INDEX IF NOT EXISTS join_requests_one_pending "
    "ON join_requests(workspace, email) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS join_requests_by_email ON join_requests(workspace, email, "
    "decided_at)",
    """CREATE TABLE IF NOT EXISTS claim_codes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code_hash   TEXT NOT NULL UNIQUE,       -- sha256 of the code, never the code
        workspace   TEXT NOT NULL,
        domain      TEXT NOT NULL,              -- the Google Workspace domain that may claim
        created_by  TEXT NOT NULL,              -- the operator
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL,
        used_at     REAL,
        used_by     TEXT,
        revoked_at  REAL)""",
)


def upgrade():
    conn = op.get_bind()
    for table, name, kind in COLUMNS:
        have = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        if name not in have:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            if table == "workspaces":
                # Existing workspaces keep today's behaviour: anyone on a verified domain
                # joins at their first sign-in. New ones get the column's default.
                conn.exec_driver_sql("UPDATE workspaces SET join_policy = 'auto'")
    for statement in TABLES:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = op.get_bind()
    for table in ("claim_codes", "join_requests"):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    for table, name, _ in COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(name)
