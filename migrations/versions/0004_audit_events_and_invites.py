"""audit events and invites

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27

Two new tables, nothing existing changes:

    audit_events   who changed what in a workspace, hash-chained per workspace
                   so an edited or deleted row shows (audit_store.py)
    invites        an admin invites someone by email (invites_store.py); only a
                   hash of the link's token is kept
"""

from alembic import op

from migrations import dialect

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS audit_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace   TEXT NOT NULL,
        at          REAL NOT NULL,
        actor       TEXT,                  -- username, or "cli" for the operator
        actor_type  TEXT NOT NULL
                    CHECK (actor_type IN ('user', 'api_key', 'scim', 'system', 'operator')),
        action      TEXT NOT NULL,         -- e.g. member.update, invite.create
        target_type TEXT,
        target_id   TEXT,
        ip          TEXT,
        user_agent  TEXT,
        details     TEXT NOT NULL DEFAULT '{}',   -- JSON, canonical (sorted keys)
        prev_hash   TEXT NOT NULL,         -- the workspace's previous row's hash
        hash        TEXT NOT NULL)         -- sha256(prev_hash + this row, canonical)""",
    "CREATE INDEX IF NOT EXISTS audit_events_by_workspace ON audit_events(workspace, id)",
    """CREATE TABLE IF NOT EXISTS invites (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash   TEXT NOT NULL UNIQUE, -- sha256 of the link's token
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        email        TEXT NOT NULL,
        role         TEXT NOT NULL CHECK (role IN ('public', 'hr', 'admin')),
        invited_by   TEXT NOT NULL,
        created_at   REAL NOT NULL,
        sent_at      REAL,                 -- last emailed (resend sends a new link)
        expires_at   REAL NOT NULL,
        accepted_at  REAL,
        revoked_at   REAL)""",
    "CREATE INDEX IF NOT EXISTS invites_by_workspace ON invites(workspace, email)",
)


def upgrade():
    conn = dialect.bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)


def downgrade():
    op.drop_table("invites")
    op.drop_table("audit_events")
