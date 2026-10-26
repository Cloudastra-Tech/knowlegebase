"""API keys and the public API's settings

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27

Keys for the versioned public API (/api/v1) and the MCP server (/mcp), and what
a workspace's admins expose through them (api_keys_store.py). New tables only:
nothing that exists is changed.

    api_keys       a key: "kb_live_<prefix>_<secret>". Only a SHA-256 hash of it
                   is kept, with its prefix to find it; a revoked or expired one
                   stays (who made it, when, what it could do)
    api_settings   per workspace: the API and the MCP server on or off, and which
                   MCP tools are exposed. No row: the defaults (api_keys_store.py)
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE IF NOT EXISTS api_keys (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        name         TEXT NOT NULL,
        kind         TEXT NOT NULL DEFAULT 'personal'
                     CHECK (kind IN ('personal', 'service')),
                                            -- personal: acts as created_by, with their
                                            -- role as it is at each call; service: acts
                                            -- as a public user of the workspace
        prefix       TEXT NOT NULL UNIQUE,  -- the key's public part, to find it by
        hash         TEXT NOT NULL,         -- sha256 of the whole key
        scopes       TEXT NOT NULL DEFAULT '[]',   -- JSON: ask, search, docs:read, ...
        created_by   TEXT NOT NULL,
        created_at   REAL NOT NULL,
        last_used_at REAL,
        expires_at   REAL,
        revoked_at   REAL,
        revoked_by   TEXT)""",
    "CREATE INDEX IF NOT EXISTS api_keys_by_workspace ON api_keys(workspace, created_by)",
    """CREATE TABLE IF NOT EXISTS api_settings (
        workspace    TEXT PRIMARY KEY REFERENCES workspaces(slug) ON DELETE CASCADE,
        api_enabled  INTEGER NOT NULL DEFAULT 1,
        mcp_enabled  INTEGER NOT NULL DEFAULT 1,
        mcp_tools    TEXT,                  -- JSON list of the tools exposed; NULL: defaults
        updated_by   TEXT,
        updated_at   REAL)""",
)


def upgrade():
    conn = op.get_bind()
    for statement in TABLES:
        conn.exec_driver_sql(statement)


def downgrade():
    conn = op.get_bind()
    conn.exec_driver_sql("DROP TABLE IF EXISTS api_settings")
    conn.exec_driver_sql("DROP TABLE IF EXISTS api_keys")
