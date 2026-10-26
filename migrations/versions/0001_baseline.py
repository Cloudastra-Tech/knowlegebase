"""baseline: the schema as the stores made it before Alembic

Revision ID: 0001
Revises:
Create Date: 2026-10-27

Every table, index and constraint config/app.db had when its schema moved to
Alembic (members_store, sessions_store and integrations_store each made their
own). Columns are in the order an existing database has them, later ones last.
A database from before this is brought here by migrations/legacy.py instead.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE workspaces (
        slug        TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  REAL NOT NULL,
        require_sso INTEGER NOT NULL DEFAULT 0,   -- passwords off, except for admins
        role_source TEXT,                         -- the provider whose groups set roles
        group_admin_days INTEGER NOT NULL DEFAULT 30)""",
    """CREATE TABLE signup_codes_used (
        code_hash  TEXT PRIMARY KEY,           -- sha256 of an invite code (WORKSPACE_SIGNUP_CODES)
        workspace  TEXT NOT NULL,
        used_at    REAL NOT NULL)""",
    """CREATE TABLE workspace_domains (
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        domain       TEXT NOT NULL,
        verify_token TEXT,                 -- for the DNS check (sign-in settings)
        verified_at  REAL,                 -- NULL until the workspace proves it owns it
        PRIMARY KEY (workspace, domain))""",
    # A domain can be claimed by several workspaces, but VERIFIED by one at most.
    """CREATE UNIQUE INDEX one_verified_owner
        ON workspace_domains(domain) WHERE verified_at IS NOT NULL""",
    """CREATE TABLE members (
        username      TEXT PRIMARY KEY,    -- the email for single sign-on
        workspace     TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'public'
                      CHECK (role IN ('public', 'hr', 'admin')),
        password_hash TEXT,                -- NULL: single sign-on only
        created_at    REAL NOT NULL,
        last_login_at REAL,
        override_role TEXT,                -- set by an admin
        group_role    TEXT,                -- from their groups at the role source
        groups        TEXT,                -- JSON list, as last sent
        disabled      INTEGER NOT NULL DEFAULT 0,
        group_source  TEXT,                -- the provider that set group_role
        groups_updated_at REAL             -- when the role source last sent them
        )""",
    """CREATE TABLE sso_connections (
        id            TEXT PRIMARY KEY,
        workspace     TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        protocol      TEXT NOT NULL CHECK (protocol IN ('oidc', 'saml')),
        label         TEXT NOT NULL,       -- the sign-in button: "Continue with <label>"
        issuer        TEXT,                -- OIDC issuer, or the SAML IdP's entity ID
        client_id     TEXT,                -- OIDC only
        secret        BLOB,                -- encrypted (secret_box.py): the OIDC client
                                           -- secret, or our SAML signing key
        metadata      TEXT NOT NULL DEFAULT '{}',  -- endpoints / IdP metadata, as checked
        enabled       INTEGER NOT NULL DEFAULT 1,
        created_by    TEXT,
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL,
        last_login_at REAL,
        group_claim   TEXT,
        logout_at_idp INTEGER NOT NULL DEFAULT 0,
        -- SAML metadata fetched again from its URL: the last success, the last
        -- try, when it's next due, why a fetched change wasn't applied, and what
        -- went wrong last time.
        metadata_refreshed_at REAL,
        metadata_fetched_at   REAL,
        metadata_due_at       REAL,
        needs_review          TEXT,
        metadata_error        TEXT)""",
    """CREATE TABLE saml_requests (
        request_id    TEXT PRIMARY KEY,
        connection_id TEXT NOT NULL REFERENCES sso_connections(id) ON DELETE CASCADE,
        expires       REAL NOT NULL)""",
    """CREATE TABLE saml_assertions (
        assertion_id  TEXT PRIMARY KEY,
        expires       REAL NOT NULL)""",
    """CREATE TABLE sso_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace     TEXT NOT NULL,
        connection_id TEXT,
        email         TEXT,
        outcome       TEXT NOT NULL
                      CHECK (outcome IN ('signed_in', 'refused', 'failed', 'test',
                                         'warning', 'updated', 'attention')),
        reason        TEXT,                -- why it was refused or failed (a code
                                           -- from sso_errors.py, never provider
                                           -- text), or the warning / metadata change
        at            REAL NOT NULL,
        ref           TEXT                 -- shown to the person it failed for
        )""",
    "CREATE INDEX sso_events_by_workspace ON sso_events(workspace, id)",
    "CREATE INDEX sso_events_by_connection ON sso_events(connection_id, id)",
    """CREATE TABLE group_role_rules (
        connection_id TEXT NOT NULL REFERENCES sso_connections(id) ON DELETE CASCADE,
        group_name    TEXT NOT NULL,       -- compared without regard to case
        role          TEXT NOT NULL CHECK (role IN ('public', 'hr', 'admin')),
        PRIMARY KEY (connection_id, group_name))""",
    """CREATE TABLE imports (
        source      TEXT PRIMARY KEY,      -- an old config file, copied in once
        imported_at REAL NOT NULL)""",
    """CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user       TEXT NOT NULL,     -- JSON: username, name, workspace
        expires    REAL NOT NULL,
        username   TEXT,              -- lower case: whose session it is
        workspace  TEXT)""",
    "CREATE INDEX sessions_by_user ON sessions (username)",
    """CREATE TABLE integrations (
        workspace    TEXT NOT NULL,
        provider     TEXT NOT NULL,
        status       TEXT NOT NULL,          -- connected | syncing | error
        account      TEXT,                   -- e.g. the Google account email
        secret       BLOB,                   -- encrypted JSON (tokens)
        settings     TEXT DEFAULT '{}',      -- JSON (e.g. chosen folder)
        connected_by TEXT,
        connected_at REAL,
        last_sync_at REAL,
        last_error   TEXT,
        doc_count    INTEGER DEFAULT 0,
        PRIMARY KEY (workspace, provider))""",
)


def upgrade():
    conn = op.get_bind()
    for statement in SCHEMA:
        conn.exec_driver_sql(statement)       # as written: no bind-parameter parsing


def downgrade():
    raise NotImplementedError("going below the baseline would drop every table")
