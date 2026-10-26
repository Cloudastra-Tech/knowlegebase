"""SCIM provisioning

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27

An identity provider (Okta, Entra ID, ...) adds, changes and removes members
and their groups through /scim/v2 (scim_store.py, kb/routers/scim.py). Existing
rows are kept as they are:

    scim_tokens        a workspace's bearer tokens for its provider; only a hash
                       of each is kept, and a revoked one stays (who made it, when)
    scim_groups        the groups the provider pushed, and who is in each
    scim_group_members
    scim_group_rules   which of those groups give which role, when SCIM is the
                       workspace's role source (members_store.py)
    members            + what SCIM knows of each person: its id for them, the
                       provider's own id and name for them, and when it deleted
                       them (anonymised later) — all NULL for everyone until then
    sso_events         its CHECK takes the new outcome 'scim' (a table copy:
                       SQLite can't change a CHECK in place; ids, rows and
                       indexes are kept)
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE IF NOT EXISTS scim_tokens (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        hash         TEXT NOT NULL UNIQUE,  -- sha256 of the token
        hint         TEXT NOT NULL,         -- its last 4 characters, to tell tokens apart
        created_by   TEXT NOT NULL,
        created_at   REAL NOT NULL,
        last_used_at REAL,
        revoked_at   REAL)""",
    "CREATE INDEX IF NOT EXISTS scim_tokens_by_workspace ON scim_tokens(workspace)",
    """CREATE TABLE IF NOT EXISTS scim_groups (
        id           TEXT PRIMARY KEY,      -- uuid, our id for it (SCIM "id")
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        display_name TEXT NOT NULL,
        external_id  TEXT,                  -- the provider's id for it
        created_at   REAL NOT NULL,
        updated_at   REAL NOT NULL)""",
    # A group's name is what the role rules match, so it's one group per name.
    """CREATE UNIQUE INDEX IF NOT EXISTS scim_groups_by_name
        ON scim_groups(workspace, display_name COLLATE NOCASE)""",
    """CREATE TABLE IF NOT EXISTS scim_group_members (
        group_id     TEXT NOT NULL REFERENCES scim_groups(id) ON DELETE CASCADE,
        username     TEXT NOT NULL,         -- members.username
        PRIMARY KEY (group_id, username))""",
    "CREATE INDEX IF NOT EXISTS scim_group_members_by_user ON scim_group_members(username)",
    """CREATE TABLE IF NOT EXISTS scim_group_rules (
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        group_name   TEXT NOT NULL,         -- compared without regard to case
        role         TEXT NOT NULL CHECK (role IN ('public', 'hr', 'admin')),
        PRIMARY KEY (workspace, group_name))""",
)

MEMBER_COLUMNS = (
    ("scim_id", "TEXT"),                    # our SCIM "id" for them (uuid)
    ("scim_external_id", "TEXT"),           # the provider's id for them
    ("scim_username", "TEXT"),              # the provider's userName (may not be an email)
    ("scim_profile", "TEXT"),               # JSON: name parts as the provider sent them
    ("scim_updated_at", "REAL"),            # SCIM last changed them
    ("scim_deleted_at", "REAL"),            # the provider deleted them: disabled, then
    ("anonymised_at", "REAL"),              # anonymised after the retention window
)

EVENTS = """CREATE TABLE sso_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace     TEXT NOT NULL,
        connection_id TEXT,
        email         TEXT,
        outcome       TEXT NOT NULL
                      CHECK (outcome IN ('signed_in', 'refused', 'failed', 'test',
                                         'warning', 'updated', 'attention', 'scim')),
        reason        TEXT,                -- why it was refused or failed (a code
                                           -- from sso_errors.py, never provider
                                           -- text), the warning / metadata change,
                                           -- or what SCIM did
        at            REAL NOT NULL,
        ref           TEXT                 -- shown to the person it failed for
        )"""
EVENT_COLUMNS = "id, workspace, connection_id, email, outcome, reason, at, ref"


def upgrade():
    conn = op.get_bind()
    for statement in TABLES:
        conn.exec_driver_sql(statement)
    have = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(members)")}
    for name, kind in MEMBER_COLUMNS:
        if name not in have:
            conn.exec_driver_sql(f"ALTER TABLE members ADD COLUMN {name} {kind}")
    conn.exec_driver_sql("""CREATE UNIQUE INDEX IF NOT EXISTS members_by_scim_id
                            ON members(scim_id) WHERE scim_id IS NOT NULL""")

    conn.exec_driver_sql("ALTER TABLE sso_events RENAME TO sso_events_old")
    conn.exec_driver_sql(EVENTS)
    conn.exec_driver_sql(f"INSERT INTO sso_events ({EVENT_COLUMNS}) "
                         f"SELECT {EVENT_COLUMNS} FROM sso_events_old")
    # Ids of events pruned since aren't handed out again.
    conn.exec_driver_sql("DELETE FROM sqlite_sequence WHERE name = 'sso_events'")
    conn.exec_driver_sql("""INSERT INTO sqlite_sequence (name, seq)
                            SELECT 'sso_events', seq FROM sqlite_sequence
                            WHERE name = 'sso_events_old'""")
    conn.exec_driver_sql("DROP TABLE sso_events_old")
    conn.exec_driver_sql("CREATE INDEX sso_events_by_workspace ON sso_events(workspace, id)")
    conn.exec_driver_sql("CREATE INDEX sso_events_by_connection ON sso_events(connection_id, id)")


def downgrade():
    raise NotImplementedError("SCIM's tables and columns hold members' data: restore a backup")
