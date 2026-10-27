"""
join_store.py  —  who may JOIN a workspace by signing in, and claiming one nobody
can administer any more.

The tables are in config/app.db (migration 0021):

JOINING POLICY (workspaces.join_policy, optionally workspace_domains.join_policy
for one verified domain; NULL there means the workspace's). What happens the
first time someone signs in with single sign-on, on a verified domain, without
already being a member:

    auto          they're added as a member (role: public, or what their groups give)
    request       nobody is added: a JOIN REQUEST waits for an admin, who approves
                  it (picking their role) or denies it
    invite_only   they're turned away; only an invite or SCIM provisioning adds people

New workspaces start at 'request'. Workspaces that existed before migration 0021
were set to 'auto', which is how every workspace worked until then.

Members already in the workspace (signed in before, SCIM-provisioned, invited,
approved, or given a role by hand) never meet the policy; nor does someone with
an invite still pending, which their sign-in uses up. The one place that decides
is kb/services/joining.py, called by every sign-in that can add someone (sso.py).

JOIN REQUESTS: one pending request per person per workspace (signing in again
only updates it). A request expires after REQUEST_DAYS; a denial is remembered
for DENY_DAYS, during which signing in again is refused without a new request.

CLAIM CODES: see claimable() and claim(). A workspace with no verified domain and
no admin who can still sign in can be claimed by someone with a Google Workspace
account on a domain, with a one-time code an operator issued for that workspace
and domain. Claiming verifies the domain and makes them an admin.
"""

import hashlib
import os
import secrets
import sqlite3
import time

import lifecycle_store
import members_store

POLICIES = ("auto", "request", "invite_only")
DEFAULT_POLICY = "request"         # a new workspace's (the column's default)
CLAIM_CODE_HOURS = 72
CLAIM_CODE_PREFIX = "kbclaim-"


def _days(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def request_days():
    """A join request nobody answered expires after this many days."""
    return _days("JOIN_REQUEST_DAYS", 30)


def deny_days():
    """After a denial, the same person can't ask again for this many days."""
    return _days("JOIN_DENY_DAYS", 30)


def admin_idle_days():
    """An admin who signed in within this many days can still administer the
    workspace, so it can't be claimed (claimable())."""
    return _days("CLAIM_ADMIN_IDLE_DAYS", 90)


def _db():
    return members_store._db()


# ---- The policy --------------------------------------------------------------------
def workspace_policy(workspace):
    with _db() as conn:
        row = conn.execute("SELECT join_policy FROM workspaces WHERE slug = ?",
                           (workspace,)).fetchone()
    return row["join_policy"] if row else DEFAULT_POLICY


def set_workspace_policy(workspace, policy):
    if policy not in POLICIES:
        raise ValueError(f"Joining policy must be one of: {', '.join(POLICIES)}.")
    with members_store._lock, _db() as conn:
        members_store._ensure_workspace(conn, workspace)
        conn.execute("UPDATE workspaces SET join_policy = ? WHERE slug = ?", (policy, workspace))


def domain_policies(workspace):
    """{domain: its own policy} for the workspace's domains that have one."""
    with _db() as conn:
        rows = conn.execute("SELECT domain, join_policy FROM workspace_domains "
                            "WHERE workspace = ? AND join_policy IS NOT NULL",
                            (workspace,)).fetchall()
    return {r["domain"]: r["join_policy"] for r in rows}


def set_domain_policy(workspace, domain, policy):
    """A domain's own policy (None: the workspace's). False if the workspace has
    no such domain."""
    if policy is not None and policy not in POLICIES:
        raise ValueError(f"Joining policy must be one of: {', '.join(POLICIES)}.")
    with members_store._lock, _db() as conn:
        n = conn.execute("UPDATE workspace_domains SET join_policy = ? "
                         "WHERE workspace = ? AND domain = ?",
                         (policy, workspace, domain.strip().lower())).rowcount
    return n > 0


def policy_for(workspace, domain):
    """The policy for someone with an email on `domain` joining `workspace`."""
    with _db() as conn:
        row = conn.execute("""SELECT COALESCE(d.join_policy, w.join_policy) AS policy
                              FROM workspaces w LEFT JOIN workspace_domains d
                                ON d.workspace = w.slug AND d.domain = ?
                              WHERE w.slug = ?""",
                           (domain.strip().lower(), workspace)).fetchone()
    return row["policy"] if row else DEFAULT_POLICY


# ---- Invites a sign-in uses up -----------------------------------------------------
def take_invite(workspace, email, name):
    """If `email` has a pending invite to `workspace`, accept it now (they're added
    with the invited role, no password) and return it; else None."""
    import invites_store
    with _db() as conn:
        row = conn.execute("SELECT id FROM invites WHERE workspace = ? AND email = ? "
                           "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ? "
                           "ORDER BY id DESC LIMIT 1",
                           (workspace, email, time.time())).fetchone()
    if not row:
        return None
    try:
        return invites_store.accept_id(row["id"], name)
    except invites_store.InviteError:          # used or withdrawn meanwhile
        return None


# ---- Join requests -----------------------------------------------------------------
class Denied(Exception):
    """An admin denied this person's request recently: no new one until `until`."""

    def __init__(self, until):
        super().__init__(until)
        self.until = until


def _request(row):
    return dict(row) if row else None


def expire_due(workspace=None, now=None):
    """Mark pending requests past their expiry as expired. Returns those rows."""
    now = now or time.time()
    sql, args = "status = 'pending' AND expires_at <= ?", [now]
    if workspace:
        sql, args = sql + " AND workspace = ?", args + [workspace]
    with members_store._lock, _db() as conn:
        rows = conn.execute(f"SELECT * FROM join_requests WHERE {sql}", args).fetchall()
        conn.execute(f"UPDATE join_requests SET status = 'expired', decided_at = expires_at "
                     f"WHERE {sql}", args)
    return [dict(r) for r in rows]


def request(workspace, email, name, provider, now=None):
    """Record that `email` asked to join (by signing in). Returns (request, new):
    new is False if they already had one pending (it's only updated). Raises
    Denied within DENY_DAYS of a denial."""
    now, email = now or time.time(), email.strip().lower()
    with members_store._lock, _db() as conn:
        denied = conn.execute("SELECT MAX(decided_at) AS at FROM join_requests "
                              "WHERE workspace = ? AND email = ? AND status = 'denied'",
                              (workspace, email)).fetchone()["at"]
        if denied and denied > now - deny_days() * 86400:
            raise Denied(denied + deny_days() * 86400)
        conn.execute("UPDATE join_requests SET status = 'expired', decided_at = expires_at "
                     "WHERE workspace = ? AND email = ? AND status = 'pending' "
                     "AND expires_at <= ?", (workspace, email, now))
        n = conn.execute("UPDATE join_requests SET last_seen_at = ?, name = COALESCE(?, name), "
                         "provider = ? WHERE workspace = ? AND email = ? AND status = 'pending'",
                         (now, name or None, provider, workspace, email)).rowcount
        if not n:
            conn.execute("""INSERT INTO join_requests (workspace, email, name, provider,
                                                       created_at, last_seen_at, expires_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                         (workspace, email, name, provider, now, now,
                          now + request_days() * 86400))
        row = conn.execute("SELECT * FROM join_requests WHERE workspace = ? AND email = ? "
                           "AND status = 'pending'", (workspace, email)).fetchone()
    return _request(row), not n


def pending(workspace):
    """The workspace's pending requests, oldest first (expired ones are marked first)."""
    expire_due(workspace)
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM join_requests WHERE workspace = ? AND status = 'pending' "
            "ORDER BY created_at", (workspace,))]


def get(workspace, request_id):
    with _db() as conn:
        return _request(conn.execute("SELECT * FROM join_requests WHERE id = ? AND workspace = ?",
                                     (request_id, workspace)).fetchone())


class RequestClosed(Exception):
    """The request can't be decided: `reason` is "gone" (no such pending request:
    decided, expired or never there) or "taken" (the address joined another
    workspace meanwhile)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def approve(workspace, request_id, role, by, now=None):
    """Let them in: they're added as a member with `role` (public: no override,
    so their groups still apply), all or nothing. Returns the request."""
    if role not in members_store.ROLES:
        raise ValueError(f"Role must be one of: {', '.join(members_store.ROLES)}.")
    now = now or time.time()
    with members_store._lock, _db() as conn:
        row = conn.execute("SELECT * FROM join_requests WHERE id = ? AND workspace = ? "
                           "AND status = 'pending' AND expires_at > ?",
                           (request_id, workspace, now)).fetchone()
        if not row:
            raise RequestClosed("gone")
        # Every workspace's members (db.platform): an admin's connection sees only
        # their own, and the person may have joined another one meanwhile.
        with members_store._platform_db() as everyone:
            member = everyone.execute("SELECT workspace FROM members WHERE username = ?",
                                      (row["email"],)).fetchone()
        if member and member["workspace"] != workspace:
            raise RequestClosed("taken")
        members_store._ensure_workspace(conn, workspace)
        conn.execute("""INSERT INTO members (username, workspace, name, override_role, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (username) DO UPDATE SET
                            override_role = COALESCE(excluded.override_role,
                                                     members.override_role)
                        WHERE members.workspace = excluded.workspace""",
                     (row["email"], workspace, row["name"] or row["email"].split("@")[0],
                      None if role == "public" else role, now))
        members_store._settle_role(conn, row["email"], workspace)
        conn.execute("UPDATE join_requests SET status = 'approved', role = ?, decided_at = ?, "
                     "decided_by = ? WHERE id = ?", (role, now, by, request_id))
        out = conn.execute("SELECT * FROM join_requests WHERE id = ?", (request_id,)).fetchone()
    return _request(out)


def deny(workspace, request_id, by, now=None):
    """Turn it down; the same person can't ask again for DENY_DAYS. Returns it."""
    now = now or time.time()
    with members_store._lock, _db() as conn:
        n = conn.execute("UPDATE join_requests SET status = 'denied', decided_at = ?, "
                         "decided_by = ? WHERE id = ? AND workspace = ? AND status = 'pending' "
                         "AND expires_at > ?", (now, by, request_id, workspace, now)).rowcount
        if not n:
            raise RequestClosed("gone")
        out = conn.execute("SELECT * FROM join_requests WHERE id = ?", (request_id,)).fetchone()
    return _request(out)


def notices_since(workspace, since):
    """How many of the workspace's requests its admins were emailed about since then."""
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM join_requests WHERE workspace = ? "
                            "AND notified_at > ?", (workspace, since)).fetchone()[0]


def mark_notified(request_id):
    with members_store._lock, _db() as conn:
        conn.execute("UPDATE join_requests SET notified_at = ? WHERE id = ?",
                     (time.time(), request_id))


# ---- Claiming a workspace nobody can administer ------------------------------------
# A workspace made by hand (before sign-up existed) may have no verified domain and
# no admin who can still get in: nobody can verify a domain, change sign-in or let
# anyone in. Claiming fixes that, under strict rules:
#
#   * The workspace has NO verified domain (else its own sign-in decides who's in).
#   * It has NO ACTIVE ADMIN: no enabled member with the admin role who either has a
#     password (it works whenever passwords are on, so they can still get in) or
#     signed in within admin_idle_days(). An admin who has never signed in and has
#     no password doesn't count: they have no way in.
#   * It isn't suspended or being deleted.
#   * The claimant brings a ONE-TIME CODE an operator issued for that workspace and
#     one domain (CLAIM_CODE_HOURS, used once, only its hash kept), AND signs in with
#     a Google Workspace account whose primary domain (hd) is that domain, with an
#     email on it.
#
# Why an operator's code and not "the workspace's own pre-registered member email":
# a member row only says someone was let in once, often as a plain member; it says
# nothing about their right to run the workspace and everything in it. A code is
# an operator's decision, after checking who's asking out of band, and it names the
# domain, so it can't be used to attach some other domain.
class ClaimRefused(Exception):
    """`reason`: a code for the sign-in page (sso_errors.py)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _hash(code):
    return hashlib.sha256(code.strip().encode()).hexdigest()


def _refusal(conn, workspace, now):
    """Why `workspace` can't be claimed now (a ClaimRefused reason), or None."""
    if not conn.execute("SELECT 1 FROM workspaces WHERE slug = ?", (workspace,)).fetchone():
        return "claim_code"
    if conn.execute("SELECT 1 FROM workspace_domains WHERE workspace = ? "
                    "AND verified_at IS NOT NULL", (workspace,)).fetchone():
        return "claim_verified"
    if conn.execute("SELECT 1 FROM members WHERE workspace = ? AND role = 'admin' "
                    "AND disabled = 0 AND (password_hash IS NOT NULL OR last_login_at >= ?)",
                    (workspace, now - admin_idle_days() * 86400)).fetchone():
        return "claim_admin"
    return None


def claimable(workspace, now=None):
    """None if `workspace` could be claimed now, else why not (see above)."""
    if not lifecycle_store.signs_in(workspace, "public"):
        return "workspace_closed"
    with _db() as conn:
        return _refusal(conn, workspace, now or time.time())


def new_claim_code(workspace, domain, by, now=None):
    """An operator's one-time code to claim `workspace` with Google Workspace on
    `domain`. Returns (row, code): the code is shown once. Raises ClaimRefused if
    the workspace can't be claimed now. Any earlier unused code is revoked."""
    now, domain = now or time.time(), domain.strip().lower()
    refused = claimable(workspace, now)
    if refused:
        raise ClaimRefused(refused)
    with _db() as conn:
        owner = conn.execute("SELECT workspace FROM workspace_domains WHERE domain = ? "
                             "AND verified_at IS NOT NULL", (domain,)).fetchone()
    if owner:
        raise ClaimRefused("claim_taken")
    code = CLAIM_CODE_PREFIX + secrets.token_urlsafe(18)
    with members_store._lock, _db() as conn:
        conn.execute("UPDATE claim_codes SET revoked_at = ? WHERE workspace = ? "
                     "AND used_at IS NULL AND revoked_at IS NULL", (now, workspace))
        cur = conn.execute("""INSERT INTO claim_codes (code_hash, workspace, domain, created_by,
                                                       created_at, expires_at)
                              VALUES (?, ?, ?, ?, ?, ?)""",
                           (_hash(code), workspace, domain, by, now,
                            now + CLAIM_CODE_HOURS * 3600))
        row = conn.execute("SELECT * FROM claim_codes WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _code(row), code


def _code(row):
    if not row:
        return None
    c = dict(row)
    c.pop("code_hash", None)
    return c


def find_code(code, now=None):
    """The unused, unexpired, unrevoked claim code, or None."""
    if not code or not code.strip().startswith(CLAIM_CODE_PREFIX):
        return None
    with _db() as conn:
        return _code(conn.execute("SELECT * FROM claim_codes WHERE code_hash = ? "
                                  "AND used_at IS NULL AND revoked_at IS NULL "
                                  "AND expires_at > ?",
                                  (_hash(code), now or time.time())).fetchone())


def claim(code, email, name, org, now=None):
    """Claim a workspace: `email` signed in with Google Workspace `org` (the hd
    claim, already checked to be a Workspace account). All or nothing: the code is
    used up, the domain verified for the workspace, they're its admin, and its
    joining policy is set to 'request' so the new admin decides who else gets in.
    Returns the code's row (workspace, domain). Raises ClaimRefused."""
    now, email, org = now or time.time(), email.strip().lower(), (org or "").strip().lower()
    found = find_code(code, now)
    if not found:
        raise ClaimRefused("claim_code")
    workspace, domain = found["workspace"], found["domain"]
    if not org or org != domain or email.rpartition("@")[2] != domain:
        raise ClaimRefused("claim_domain")
    if not lifecycle_store.signs_in(workspace, "public"):
        raise ClaimRefused("workspace_closed")
    try:
        with members_store._lock, _db() as conn:
            refused = _refusal(conn, workspace, now)   # again, inside the transaction
            if refused:
                raise ClaimRefused(refused)
            member = conn.execute("SELECT workspace, disabled FROM members WHERE username = ?",
                                  (email,)).fetchone()
            if member and member["workspace"] != workspace:
                raise ClaimRefused("claim_taken")
            if member and member["disabled"]:
                raise ClaimRefused("disabled")
            n = conn.execute("UPDATE claim_codes SET used_at = ?, used_by = ? WHERE id = ? "
                             "AND used_at IS NULL AND revoked_at IS NULL",
                             (now, email, found["id"])).rowcount
            if not n:
                raise ClaimRefused("claim_code")
            # Raises IntegrityError if another workspace has verified it.
            conn.execute("""INSERT INTO workspace_domains (workspace, domain, verify_token,
                                                           verified_at)
                            VALUES (?, ?, NULL, ?)
                            ON CONFLICT (workspace, domain)
                            DO UPDATE SET verified_at = excluded.verified_at""",
                         (workspace, domain, now))
            conn.execute("""INSERT INTO members (username, workspace, name, override_role,
                                                 created_at, last_login_at)
                            VALUES (?, ?, ?, 'admin', ?, ?)
                            ON CONFLICT (username) DO UPDATE SET override_role = 'admin',
                                last_login_at = excluded.last_login_at
                            WHERE members.workspace = excluded.workspace""",
                         (email, workspace, name or email.split("@")[0], now, now))
            members_store._settle_role(conn, email, workspace)
            conn.execute("UPDATE workspaces SET join_policy = 'request' WHERE slug = ?",
                         (workspace,))
            conn.execute("UPDATE claim_codes SET revoked_at = ? WHERE workspace = ? "
                         "AND used_at IS NULL AND revoked_at IS NULL", (now, workspace))
    except sqlite3.IntegrityError:
        raise ClaimRefused("claim_taken") from None
    return found
