"""
members_store.py  —  WHO may sign in to which workspace, and with which role.

It lives in the same small SQLite file as the sessions and integrations
(config/app.db), so it can be changed from the app instead of by editing files:

    workspaces         one row per company; require_sso turns passwords off for
                       everyone but its admins (their way in if the provider breaks)
    workspace_domains  email domains a workspace owns; only VERIFIED ones let
                       anyone in, and a domain is verified for one workspace at most
    members            everyone who can sign in: password accounts, and people
                       who signed in with single sign-on (or were given a role first)
    sso_connections    a workspace's own sign-in provider (OpenID Connect or SAML),
                       set up by its admin; its secrets are encrypted
    saml_requests      SAML sign-ins in progress: an IdP's answer must match one, once
    saml_assertions    SAML assertions already used, so none can be replayed
    sso_events         recent sign-ins and refusals through a workspace's providers,
                       for its admin (the newest EVENTS_KEPT per workspace)
    group_role_rules   per provider: people in this group get this role

ROLES, lowest to highest:
    public   chat over public documents
    hr       also HR documents, the Documents and Integrations screens
    admin    everything hr can, plus who may sign in (sign-in settings, members)

Where a member's role comes from (the `role` column holds the result):
    1. disabled          no access at all (a separate flag)
    2. override_role     set by an admin (or `python auth.py role`)
    3. group_role        from their groups at the provider, by the provider's
                         rules (group_role_rules)
    4. otherwise         public

Group roles come from ONE provider per workspace, its ROLE SOURCE
(workspaces.role_source), however many providers it has. Only a sign-in through
the role source sets someone's groups and group role (and groups_updated_at);
a sign-in through any other provider records the sign-in and leaves the role
alone, so it doesn't change with the button someone clicks.
    * Saving rules on a provider when there's no role source makes it the role
      source. Saving them on another one switches the source, but only when
      asked to (set_group_rules raises RoleSourceTaken otherwise).
    * When the source's rules change, everyone's group role is recomputed from
      the groups it last sent. Switching the source, clearing its rules or
      deleting it: the group roles it gave are gone, and the new source's apply
      at each person's next sign-in through it (or straight away, from the
      groups it last sent, for people it already sent groups for).
    * None of these may leave a workspace with no admin (NoAdminLeft).
    * Group-given admin expires: after workspaces.group_admin_days (default 30;
      0 = never) without a sign-in through the role source, it's dropped to what
      their groups give otherwise, unless they're the workspace's last admin
      (they keep it, and the admin is warned in the sign-in events). Checked on
      each of their requests, and for everyone when the members are listed.
    * Databases from before role sources: the provider with rules that signed
      someone in last became the role source (see _add_columns).

Until there's a screen for it, manage it with `python auth.py` (see its help).

It replaces config/users.json and config/sso.json. The first time the database
is opened, whatever is in those files is copied in, once; after that the files
are no longer read, and can be kept as a backup.
"""

import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time

import secret_box
import workspaces

DB_PATH = os.getenv("APP_DB", os.path.join("config", "app.db"))
USERS_FILE = os.getenv("USERS_FILE", os.path.join("config", "users.json"))
SSO_FILE = os.getenv("SSO_CONFIG", os.path.join("config", "sso.json"))

ROLES = ("public", "hr", "admin")
STAFF_ROLES = ("hr", "admin")      # see HR documents and the HR / Admin screens
# A workspace proves it owns a domain with this DNS TXT record:
#     _knowledge-assistant.<domain>   TXT   "kb-verify=<token>"
TXT_PREFIX = "_knowledge-assistant"
EVENTS_KEPT = 500
EVENT_OUTCOMES = ('signed_in', 'refused', 'failed', 'test', 'warning')
GROUP_ADMIN_DAYS = 30              # group-given admin lasts this long without a sign-in

log = logging.getLogger(__name__)
_lock = threading.RLock()
_ready = False                     # tables made and the old files imported, this process
_warned = {}                       # username -> when they were last kept as the only admin


class RoleSourceTaken(Exception):
    """Another provider already sets the workspace's roles (`source`: its id)."""

    def __init__(self, source):
        super().__init__(source)
        self.source = source


class NoAdminLeft(Exception):
    """The change would leave the workspace without an admin. `admins`: who'd lose it."""

    def __init__(self, admins):
        super().__init__(", ".join(admins))
        self.admins = admins


def _connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _db():
    global _ready
    conn = _connect()
    if not _ready:
        with _lock:
            if not _ready:
                with conn:
                    _create_tables(conn)
                    _add_columns(conn)
                    _import_old_files(conn)
                _ready = True
    return conn


# Columns added after the first release of a table: (table, column, definition).
# SQLite can add a column in place; existing rows get the default.
_NEW_COLUMNS = (
    ("members", "override_role", "TEXT"),
    ("members", "group_role", "TEXT"),
    ("members", "groups", "TEXT"),                  # JSON list, as last sent
    ("members", "disabled", "INTEGER NOT NULL DEFAULT 0"),
    ("members", "group_source", "TEXT"),            # the provider that set group_role
    ("members", "groups_updated_at", "REAL"),       # when the role source last sent them
    ("sso_connections", "group_claim", "TEXT"),
    ("sso_connections", "logout_at_idp", "INTEGER NOT NULL DEFAULT 0"),
    ("workspaces", "require_sso", "INTEGER NOT NULL DEFAULT 0"),
    ("sso_events", "ref", "TEXT"),                  # shown to the person it failed for
    ("workspaces", "role_source", "TEXT"),          # the provider whose groups set roles
    ("workspaces", "group_admin_days", f"INTEGER NOT NULL DEFAULT {GROUP_ADMIN_DAYS}"),
)


def _add_columns(conn):
    for table, column, definition in _NEW_COLUMNS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    # Before group roles, every role was set by hand: keep each as an override.
    # Done once, and marked in the same transaction as the copy (an ALTER TABLE
    # commits on its own, so "the column is new" can't be the marker).
    if not conn.execute("SELECT 1 FROM imports WHERE source = 'roles-as-overrides'").fetchone():
        conn.execute("UPDATE members SET override_role = role "
                     "WHERE role != 'public' AND override_role IS NULL AND group_role IS NULL")
        conn.execute("INSERT INTO imports (source, imported_at) VALUES ('roles-as-overrides', ?)",
                     (time.time(),))
    # Before role sources, any provider with rules set roles. Make the one with
    # rules that signed someone in last each workspace's role source, and count
    # the groups already known as sent at their last sign-in, so expiry starts
    # from there. Group roles already given are left as they are.
    if not conn.execute("SELECT 1 FROM imports WHERE source = 'role-source'").fetchone():
        for row in conn.execute("""SELECT slug FROM workspaces
                                   WHERE role_source IS NULL""").fetchall():
            pick = conn.execute("""SELECT id FROM sso_connections c WHERE workspace = ?
                                   AND EXISTS (SELECT 1 FROM group_role_rules
                                               WHERE connection_id = c.id)
                                   ORDER BY last_login_at IS NULL, last_login_at DESC,
                                            created_at LIMIT 1""", (row["slug"],)).fetchone()
            if pick:
                conn.execute("UPDATE workspaces SET role_source = ? WHERE slug = ?",
                             (pick["id"], row["slug"]))
        conn.execute("UPDATE members SET groups_updated_at = COALESCE(last_login_at, ?) "
                     "WHERE groups IS NOT NULL AND groups_updated_at IS NULL", (time.time(),))
        conn.execute("INSERT INTO imports (source, imported_at) VALUES ('role-source', ?)",
                     (time.time(),))
    _allow_new_event_outcomes(conn)


def _allow_new_event_outcomes(conn):
    """sso_events from before some outcome in EVENT_OUTCOMES: SQLite can't change
    a CHECK in place, so copy the table into one that allows them all, keeping
    every row and column (all or nothing)."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'sso_events'").fetchone()["sql"]
    check = re.search(r"CHECK \(outcome IN \(([^)]*)\)\)", sql)
    if not check:
        raise RuntimeError("sso_events isn't the table this code knows; not changing it")
    if all(f"'{o}'" in check.group(1) for o in EVENT_OUTCOMES):
        return
    allowed = ", ".join(f"'{o}'" for o in EVENT_OUTCOMES)
    new = sql.replace("CREATE TABLE sso_events", "CREATE TABLE sso_events_new", 1)
    new = new.replace(check.group(0), f"CHECK (outcome IN ({allowed}))", 1)
    columns = ", ".join(r["name"] for r in conn.execute("PRAGMA table_info(sso_events)"))
    conn.execute("SAVEPOINT event_outcomes")
    conn.execute(new)
    conn.execute(f"INSERT INTO sso_events_new ({columns}) SELECT {columns} FROM sso_events")
    conn.execute("DROP TABLE sso_events")
    conn.execute("ALTER TABLE sso_events_new RENAME TO sso_events")
    conn.execute("CREATE INDEX IF NOT EXISTS sso_events_by_workspace ON sso_events(workspace, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS sso_events_by_connection "
                 "ON sso_events(connection_id, id)")
    conn.execute("RELEASE event_outcomes")


def _create_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS workspaces (
        slug        TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS workspace_domains (
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        domain       TEXT NOT NULL,
        verify_token TEXT,                 -- for the DNS check (sign-in settings)
        verified_at  REAL,                 -- NULL until the workspace proves it owns it
        PRIMARY KEY (workspace, domain));
    -- A domain can be claimed by several workspaces, but VERIFIED by one at most.
    CREATE UNIQUE INDEX IF NOT EXISTS one_verified_owner
        ON workspace_domains(domain) WHERE verified_at IS NOT NULL;
    CREATE TABLE IF NOT EXISTS members (
        username      TEXT PRIMARY KEY,    -- the email for single sign-on
        workspace     TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'public'
                      CHECK (role IN ('public', 'hr', 'admin')),
        password_hash TEXT,                -- NULL: single sign-on only
        created_at    REAL NOT NULL,
        last_login_at REAL);
    CREATE TABLE IF NOT EXISTS sso_connections (
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
        last_login_at REAL);
    CREATE TABLE IF NOT EXISTS saml_requests (
        request_id    TEXT PRIMARY KEY,
        connection_id TEXT NOT NULL REFERENCES sso_connections(id) ON DELETE CASCADE,
        expires       REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS saml_assertions (
        assertion_id  TEXT PRIMARY KEY,
        expires       REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS sso_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace     TEXT NOT NULL,
        connection_id TEXT,
        email         TEXT,
        outcome       TEXT NOT NULL
                      CHECK (outcome IN ('signed_in', 'refused', 'failed', 'test', 'warning')),
        reason        TEXT,                -- why it was refused or failed (a code
                                           -- from sso_errors.py, never provider
                                           -- text), or the warning
        at            REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS sso_events_by_workspace ON sso_events(workspace, id);
    CREATE INDEX IF NOT EXISTS sso_events_by_connection ON sso_events(connection_id, id);
    CREATE TABLE IF NOT EXISTS group_role_rules (
        connection_id TEXT NOT NULL REFERENCES sso_connections(id) ON DELETE CASCADE,
        group_name    TEXT NOT NULL,       -- compared without regard to case
        role          TEXT NOT NULL CHECK (role IN ('public', 'hr', 'admin')),
        PRIMARY KEY (connection_id, group_name));
    CREATE TABLE IF NOT EXISTS imports (
        source      TEXT PRIMARY KEY,      -- an old config file, copied in once
        imported_at REAL NOT NULL);
    """)


# ---- The old config files, copied in once -------------------------------------
def _import_old_files(conn):
    for source, path, copy in (("users.json", USERS_FILE, _copy_users),
                               ("sso.json", SSO_FILE, _copy_sso)):
        if not os.path.exists(path):
            continue
        if conn.execute("SELECT 1 FROM imports WHERE source = ?", (source,)).fetchone():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            # Leave it unmarked: once the file is fixed, the next start copies it.
            log.exception("Couldn't read %s, so it wasn't imported", path)
            continue
        copy(conn, data)
        conn.execute("INSERT INTO imports (source, imported_at) VALUES (?, ?)", (source, time.time()))
        log.warning("Imported %s into the database; edits to it are no longer read.", path)


def _copy_users(conn, users):
    if not isinstance(users, dict):
        log.warning("users.json isn't a list of accounts by name; nothing imported from it")
        return
    for username, user in users.items():
        if not isinstance(user, dict):
            continue
        ws = user.get("workspace") or workspaces.DEFAULT
        if not workspaces.valid(ws):
            continue
        _ensure_workspace(conn, ws)
        role = "hr" if user.get("role") == "hr" else None
        conn.execute("""INSERT OR IGNORE INTO members
                        (username, workspace, name, role, override_role, password_hash,
                         created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (username.strip().lower(), ws, user.get("name") or username,
                      role or "public", role, user.get("password"), time.time()))


def _copy_sso(conn, config):
    found = config.get("workspaces") if isinstance(config, dict) else None
    for slug, ws in (found if isinstance(found, dict) else {}).items():
        if not workspaces.valid(slug) or not isinstance(ws, dict):
            continue
        _ensure_workspace(conn, slug)
        for domain in ws.get("domains", []):
            domain = domain.strip().lower()
            # These were set by whoever runs the server, so they count as verified,
            # unless another workspace in the file listed the domain first.
            taken = conn.execute("""SELECT 1 FROM workspace_domains
                                    WHERE domain = ? AND verified_at IS NOT NULL""",
                                 (domain,)).fetchone()
            conn.execute("INSERT OR IGNORE INTO workspace_domains (workspace, domain, verify_token, verified_at) "
                         "VALUES (?, ?, NULL, ?)",
                         (slug, domain, None if taken else time.time()))
        for email in ws.get("hr", []):
            email = email.strip().lower()
            conn.execute("""INSERT OR IGNORE INTO members
                            (username, workspace, name, role, override_role, created_at)
                            VALUES (?, ?, ?, 'hr', 'hr', ?)""",
                         (email, slug, email.split("@")[0], time.time()))
            conn.execute("UPDATE members SET role = 'hr', override_role = 'hr' "
                         "WHERE username = ? AND workspace = ? AND role = 'public'",
                         (email, slug))


def _ensure_workspace(conn, slug):
    conn.execute("INSERT OR IGNORE INTO workspaces (slug, name, created_at) VALUES (?, ?, ?)",
                 (slug, slug, time.time()))


# ---- Workspaces ----------------------------------------------------------------
def requires_sso(workspace):
    """Does this workspace allow passwords only for its admins?"""
    with _db() as conn:
        row = conn.execute("SELECT require_sso FROM workspaces WHERE slug = ?",
                           (workspace,)).fetchone()
    return bool(row and row["require_sso"])


def set_requires_sso(workspace, on):
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("UPDATE workspaces SET require_sso = ? WHERE slug = ?",
                     (int(bool(on)), workspace))


def role_source(workspace):
    """The provider whose groups set the workspace's roles, or None."""
    with _db() as conn:
        row = conn.execute("SELECT role_source FROM workspaces WHERE slug = ?",
                           (workspace,)).fetchone()
    return row["role_source"] if row else None


def group_admin_days(workspace):
    """Days group-given admin lasts without a sign-in through the role source (0: forever)."""
    with _db() as conn:
        row = conn.execute("SELECT group_admin_days FROM workspaces WHERE slug = ?",
                           (workspace,)).fetchone()
    return row["group_admin_days"] if row else GROUP_ADMIN_DAYS


def set_group_admin_days(workspace, days):
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("UPDATE workspaces SET group_admin_days = ? WHERE slug = ?",
                     (max(0, int(days)), workspace))
        _expire_group_admins(conn, workspace)


# ---- Domains -------------------------------------------------------------------
def workspace_for_domain(domain):
    """The workspace that has verified this email domain, or None."""
    with _db() as conn:
        row = conn.execute("""SELECT workspace FROM workspace_domains
                              WHERE domain = ? AND verified_at IS NOT NULL""",
                           (domain.strip().lower(),)).fetchone()
    return row["workspace"] if row else None


def verified_domains(workspace):
    with _db() as conn:
        rows = conn.execute("""SELECT domain FROM workspace_domains
                               WHERE workspace = ? AND verified_at IS NOT NULL""",
                            (workspace,)).fetchall()
    return {r["domain"] for r in rows}


def add_verified_domain(workspace, domain):
    """For whoever runs the server: let `domain` sign in to `workspace`.
    Raises ValueError if another workspace has already verified it."""
    domain = domain.strip().lower()
    with _lock, _db() as conn:
        owner = conn.execute("""SELECT workspace FROM workspace_domains
                                WHERE domain = ? AND verified_at IS NOT NULL""",
                             (domain,)).fetchone()
        if owner and owner["workspace"] != workspace:
            raise ValueError(f"{domain} already belongs to the workspace {owner['workspace']}.")
        _ensure_workspace(conn, workspace)
        conn.execute("""INSERT INTO workspace_domains (workspace, domain, verify_token, verified_at)
                        VALUES (?, ?, NULL, ?)
                        ON CONFLICT (workspace, domain)
                        DO UPDATE SET verified_at = excluded.verified_at""",
                     (workspace, domain, time.time()))


def remove_domain(domain, workspace=None):
    """Stop a domain signing in (to `workspace`, or anywhere). False if it wasn't there."""
    sql, args = "DELETE FROM workspace_domains WHERE domain = ?", [domain.strip().lower()]
    if workspace:
        sql, args = sql + " AND workspace = ?", args + [workspace]
    with _lock, _db() as conn:
        n = conn.execute(sql, args).rowcount
    return n > 0


def list_domains(workspace=None):
    sql, args = "SELECT * FROM workspace_domains", []
    if workspace:
        sql, args = sql + " WHERE workspace = ?", [workspace]
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY workspace, domain", args)]


def claim_domain(workspace, domain):
    """A workspace admin adds a domain: it signs nobody in until verified.
    Returns the row, with the token for the DNS record."""
    domain = domain.strip().lower()
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("""INSERT OR IGNORE INTO workspace_domains (workspace, domain, verify_token)
                        VALUES (?, ?, ?)""", (workspace, domain, secrets.token_urlsafe(18)))
        conn.execute("""UPDATE workspace_domains SET verify_token = ?
                        WHERE workspace = ? AND domain = ? AND verify_token IS NULL""",
                     (secrets.token_urlsafe(18), workspace, domain))
        return dict(conn.execute("SELECT * FROM workspace_domains WHERE workspace = ? "
                                 "AND domain = ?", (workspace, domain)).fetchone())


def get_domain(workspace, domain):
    with _db() as conn:
        row = conn.execute("SELECT * FROM workspace_domains WHERE workspace = ? AND domain = ?",
                           (workspace, domain.strip().lower())).fetchone()
    return dict(row) if row else None


def mark_verified(workspace, domain):
    """The DNS record checked out. Raises ValueError if another workspace got there first."""
    try:
        with _lock, _db() as conn:
            conn.execute("""UPDATE workspace_domains SET verified_at = ?
                            WHERE workspace = ? AND domain = ? AND verified_at IS NULL""",
                         (time.time(), workspace, domain.strip().lower()))
    except sqlite3.IntegrityError:
        raise ValueError("Another workspace has already verified this domain.") from None


def txt_record(row):
    """(name, value) of the DNS TXT record that proves a workspace owns a domain."""
    return f"{TXT_PREFIX}.{row['domain']}", f"kb-verify={row['verify_token']}"


# ---- Members -------------------------------------------------------------------
def _member(row):
    if not row:
        return None
    m = dict(row)
    m["groups"] = json.loads(m.get("groups") or "[]")
    m["disabled"] = bool(m.get("disabled"))
    return m


def _settle_role(conn, username):
    """Recompute the effective role (see the top of this file)."""
    conn.execute("UPDATE members SET role = COALESCE(override_role, group_role, 'public') "
                 "WHERE username = ?", (username,))


def get(username):
    """The member's row as a dict, or None."""
    with _db() as conn:
        return _member(conn.execute("SELECT * FROM members WHERE username = ?",
                                    ((username or "").strip().lower(),)).fetchone())


def list_members(workspace=None):
    sql, args = "SELECT * FROM members", []
    if workspace:
        sql, args = sql + " WHERE workspace = ?", [workspace]
    with _db() as conn:
        rows = conn.execute(sql + " ORDER BY workspace, username", args).fetchall()
    out = []
    for r in rows:
        m = _member(r)
        m["has_password"] = m.pop("password_hash") is not None
        out.append(m)
    return out


def has_password_accounts():
    with _db() as conn:
        return conn.execute("SELECT 1 FROM members WHERE password_hash IS NOT NULL "
                            "LIMIT 1").fetchone() is not None


def set_password(username, name, password_hash, workspace, role=None):
    """Add a password account, or reset its password. Keeps the role unless one is given."""
    username = username.strip().lower()
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("""INSERT INTO members
                        (username, workspace, name, password_hash, override_role, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (username) DO UPDATE SET
                            workspace = excluded.workspace,
                            password_hash = excluded.password_hash,
                            override_role = COALESCE(excluded.override_role,
                                                     members.override_role)""",
                     (username, workspace, name, password_hash, role, time.time()))
        _settle_role(conn, username)


def set_role(username, role, workspace=workspaces.DEFAULT):
    """Give someone a role, overriding their groups; None goes back to their groups.
    Someone who hasn't signed in yet is added to `workspace`, so they have it from
    their first single sign-on."""
    if role is not None and role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
    username = username.strip().lower()
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("""INSERT INTO members (username, workspace, name, override_role, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (username) DO UPDATE SET override_role = excluded.override_role""",
                     (username, workspace, username.split("@")[0], role, time.time()))
        _settle_role(conn, username)


def set_disabled(username, disabled):
    """Turn someone's access off (or back on). False if there's no such member."""
    with _lock, _db() as conn:
        n = conn.execute("UPDATE members SET disabled = ? WHERE username = ?",
                         (int(bool(disabled)), username.strip().lower())).rowcount
    return n > 0


def enabled_admins(workspace):
    with _db() as conn:
        return _admins(conn, workspace)


def _admins(conn, workspace):
    return [r["username"] for r in conn.execute(
        "SELECT username FROM members WHERE workspace = ? AND role = 'admin' "
        "AND disabled = 0", (workspace,))]


def _keep_an_admin(conn, workspace, before):
    """After a change to group roles, in its transaction: raise NoAdminLeft (which
    undoes it) if the workspace had admins (`before`) and now has none."""
    if before and not _admins(conn, workspace):
        raise NoAdminLeft(before)


def remove(username):
    """Returns False if there was no such member."""
    with _lock, _db() as conn:
        n = conn.execute("DELETE FROM members WHERE username = ?",
                         (username.strip().lower(),)).rowcount
    return n > 0


def record_sso_login(username, name, workspace, groups=None, source=None):
    """Someone just signed in with single sign-on: add them if they're new.
    `groups` = (their groups, the role those give) from workspace provider
    `source`. If that's the workspace's role source, it replaces what their
    groups gave them last time; through any other provider their role stays as
    it is. Returns their member row, or None if they already belong to another
    workspace."""
    username = username.strip().lower()
    now = time.time()
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("""INSERT OR IGNORE INTO members (username, workspace, name, created_at)
                        VALUES (?, ?, ?, ?)""", (username, workspace, name, now))
        # The provider's name for them is the current one.
        conn.execute("UPDATE members SET last_login_at = ?, name = COALESCE(?, name) "
                     "WHERE username = ? AND workspace = ?",
                     (now, name or None, username, workspace))
        sets_roles = conn.execute("SELECT 1 FROM workspaces WHERE slug = ? AND role_source = ?",
                                  (workspace, source)).fetchone()
        if groups is not None and sets_roles:
            names, role = groups
            conn.execute("UPDATE members SET groups = ?, group_role = ?, group_source = ?, "
                         "groups_updated_at = ? WHERE username = ? AND workspace = ?",
                         (json.dumps(names[:100]), role, source, now, username, workspace))
        _settle_role(conn, username)
        row = conn.execute("SELECT * FROM members WHERE username = ?", (username,)).fetchone()
    return _member(row) if row and row["workspace"] == workspace else None


def role_for_groups(rules, groups):
    """The highest role the rules give these groups, or None if none matches."""
    have = {g.strip().lower() for g in groups}
    matched = [r["role"] for r in rules if r["group_name"].lower() in have]
    return max(matched, key=ROLES.index) if matched else None


# ---- A workspace's own sign-in providers ---------------------------------------
def _connection(row):
    if not row:
        return None
    c = dict(row)
    c.pop("secret", None)                 # only connection_keys() hands that out
    c["metadata"] = json.loads(c["metadata"] or "{}")
    c["enabled"] = bool(c["enabled"])
    c["logout_at_idp"] = bool(c.get("logout_at_idp"))
    return c


def create_connection(workspace, protocol, label, issuer, client_id, keys, metadata,
                      created_by, cid=None):
    """Add a sign-in provider to a workspace. `keys` (a dict) is stored encrypted:
    {"client_secret"} for OpenID Connect, {"sp_key", "sp_cert"} for SAML.
    Returns the connection."""
    cid, now = cid or new_connection_id(), time.time()
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("""INSERT INTO sso_connections
                        (id, workspace, protocol, label, issuer, client_id, secret, metadata,
                         created_by, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (cid, workspace, protocol, label, issuer, client_id,
                      secret_box.seal(keys), json.dumps(metadata), created_by, now, now))
    return get_connection(cid)


def new_connection_id():
    return secrets.token_hex(6)


def update_connection(workspace, cid, **fields):
    """Change a connection's label, issuer, client_id, keys (merged into the
    stored ones), metadata or enabled. Returns the connection, or None if the
    workspace has no such one."""
    if "keys" in fields:
        fields["secret"] = secret_box.seal({**(connection_keys(cid) or {}),
                                            **fields.pop("keys")})
    if "metadata" in fields:
        fields["metadata"] = json.dumps(fields["metadata"])
    for flag in ("enabled", "logout_at_idp"):
        if flag in fields:
            fields[flag] = int(bool(fields[flag]))
    allowed = {"label", "issuer", "client_id", "secret", "metadata", "enabled", "logout_at_idp"}
    if set(fields) - allowed:
        raise ValueError(f"Can't change {', '.join(sorted(set(fields) - allowed))}.")
    cols = "".join(f"{k} = ?, " for k in fields)
    with _lock, _db() as conn:
        n = conn.execute(f"UPDATE sso_connections SET {cols}updated_at = ? "
                         "WHERE id = ? AND workspace = ?",
                         (*fields.values(), time.time(), cid, workspace)).rowcount
    return get_connection(cid) if n else None


def get_connection(cid):
    with _db() as conn:
        return _connection(conn.execute("SELECT * FROM sso_connections WHERE id = ?",
                                        (cid or "",)).fetchone())


def connection_keys(cid):
    """The connection's decrypted keys (see create_connection), or None."""
    with _db() as conn:
        row = conn.execute("SELECT secret FROM sso_connections WHERE id = ?", (cid,)).fetchone()
    return secret_box.unseal(row["secret"]) if row else None


def list_connections(workspace, enabled_only=False):
    sql = "SELECT * FROM sso_connections WHERE workspace = ?"
    if enabled_only:
        sql += " AND enabled = 1"
    with _db() as conn:
        return [_connection(r) for r in conn.execute(sql + " ORDER BY created_at", (workspace,))]


def any_connections():
    """Does any workspace have an enabled provider of its own? (The login page then
    asks for an email; it says nothing about which workspaces.)"""
    with _db() as conn:
        return conn.execute("SELECT 1 FROM sso_connections WHERE enabled = 1 "
                            "LIMIT 1").fetchone() is not None


def delete_connection(workspace, cid):
    """False if the workspace has no such provider. Raises NoAdminLeft (deleting
    nothing) if the roles its groups gave were the workspace's only admins."""
    with _lock, _db() as conn:
        before = _admins(conn, workspace)
        n = conn.execute("DELETE FROM sso_connections WHERE id = ? AND workspace = ?",
                         (cid, workspace)).rowcount
        if n:                              # roles its groups gave go with it
            conn.execute("UPDATE workspaces SET role_source = NULL "
                         "WHERE slug = ? AND role_source = ?", (workspace, cid))
            _regroup(conn, cid, [])
            _keep_an_admin(conn, workspace, before)
    return n > 0


def record_connection_login(cid):
    with _lock, _db() as conn:
        conn.execute("UPDATE sso_connections SET last_login_at = ? WHERE id = ?",
                     (time.time(), cid))


# ---- SAML: each answer used once ------------------------------------------------
def remember_saml_request(cid, request_id, seconds):
    now = time.time()
    with _lock, _db() as conn:
        conn.execute("DELETE FROM saml_requests WHERE expires < ?", (now,))
        conn.execute("INSERT INTO saml_requests (request_id, connection_id, expires) "
                     "VALUES (?, ?, ?)", (request_id, cid, now + seconds))


def open_saml_requests(cid):
    """IDs of this connection's sign-ins still waiting for the IdP."""
    with _db() as conn:
        return [r["request_id"] for r in conn.execute(
            "SELECT request_id FROM saml_requests WHERE connection_id = ? AND expires >= ?",
            (cid, time.time()))]


def close_saml_request(request_id):
    """True the first time only: a request can be answered once."""
    with _lock, _db() as conn:
        return conn.execute("DELETE FROM saml_requests WHERE request_id = ?",
                            (request_id,)).rowcount == 1


def first_use_of_assertion(assertion_id, expires):
    """True the first time an assertion is seen. It's remembered until it expires."""
    now = time.time()
    with _lock, _db() as conn:
        conn.execute("DELETE FROM saml_assertions WHERE expires < ?", (now,))
        return conn.execute("INSERT OR IGNORE INTO saml_assertions (assertion_id, expires) "
                            "VALUES (?, ?)",
                            (assertion_id, max(expires, now + 60))).rowcount == 1


# ---- What happened at sign-in, for the workspace's admin ---------------------------
def log_event(workspace, cid, email, outcome, reason=None, ref=None):
    """Note a sign-in for the workspace's admin. `reason` is a code from
    sso_errors.py; `ref` the reference the person was shown, if any."""
    with _lock, _db() as conn:
        _log_event(conn, workspace, cid, email, outcome, reason, ref)


def _log_event(conn, workspace, cid, email, outcome, reason=None, ref=None):
    conn.execute("""INSERT INTO sso_events
                    (workspace, connection_id, email, outcome, reason, ref, at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                 (workspace, cid, email, outcome, (reason or "")[:300] or None, ref,
                  time.time()))
    conn.execute("""DELETE FROM sso_events WHERE workspace = ? AND id NOT IN
                    (SELECT id FROM sso_events WHERE workspace = ?
                     ORDER BY id DESC LIMIT ?)""", (workspace, workspace, EVENTS_KEPT))


def recent_events(workspace, limit=50):
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT connection_id, email, outcome, reason, ref, at FROM sso_events "
            "WHERE workspace = ? ORDER BY id DESC LIMIT ?", (workspace, limit))]


def connection_events(cid, limit=100):
    """A provider's latest events, newest first (for sso_errors.health)."""
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT outcome, reason, at FROM sso_events WHERE connection_id = ? "
            "ORDER BY id DESC LIMIT ?", (cid, limit))]


# ---- Roles from a provider's groups ---------------------------------------------
def group_rules(cid):
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT group_name, role FROM group_role_rules WHERE connection_id = ? "
            "ORDER BY group_name COLLATE NOCASE", (cid,))]


def set_group_rules(workspace, cid, group_claim, rules, make_role_source=False):
    """Replace a provider's group claim and rules ([(group, role)]). False if the
    workspace has no such provider. Rules make it the workspace's role source
    (see the top of this file); if another provider is, that raises
    RoleSourceTaken unless `make_role_source`. No rules on the role source: the
    workspace has none. Raises NoAdminLeft (changing nothing) if the group roles
    that go were its only admins."""
    for _, role in rules:
        if role not in ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
    kept, seen = [], set()
    for group, role in rules:
        if group.strip().lower() not in seen:
            seen.add(group.strip().lower())
            kept.append({"group_name": group.strip(), "role": role})
    with _lock, _db() as conn:
        n = conn.execute("UPDATE sso_connections SET group_claim = ?, updated_at = ? "
                         "WHERE id = ? AND workspace = ?",
                         (group_claim, time.time(), cid, workspace)).rowcount
        if not n:
            return False
        source = conn.execute("SELECT role_source FROM workspaces WHERE slug = ?",
                              (workspace,)).fetchone()["role_source"]
        if kept and source not in (None, cid) and not make_role_source:
            raise RoleSourceTaken(source)
        before = _admins(conn, workspace)
        conn.execute("DELETE FROM group_role_rules WHERE connection_id = ?", (cid,))
        for r in kept:
            conn.execute("INSERT INTO group_role_rules (connection_id, group_name, role) "
                         "VALUES (?, ?, ?)", (cid, r["group_name"], r["role"]))
        if kept and source != cid:
            # A new role source: group roles other providers gave no longer count.
            # Its own come from the groups it sent last (if it was the source
            # before), else at each person's next sign-in through it.
            conn.execute("UPDATE workspaces SET role_source = ? WHERE slug = ?",
                         (cid, workspace))
            for row in conn.execute("SELECT DISTINCT group_source FROM members "
                                    "WHERE workspace = ? AND group_source != ?",
                                    (workspace, cid)).fetchall():
                _regroup(conn, row["group_source"], [])
        elif source == cid and not kept:
            conn.execute("UPDATE workspaces SET role_source = NULL WHERE slug = ?",
                         (workspace,))
        _regroup(conn, cid, kept)
        _keep_an_admin(conn, workspace, before)
        _expire_group_admins(conn, workspace)
    return True


def _regroup(conn, cid, rules):
    """The rules of provider `cid` changed: recompute the group role of everyone it
    gave one to, from the groups it sent at their last sign-in. (No rules, or a
    deleted provider: they lose it.)"""
    for row in conn.execute("SELECT username, groups FROM members WHERE group_source = ?",
                            (cid,)).fetchall():
        role = role_for_groups(rules, json.loads(row["groups"] or "[]")) if rules else None
        conn.execute("UPDATE members SET group_role = ? WHERE username = ?",
                     (role, row["username"]))
        _settle_role(conn, row["username"])


# ---- Group-given admin expires --------------------------------------------------
def expire_group_admins(workspace):
    """Drop group-given admin the role source hasn't confirmed for a while (see
    the top of this file), across the workspace."""
    with _lock, _db() as conn:
        _expire_group_admins(conn, workspace)


def group_admin_stale(member, days):
    """Is their admin from groups the role source last sent over `days` ago?"""
    return bool(days and member["role"] == "admin" and not member["override_role"]
                and member["group_role"] == "admin" and member["groups_updated_at"]
                and member["groups_updated_at"] < time.time() - days * 86400)


def check_group_admin(member):
    """The same for one member, on each request: cheap unless their admin comes
    from groups at least a day old. Returns their row as it is now."""
    if (not member or member["group_role"] != "admin"
            or (member["groups_updated_at"] or time.time()) > time.time() - 86400):
        return member
    with _lock, _db() as conn:
        if not _expire_group_admins(conn, member["workspace"], member["username"]):
            return member
        return _member(conn.execute("SELECT * FROM members WHERE username = ?",
                                    (member["username"],)).fetchone())


def _expire_group_admins(conn, workspace, username=None):
    """True if someone's admin expired. Someone who is the workspace's last admin
    keeps it, and its admins are warned (at most once a day per person)."""
    days = conn.execute("SELECT group_admin_days FROM workspaces WHERE slug = ?",
                        (workspace,)).fetchone()
    days = days["group_admin_days"] if days else GROUP_ADMIN_DAYS
    if not days:
        return False
    sql = ("SELECT username, groups, group_source, override_role FROM members "
           "WHERE workspace = ? AND group_role = 'admin' AND groups_updated_at < ?")
    args = [workspace, time.time() - days * 86400]
    if username:
        sql, args = sql + " AND username = ?", args + [username]
    expired = False
    for row in conn.execute(sql, args).fetchall():
        if row["override_role"] is None and _admins(conn, workspace) == [row["username"]]:
            if _warned.get(row["username"], 0) < time.time() - 86400:
                _warned[row["username"]] = time.time()
                log.warning("%s is the only admin of %s, so keeps admin from groups not "
                            "updated in %s days", row["username"], workspace, days)
                _log_event(conn, workspace, row["group_source"], row["username"], "warning",
                           "last_admin_kept")
            continue
        # What their groups give without the rules that make them admin.
        rules = conn.execute("SELECT group_name, role FROM group_role_rules "
                             "WHERE connection_id = ? AND role != 'admin'",
                             (row["group_source"],)).fetchall()
        conn.execute("UPDATE members SET group_role = ? WHERE username = ?",
                     (role_for_groups(rules, json.loads(row["groups"] or "[]")),
                      row["username"]))
        _settle_role(conn, row["username"])
        expired = True
    return expired
