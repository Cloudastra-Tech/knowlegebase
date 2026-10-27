"""
members_store.py  —  WHO may sign in to which workspace, and with which role.

It lives in the same small SQLite file as the sessions and integrations
(config/app.db, opened by db.py; its tables are made and changed by the
migrations in migrations/), so it can be changed from the app instead of by
editing files:

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
                       and changes made by themselves to a SAML provider's metadata,
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
      someone in last became the role source (migrations/legacy.py).
    * SCIM can be the role source instead of a provider (role_source = SCIM_SOURCE,
      its rules in scim_group_rules): then the groups the identity provider pushes
      through /scim/v2 set group roles as they change (scim_store.py), sign-ins
      set none, and group-given admin doesn't expire, since SCIM keeps it current.

Until there's a screen for it, manage it with `python auth.py` (see its help).

It replaces config/users.json and config/sso.json. The first time the database
is opened, whatever is in those files is copied in, once; after that the files
are no longer read, and can be kept as a backup.
"""

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time

import db
import secret_box
import workspaces

USERS_FILE = os.getenv("USERS_FILE", os.path.join("config", "users.json"))
SSO_FILE = os.getenv("SSO_CONFIG", os.path.join("config", "sso.json"))

ROLES = ("public", "hr", "admin")
STAFF_ROLES = ("hr", "admin")      # see HR documents and the HR / Admin screens
# A workspace proves it owns a domain with this DNS TXT record:
#     _knowledge-assistant.<domain>   TXT   "kb-verify=<token>"
TXT_PREFIX = "_knowledge-assistant"
EVENTS_KEPT = 500
# 'warning': role-source admin kept; 'updated' / 'attention': a SAML provider's
# metadata changed by itself, or needs a look; 'scim': what the identity
# provider did through SCIM (scim_store.py). sso_events' CHECK allows exactly
# these: a new one needs a migration.
EVENT_OUTCOMES = ('signed_in', 'refused', 'failed', 'test', 'warning', 'updated', 'attention',
                  'scim')
SCIM_SOURCE = "scim"               # workspaces.role_source / members.group_source: SCIM's groups
GROUP_ADMIN_DAYS = 30              # group-given admin lasts this long without a sign-in
                                   # (the column's default in the schema too)

log = logging.getLogger(__name__)
_lock = threading.RLock()
_ready = False                     # the old files imported, this process
_warned = {}                       # username -> when they were last kept as the only admin


def __getattr__(name):
    if name == "DB_PATH":          # the database file, as api.py's readiness check reads it
        return db.DB_PATH
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


def _db():
    global _ready
    if not _ready:
        with _lock:
            if not _ready:
                conn = db.connect_platform()        # the files name any workspace
                try:
                    with conn:
                        _import_old_files(conn)
                finally:
                    conn.close()
                _ready = True
    return db.connect()


def _platform_db():
    """_db(), seeing every workspace's rows (db.platform): for a check that is
    across workspaces by nature, e.g. whether another one owns a domain."""
    with db.platform():
        return _db()


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
    with _platform_db() as conn:
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
    with _lock, _platform_db() as conn:
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


class WorkspaceTaken(ValueError):
    """Sign-up can't create this workspace. `reason`: "workspace_exists" (the
    domain is already verified by a workspace), "account_exists" (the founder
    is already a member of one) or "invite" (the invite code was used)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _code_hash(code):
    return hashlib.sha256(code.strip().encode()).hexdigest()


def signup_code_used(code):
    with _db() as conn:
        return bool(conn.execute("SELECT 1 FROM signup_codes_used WHERE code_hash = ?",
                                 (_code_hash(code),)).fetchone())


def create_workspace(name, domain, email, person_name, code=None):
    """Sign-up: a new workspace called `name`, with `domain` verified and `email`
    as its first admin, all or nothing. The caller has proved `email` controls
    `domain` (a Google Workspace account whose primary domain it is). An invite
    `code`, when given, is used up by it. Returns the new workspace's slug;
    raises WorkspaceTaken."""
    domain, email = domain.strip().lower(), email.strip().lower()
    with _lock, _platform_db() as conn:
        if conn.execute("""SELECT 1 FROM workspace_domains
                           WHERE domain = ? AND verified_at IS NOT NULL""", (domain,)).fetchone():
            raise WorkspaceTaken("workspace_exists")
        if conn.execute("SELECT 1 FROM members WHERE username = ?", (email,)).fetchone():
            raise WorkspaceTaken("account_exists")
        if code and conn.execute("SELECT 1 FROM signup_codes_used WHERE code_hash = ?",
                                 (_code_hash(code),)).fetchone():
            raise WorkspaceTaken("invite")
        slug, now = _free_slug(conn, name, domain), time.time()
        if code:
            conn.execute("INSERT INTO signup_codes_used (code_hash, workspace, used_at) "
                         "VALUES (?, ?, ?)", (_code_hash(code), slug, now))
        conn.execute("INSERT INTO workspaces (slug, name, created_at) VALUES (?, ?, ?)",
                     (slug, name.strip(), now))
        conn.execute("""INSERT INTO workspace_domains (workspace, domain, verify_token, verified_at)
                        VALUES (?, ?, NULL, ?)""", (slug, domain, now))
        conn.execute("""INSERT INTO members (username, workspace, name, override_role,
                                             created_at, last_login_at)
                        VALUES (?, ?, ?, 'admin', ?, ?)""",
                     (email, slug, person_name or email.split("@")[0], now, now))
        _settle_role(conn, email)
    return slug


def _free_slug(conn, name, domain):
    """A workspace slug from its name (or else its domain) that isn't taken."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-")
    if not workspaces.valid(base) or base == workspaces.DEFAULT:
        base = re.sub(r"[^a-z0-9]+", "-", domain.split(".")[0])[:32].strip("-") or "workspace"
    slug, n = base, 1
    while conn.execute("SELECT 1 FROM workspaces WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def workspace_name(workspace):
    with _db() as conn:
        row = conn.execute("SELECT name FROM workspaces WHERE slug = ?", (workspace,)).fetchone()
    return row["name"] if row else workspace


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


class WrongWorkspace(ValueError):
    """The account belongs to another workspace, so this one can't change it."""


def set_password(username, name, password_hash, workspace, role=None):
    """Add a password account to `workspace`, or reset its password. Keeps the
    role unless one is given. Raises WrongWorkspace if the username is taken in
    another workspace: an account is never moved, or changed, from here."""
    username = username.strip().lower()
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        n = conn.execute("""INSERT INTO members
                            (username, workspace, name, password_hash, override_role, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT (username) DO UPDATE SET
                                password_hash = excluded.password_hash,
                                override_role = COALESCE(excluded.override_role,
                                                         members.override_role)
                            WHERE members.workspace = excluded.workspace""",
                         (username, workspace, name, password_hash, role, time.time())).rowcount
        if not n:
            raise WrongWorkspace(f"{username} belongs to another workspace.")
        _settle_role(conn, username)


def set_role(username, role, workspace):
    """Give someone in `workspace` a role, overriding their groups; None goes back
    to their groups. Someone who hasn't signed in yet is added to `workspace`, so
    they have it from their first single sign-on. Raises WrongWorkspace if they
    belong to another workspace."""
    if role is not None and role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
    username = username.strip().lower()
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        n = conn.execute("""INSERT INTO members (username, workspace, name, override_role, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT (username) DO UPDATE SET override_role = excluded.override_role
                            WHERE members.workspace = excluded.workspace""",
                         (username, workspace, username.split("@")[0], role,
                          time.time())).rowcount
        if not n:
            raise WrongWorkspace(f"{username} belongs to another workspace.")
        _settle_role(conn, username)


def set_disabled(username, disabled, workspace):
    """Turn someone's access off (or back on). False if `workspace` has no such member."""
    with _lock, _db() as conn:
        n = conn.execute("UPDATE members SET disabled = ? WHERE username = ? AND workspace = ?",
                         (int(bool(disabled)), username.strip().lower(), workspace)).rowcount
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


def remove(username, workspace):
    """Returns False if `workspace` has no such member."""
    with _lock, _db() as conn:
        n = conn.execute("DELETE FROM members WHERE username = ? AND workspace = ?",
                         (username.strip().lower(), workspace)).rowcount
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
    sealed = secret_box.seal(keys, workspace)      # before the transaction (secret_box)
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        conn.execute("""INSERT INTO sso_connections
                        (id, workspace, protocol, label, issuer, client_id, secret, metadata,
                         created_by, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (cid, workspace, protocol, label, issuer, client_id,
                      sealed, json.dumps(metadata), created_by, now, now))
    return get_connection(cid)


def new_connection_id():
    return secrets.token_hex(6)


METADATA_STATUS = ("metadata_refreshed_at", "metadata_due_at", "needs_review", "metadata_error")


def update_connection(workspace, cid, **fields):
    """Change a connection's label, issuer, client_id, keys (merged into the
    stored ones), metadata, enabled or METADATA_STATUS. Returns the connection,
    or None if the workspace has no such one."""
    if "keys" in fields:
        fields["secret"] = secret_box.seal({**(connection_keys(cid) or {}),
                                            **fields.pop("keys")}, workspace)
    if "metadata" in fields:
        fields["metadata"] = json.dumps(fields["metadata"])
    for flag in ("enabled", "logout_at_idp"):
        if flag in fields:
            fields[flag] = int(bool(fields[flag]))
    allowed = {"label", "issuer", "client_id", "secret", "metadata", "enabled", "logout_at_idp",
               *METADATA_STATUS}
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
        row = conn.execute("SELECT secret, workspace FROM sso_connections WHERE id = ?",
                           (cid,)).fetchone()
    return secret_box.unseal(row["secret"], row["workspace"]) if row else None


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
            _regroup(conn, cid, [], workspace)
            _keep_an_admin(conn, workspace, before)
    return n > 0


# ---- SAML: metadata fetched again from its URL (api.py) ----------------------------
def saml_connections_from_urls():
    """Every SAML provider, in every workspace, whose metadata came from a URL."""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM sso_connections WHERE protocol = 'saml'").fetchall()
    return [c for c in map(_connection, rows) if c["metadata"].get("metadata_url")]


def claim_metadata_fetch(cid, gap, due_by=None):
    """True if this process may fetch the connection's metadata now: nobody has
    in the last `gap` seconds (and, given `due_by`, it's due by then). It's
    marked as fetched in the same statement, so another worker won't as well."""
    now = time.time()
    sql = ("UPDATE sso_connections SET metadata_fetched_at = ? "
           "WHERE id = ? AND COALESCE(metadata_fetched_at, 0) <= ?")
    args = [now, cid, now - gap]
    if due_by is not None:
        sql, args = sql + " AND COALESCE(metadata_due_at, 0) <= ?", args + [due_by]
    with _lock, _db() as conn:
        return conn.execute(sql, args).rowcount == 1


def _status_columns(fields):
    if set(fields) - set(METADATA_STATUS):
        raise ValueError(f"Can't change {', '.join(sorted(set(fields) - set(METADATA_STATUS)))}.")
    return "".join(f"{k} = ?, " for k in fields)


def set_metadata_status(cid, **fields):
    """Change only a connection's METADATA_STATUS. Not an admin's change, so
    updated_at stays as it was."""
    cols = _status_columns(fields).rstrip(", ")
    with _lock, _db() as conn:
        conn.execute(f"UPDATE sso_connections SET {cols} WHERE id = ?", (*fields.values(), cid))


def replace_metadata(cid, seen_updated_at, metadata, **status):
    """Store newly fetched metadata (and METADATA_STATUS), unless the connection
    has changed since it was read at `seen_updated_at`: an admin's edit wins.
    True if it was stored."""
    cols = _status_columns(status)
    with _lock, _db() as conn:
        return conn.execute(f"UPDATE sso_connections SET {cols}metadata = ?, updated_at = ? "
                            "WHERE id = ? AND updated_at = ?",
                            (*status.values(), json.dumps(metadata), time.time(), cid,
                             seen_updated_at)).rowcount == 1


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
    # SCIM's events are kept apart: a big first sync mustn't push out the sign-ins.
    scim = int(outcome == "scim")
    conn.execute("""DELETE FROM sso_events WHERE workspace = ? AND (outcome = 'scim') = (? = 1)
                    AND id NOT IN (SELECT id FROM sso_events
                                   WHERE workspace = ? AND (outcome = 'scim') = (? = 1)
                                   ORDER BY id DESC LIMIT ?)""",
                 (workspace, scim, workspace, scim, EVENTS_KEPT))


def recent_events(workspace, limit=50, scim=False):
    """The newest sign-in events, or (`scim`) the newest SCIM ones."""
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT connection_id, email, outcome, reason, ref, at FROM sso_events "
            "WHERE workspace = ? AND (outcome = 'scim') = (? = 1) ORDER BY id DESC LIMIT ?",
            (workspace, int(scim), limit))]


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
    kept = _kept_rules(rules)
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
            _drop_other_sources(conn, workspace, cid)
        elif source == cid and not kept:
            conn.execute("UPDATE workspaces SET role_source = NULL WHERE slug = ?",
                         (workspace,))
        _regroup(conn, cid, kept, workspace)
        _keep_an_admin(conn, workspace, before)
        _expire_group_admins(conn, workspace)
    return True


def _kept_rules(rules):
    """[(group, role)] checked, first of each group (without regard to case) kept."""
    for _, role in rules:
        if role not in ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
    kept, seen = [], set()
    for group, role in rules:
        if group.strip().lower() not in seen:
            seen.add(group.strip().lower())
            kept.append({"group_name": group.strip(), "role": role})
    return kept


# ---- Roles from SCIM's groups -------------------------------------------------
def scim_group_rules(workspace):
    with _db() as conn:
        return _scim_rules(conn, workspace)


def _scim_rules(conn, workspace):
    return [dict(r) for r in conn.execute(
        "SELECT group_name, role FROM scim_group_rules WHERE workspace = ? "
        "ORDER BY group_name COLLATE NOCASE", (workspace,))]


def set_scim_group_rules(workspace, rules, make_role_source=False):
    """Replace the rules for the groups SCIM pushes ([(group, role)]). Rules make
    SCIM the workspace's role source, the same way set_group_rules does for a
    provider (RoleSourceTaken, NoAdminLeft); its group roles apply at once, from
    the groups SCIM has pushed."""
    kept = _kept_rules(rules)
    with _lock, _db() as conn:
        _ensure_workspace(conn, workspace)
        source = conn.execute("SELECT role_source FROM workspaces WHERE slug = ?",
                              (workspace,)).fetchone()["role_source"]
        if kept and source not in (None, SCIM_SOURCE) and not make_role_source:
            raise RoleSourceTaken(source)
        before = _admins(conn, workspace)
        conn.execute("DELETE FROM scim_group_rules WHERE workspace = ?", (workspace,))
        for r in kept:
            conn.execute("INSERT INTO scim_group_rules (workspace, group_name, role) "
                         "VALUES (?, ?, ?)", (workspace, r["group_name"], r["role"]))
        if kept and source != SCIM_SOURCE:
            conn.execute("UPDATE workspaces SET role_source = ? WHERE slug = ?",
                         (SCIM_SOURCE, workspace))
            _drop_other_sources(conn, workspace, SCIM_SOURCE)
            apply_scim_groups(conn, workspace)
        elif source == SCIM_SOURCE and not kept:
            conn.execute("UPDATE workspaces SET role_source = NULL WHERE slug = ?",
                         (workspace,))
        _regroup(conn, SCIM_SOURCE, kept, workspace)
        _keep_an_admin(conn, workspace, before)


def apply_scim_groups(conn, workspace, usernames=None):
    """In a transaction that changed SCIM's groups (scim_store.py): when SCIM is
    the role source, set these members' groups (default: everyone SCIM has put
    in a group) from the groups it pushed, and their group role by its rules.
    The caller checks _keep_an_admin."""
    if conn.execute("SELECT 1 FROM workspaces WHERE slug = ? AND role_source = ?",
                    (workspace, SCIM_SOURCE)).fetchone() is None:
        return
    if usernames is None:
        usernames = [r["username"] for r in conn.execute(
            """SELECT m.username FROM scim_group_members m
               JOIN scim_groups g ON g.id = m.group_id WHERE g.workspace = ?
               UNION SELECT username FROM members WHERE workspace = ? AND group_source = ?""",
            (workspace, workspace, SCIM_SOURCE))]
    rules, now = _scim_rules(conn, workspace), time.time()
    for username in usernames:
        names = [r["display_name"] for r in conn.execute(
            """SELECT g.display_name FROM scim_groups g
               JOIN scim_group_members m ON m.group_id = g.id
               WHERE g.workspace = ? AND m.username = ?
               ORDER BY g.display_name COLLATE NOCASE""", (workspace, username))]
        conn.execute("UPDATE members SET groups = ?, group_role = ?, group_source = ?, "
                     "groups_updated_at = ? WHERE username = ? AND workspace = ?",
                     (json.dumps(names[:100]), role_for_groups(rules, names) if rules else None,
                      SCIM_SOURCE, now, username, workspace))
        _settle_role(conn, username)


def _drop_other_sources(conn, workspace, source):
    """`source` just became the role source: group roles any other source (a
    provider, or SCIM) gave in the workspace no longer count."""
    for row in conn.execute("SELECT DISTINCT group_source FROM members "
                            "WHERE workspace = ? AND group_source != ?",
                            (workspace, source)).fetchall():
        _regroup(conn, row["group_source"], [], workspace)


def _regroup(conn, cid, rules, workspace):
    """The rules of provider `cid` (or SCIM) in `workspace` changed: recompute the
    group role of everyone it gave one to, from the groups it sent last. (No
    rules, or a deleted provider: they lose it.)"""
    for row in conn.execute("SELECT username, groups FROM members WHERE group_source = ? "
                            "AND workspace = ?", (cid, workspace)).fetchall():
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
                and member.get("group_source") != SCIM_SOURCE
                and member["groups_updated_at"] < time.time() - days * 86400)


def check_group_admin(member):
    """The same for one member, on each request: cheap unless their admin comes
    from groups at least a day old. Returns their row as it is now."""
    if (not member or member["group_role"] != "admin"
            or member.get("group_source") == SCIM_SOURCE
            or (member["groups_updated_at"] or time.time()) > time.time() - 86400):
        return member
    with _lock, _db() as conn:
        if not _expire_group_admins(conn, member["workspace"], member["username"]):
            return member
        return _member(conn.execute("SELECT * FROM members WHERE username = ?",
                                    (member["username"],)).fetchone())


def _expire_group_admins(conn, workspace, username=None):
    """True if someone's admin expired. Someone who is the workspace's last admin
    keeps it, and its admins are warned (at most once a day per person). Admin
    from SCIM's groups never expires: SCIM sends every change as it happens."""
    days = conn.execute("SELECT group_admin_days FROM workspaces WHERE slug = ?",
                        (workspace,)).fetchone()
    days = days["group_admin_days"] if days else GROUP_ADMIN_DAYS
    if not days:
        return False
    sql = ("SELECT username, groups, group_source, override_role FROM members "
           "WHERE workspace = ? AND group_role = 'admin' AND groups_updated_at < ? "
           f"AND group_source IS NOT '{SCIM_SOURCE}'")
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
