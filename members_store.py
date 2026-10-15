"""
members_store.py  —  WHO may sign in to which workspace, and with which role.

It lives in the same small SQLite file as the sessions and integrations
(config/app.db), so it can be changed from the app instead of by editing files:

    workspaces         one row per company
    workspace_domains  email domains a workspace owns; only VERIFIED ones let
                       anyone in, and a domain is verified for one workspace at most
    members            everyone who can sign in: password accounts, and people
                       who signed in with single sign-on (or were given a role first)

ROLES, lowest to highest:
    public   chat over public documents
    hr       also HR documents, the Documents and Integrations screens
    admin    everything hr can, plus who may sign in (sign-in settings, members)

Until there's a screen for it, manage it with `python auth.py` (see its help).

It replaces config/users.json and config/sso.json. The first time the database
is opened, whatever is in those files is copied in, once; after that the files
are no longer read, and can be kept as a backup.
"""

import json
import logging
import os
import sqlite3
import threading
import time

import workspaces

DB_PATH = os.getenv("APP_DB", os.path.join("config", "app.db"))
USERS_FILE = os.getenv("USERS_FILE", os.path.join("config", "users.json"))
SSO_FILE = os.getenv("SSO_CONFIG", os.path.join("config", "sso.json"))

ROLES = ("public", "hr", "admin")
STAFF_ROLES = ("hr", "admin")      # see HR documents and the HR / Admin screens

log = logging.getLogger(__name__)
_lock = threading.RLock()
_ready = False                     # tables made and the old files imported, this process


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
                    _import_old_files(conn)
                _ready = True
    return conn


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
        conn.execute("INSERT INTO imports VALUES (?, ?)", (source, time.time()))
        log.warning("Imported %s into the database; edits to it are no longer read.", path)


def _copy_users(conn, users):
    for username, user in (users or {}).items():
        ws = user.get("workspace") or workspaces.DEFAULT
        if not workspaces.valid(ws):
            continue
        _ensure_workspace(conn, ws)
        conn.execute("""INSERT OR IGNORE INTO members
                        (username, workspace, name, role, password_hash, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (username.strip().lower(), ws, user.get("name") or username,
                      "hr" if user.get("role") == "hr" else "public",
                      user.get("password"), time.time()))


def _copy_sso(conn, config):
    for slug, ws in ((config or {}).get("workspaces") or {}).items():
        if not workspaces.valid(slug):
            continue
        _ensure_workspace(conn, slug)
        for domain in ws.get("domains", []):
            domain = domain.strip().lower()
            # These were set by whoever runs the server, so they count as verified,
            # unless another workspace in the file listed the domain first.
            taken = conn.execute("""SELECT 1 FROM workspace_domains
                                    WHERE domain = ? AND verified_at IS NOT NULL""",
                                 (domain,)).fetchone()
            conn.execute("INSERT OR IGNORE INTO workspace_domains VALUES (?, ?, NULL, ?)",
                         (slug, domain, None if taken else time.time()))
        for email in ws.get("hr", []):
            email = email.strip().lower()
            conn.execute("""INSERT OR IGNORE INTO members
                            (username, workspace, name, role, created_at)
                            VALUES (?, ?, ?, 'hr', ?)""",
                         (email, slug, email.split("@")[0], time.time()))
            conn.execute("UPDATE members SET role = 'hr' WHERE username = ? AND workspace = ? "
                         "AND role = 'public'", (email, slug))


def _ensure_workspace(conn, slug):
    conn.execute("INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?)", (slug, slug, time.time()))


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
        conn.execute("""INSERT INTO workspace_domains VALUES (?, ?, NULL, ?)
                        ON CONFLICT (workspace, domain)
                        DO UPDATE SET verified_at = excluded.verified_at""",
                     (workspace, domain, time.time()))


def remove_domain(domain):
    """Stop a domain signing in anywhere. Returns False if no workspace had it."""
    with _lock, _db() as conn:
        n = conn.execute("DELETE FROM workspace_domains WHERE domain = ?",
                         (domain.strip().lower(),)).rowcount
    return n > 0


def list_domains():
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT workspace, domain, verified_at FROM workspace_domains "
            "ORDER BY workspace, domain")]


# ---- Members -------------------------------------------------------------------
def get(username):
    """The member's row as a dict, or None."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM members WHERE username = ?",
                           ((username or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def list_members():
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT username, workspace, name, role, password_hash IS NOT NULL AS has_password, "
            "last_login_at FROM members ORDER BY workspace, username")]


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
                        (username, workspace, name, role, password_hash, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (username) DO UPDATE SET
                            workspace = excluded.workspace,
                            password_hash = excluded.password_hash,
                            role = COALESCE(?, members.role)""",
                     (username, workspace, name, role or "public", password_hash, time.time(),
                      role))


def set_role(username, role, workspace=workspaces.DEFAULT):
    """Give someone a role. Someone who hasn't signed in yet is added to `workspace`,
    so they have it from their first single sign-on."""
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
    username = username.strip().lower()
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("""INSERT INTO members (username, workspace, name, role, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (username) DO UPDATE SET role = excluded.role""",
                     (username, workspace, username.split("@")[0], role, time.time()))


def remove(username):
    """Returns False if there was no such member."""
    with _lock, _db() as conn:
        n = conn.execute("DELETE FROM members WHERE username = ?",
                         (username.strip().lower(),)).rowcount
    return n > 0


def record_sso_login(username, name, workspace):
    """Someone just signed in with single sign-on: add them if they're new.
    Returns their member row, or None if they already belong to another workspace."""
    username = username.strip().lower()
    with _lock, _db() as conn:
        conn.execute("""INSERT OR IGNORE INTO members (username, workspace, name, created_at)
                        VALUES (?, ?, ?, ?)""", (username, workspace, name, time.time()))
        conn.execute("UPDATE members SET last_login_at = ? WHERE username = ? AND workspace = ?",
                     (time.time(), username, workspace))
        row = conn.execute("SELECT * FROM members WHERE username = ?", (username,)).fetchone()
    return dict(row) if row and row["workspace"] == workspace else None
