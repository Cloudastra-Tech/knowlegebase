"""
migrations/legacy.py  —  a config/app.db from before Alembic, brought to the baseline.

Until Alembic, each store made its own tables on first use (CREATE TABLE IF NOT
EXISTS), added the columns it was missing (ALTER TABLE ADD COLUMN), ran one-off
data fixes marked done by a row in `imports`, and copied sso_events into a new
table to widen its CHECK. Databases made by that code, at any age, are found by
having app tables but no alembic_version table. adopt() runs those same steps,
whichever it still lacks, then env.py marks the database as at BASELINE (the
schema migrations/versions/0001_baseline.py makes on an empty database) and
upgrades it from there like any other.

This is that code, moved here from members_store.py, sessions_store.py and
integrations_store.py and frozen: it describes databases that already exist,
so it never changes. Schema changes from now on are new migrations.
"""

import json
import logging
import re
import time

BASELINE = "0001"

log = logging.getLogger(__name__)

# The tables the stores made, as they made them (later columns: _NEW_COLUMNS).
_TABLES = (
    """CREATE TABLE IF NOT EXISTS workspaces (
        slug        TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS signup_codes_used (
        code_hash  TEXT PRIMARY KEY,           -- sha256 of an invite code (WORKSPACE_SIGNUP_CODES)
        workspace  TEXT NOT NULL,
        used_at    REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS workspace_domains (
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        domain       TEXT NOT NULL,
        verify_token TEXT,                 -- for the DNS check (sign-in settings)
        verified_at  REAL,                 -- NULL until the workspace proves it owns it
        PRIMARY KEY (workspace, domain))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_verified_owner
        ON workspace_domains(domain) WHERE verified_at IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS members (
        username      TEXT PRIMARY KEY,    -- the email for single sign-on
        workspace     TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'public'
                      CHECK (role IN ('public', 'hr', 'admin')),
        password_hash TEXT,                -- NULL: single sign-on only
        created_at    REAL NOT NULL,
        last_login_at REAL)""",
    """CREATE TABLE IF NOT EXISTS sso_connections (
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
        last_login_at REAL)""",
    """CREATE TABLE IF NOT EXISTS saml_requests (
        request_id    TEXT PRIMARY KEY,
        connection_id TEXT NOT NULL REFERENCES sso_connections(id) ON DELETE CASCADE,
        expires       REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS saml_assertions (
        assertion_id  TEXT PRIMARY KEY,
        expires       REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sso_events (
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
        at            REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS sso_events_by_workspace ON sso_events(workspace, id)",
    "CREATE INDEX IF NOT EXISTS sso_events_by_connection ON sso_events(connection_id, id)",
    """CREATE TABLE IF NOT EXISTS group_role_rules (
        connection_id TEXT NOT NULL REFERENCES sso_connections(id) ON DELETE CASCADE,
        group_name    TEXT NOT NULL,       -- compared without regard to case
        role          TEXT NOT NULL CHECK (role IN ('public', 'hr', 'admin')),
        PRIMARY KEY (connection_id, group_name))""",
    """CREATE TABLE IF NOT EXISTS imports (
        source      TEXT PRIMARY KEY,      -- an old config file, copied in once
        imported_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user       TEXT NOT NULL,     -- JSON: username, name, workspace
        expires    REAL NOT NULL,
        username   TEXT,              -- lower case: whose session it is
        workspace  TEXT)""",
    """CREATE TABLE IF NOT EXISTS integrations (
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
APP_TABLES = {"workspaces", "signup_codes_used", "workspace_domains", "members",
              "sso_connections", "saml_requests", "saml_assertions", "sso_events",
              "group_role_rules", "imports", "sessions", "integrations"}

# Columns added after the first release of a table: (table, column, definition).
_NEW_COLUMNS = (
    ("members", "override_role", "TEXT"),
    ("members", "group_role", "TEXT"),
    ("members", "groups", "TEXT"),
    ("members", "disabled", "INTEGER NOT NULL DEFAULT 0"),
    ("members", "group_source", "TEXT"),
    ("members", "groups_updated_at", "REAL"),
    ("sso_connections", "group_claim", "TEXT"),
    ("sso_connections", "logout_at_idp", "INTEGER NOT NULL DEFAULT 0"),
    ("sso_connections", "metadata_refreshed_at", "REAL"),
    ("sso_connections", "metadata_fetched_at", "REAL"),
    ("sso_connections", "metadata_due_at", "REAL"),
    ("sso_connections", "needs_review", "TEXT"),
    ("sso_connections", "metadata_error", "TEXT"),
    ("workspaces", "require_sso", "INTEGER NOT NULL DEFAULT 0"),
    ("sso_events", "ref", "TEXT"),
    ("workspaces", "role_source", "TEXT"),
    ("workspaces", "group_admin_days", "INTEGER NOT NULL DEFAULT 30"),
    ("sessions", "username", "TEXT"),
    ("sessions", "workspace", "TEXT"),
)
_EVENT_OUTCOMES = ('signed_in', 'refused', 'failed', 'test', 'warning', 'updated', 'attention')


def adopt(conn):
    """If `conn` (sqlite3, inside the migration's transaction) is a database from
    before Alembic, bring it to the baseline schema and return True. A new or
    empty database, or one Alembic already keeps, is left alone (False)."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "alembic_version" in tables or not tables & APP_TABLES:
        return False
    log.warning("Bringing a database from before migrations to the baseline (tables: %s)",
                ", ".join(sorted(tables & APP_TABLES)))
    for sql in _TABLES:
        conn.execute(sql)
    for table, column, definition in _NEW_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    _roles_as_overrides(conn)
    _role_source(conn)
    _allow_new_event_outcomes(conn)
    _sessions_say_whose(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (username)")
    return True


def _done(conn, marker):
    return conn.execute("SELECT 1 FROM imports WHERE source = ?", (marker,)).fetchone()


def _mark(conn, marker):
    conn.execute("INSERT INTO imports (source, imported_at) VALUES (?, ?)", (marker, time.time()))


def _roles_as_overrides(conn):
    """Before group roles, every role was set by hand: keep each as an override."""
    if not _done(conn, "roles-as-overrides"):
        conn.execute("UPDATE members SET override_role = role "
                     "WHERE role != 'public' AND override_role IS NULL AND group_role IS NULL")
        _mark(conn, "roles-as-overrides")


def _role_source(conn):
    """Before role sources, any provider with rules set roles. Make the one with
    rules that signed someone in last each workspace's role source, and count
    the groups already known as sent at their last sign-in, so expiry starts
    from there. Group roles already given are left as they are."""
    if _done(conn, "role-source"):
        return
    for (slug,) in conn.execute("SELECT slug FROM workspaces WHERE role_source IS NULL").fetchall():
        pick = conn.execute("""SELECT id FROM sso_connections c WHERE workspace = ?
                               AND EXISTS (SELECT 1 FROM group_role_rules
                                           WHERE connection_id = c.id)
                               ORDER BY last_login_at IS NULL, last_login_at DESC,
                                        created_at LIMIT 1""", (slug,)).fetchone()
        if pick:
            conn.execute("UPDATE workspaces SET role_source = ? WHERE slug = ?", (pick[0], slug))
    conn.execute("UPDATE members SET groups_updated_at = COALESCE(last_login_at, ?) "
                 "WHERE groups IS NOT NULL AND groups_updated_at IS NULL", (time.time(),))
    _mark(conn, "role-source")


def _allow_new_event_outcomes(conn):
    """sso_events from before some outcome: SQLite can't change a CHECK in place,
    so copy the table into one that allows them all, keeping every row and column."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'sso_events'").fetchone()[0]
    check = re.search(r"CHECK \(outcome IN \(([^)]*)\)\)", sql)
    if not check:
        raise RuntimeError("sso_events isn't the table this code knows; not changing it")
    if all(f"'{o}'" in check.group(1) for o in _EVENT_OUTCOMES):
        return
    allowed = ", ".join(f"'{o}'" for o in _EVENT_OUTCOMES)
    new = sql.replace("CREATE TABLE sso_events", "CREATE TABLE sso_events_new", 1)
    new = new.replace(check.group(0), f"CHECK (outcome IN ({allowed}))", 1)
    columns = ", ".join(r[1] for r in conn.execute("PRAGMA table_info(sso_events)"))
    conn.execute(new)
    conn.execute(f"INSERT INTO sso_events_new ({columns}) SELECT {columns} FROM sso_events")
    conn.execute("DROP TABLE sso_events")
    conn.execute("ALTER TABLE sso_events_new RENAME TO sso_events")
    conn.execute("CREATE INDEX IF NOT EXISTS sso_events_by_workspace ON sso_events(workspace, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS sso_events_by_connection "
                 "ON sso_events(connection_id, id)")


def _sessions_say_whose(conn):
    """Sessions from before the username / workspace columns: fill them in from
    the stored user, so "sign out everywhere" finds them too."""
    rows = conn.execute("SELECT token_hash, user FROM sessions WHERE username IS NULL").fetchall()
    for token_hash, user in rows:
        try:
            user = json.loads(user)
        except ValueError:
            continue
        if isinstance(user, dict):
            username = (user.get("username") or "").strip().lower() or None
            conn.execute("UPDATE sessions SET username = ?, workspace = ? WHERE token_hash = ?",
                         (username, user.get("workspace"), token_hash))
