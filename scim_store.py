"""
scim_store.py  —  what a workspace's identity provider (Okta, Entra ID, ...)
provisions through SCIM 2.0 (kb/routers/scim.py speaks the protocol; this keeps
the data and its rules).

    scim_tokens         a workspace's bearer tokens: the provider sends one with
                        every request, and it decides the workspace. Only a
                        SHA-256 hash is kept; the token is shown once.
    members             SCIM's users ARE the workspace's members. SCIM adds its
                        own id for each (scim_id) and the provider's name and id
                        for them; a member who signed in before SCIM existed is
                        a SCIM user too, linked by their email.
    scim_groups         the provider's groups and who is in each. When SCIM is
    scim_group_members  the workspace's role source (members_store.py), they set
                        members.groups and group roles by scim_group_rules.

THE RULES
  * A member's username is their sign-in email: the provider's primary (or work)
    email, else its userName if that's an email. Only an email on one of the
    workspace's verified domains, or one with an open invite to it, can be
    provisioned (Refused otherwise). An invite used this way is accepted, with
    its role.
  * active=false disables them, and the caller ends all their sessions at once.
  * DELETE disables them and hides them from SCIM (it may provision them again,
    as a new user); their row stays for the audit log until the retention
    window (SCIM_RETENTION_DAYS, default 30) has passed, then anonymise_due()
    (a job: kb/services/scim.py) replaces their email and name. Never a hard
    delete.
  * No change may leave the workspace without an admin: members_store.NoAdminLeft,
    and nothing is changed.
  * Every query names the workspace: a token for one workspace can't see or
    change another's members or groups. On Postgres a SCIM request also runs as
    its workspace's tenant (row-level security, kb/routers/scim.py), and only
    two lookups see every workspace (db.platform): the token itself, and
    whether an email is already a member somewhere else (_owner), since a
    username is unique across workspaces.
"""

import hashlib
import json
import os
import secrets
import time
import uuid

import members_store
from members_store import SCIM_SOURCE, NoAdminLeft  # noqa: F401  (re-exported for callers)

MAX_TOKENS = 3                     # active tokens per workspace (two while rotating)
TOKEN_PREFIX = "kbscim_"
USED_EVERY = 60                    # last_used_at is written at most this often
RETENTION_DAYS = 30


class Refused(Exception):
    """The provider asked for something the rules don't allow. `status` and
    `scim_type` for the SCIM error; `code` for the workspace's SCIM events."""

    def __init__(self, status, detail, code, scim_type=None):
        super().__init__(detail)
        self.status, self.detail, self.code, self.scim_type = status, detail, code, scim_type


def retention_days():
    """Days a deleted SCIM user is kept, disabled, before being anonymised."""
    try:
        return max(0, int(os.getenv("SCIM_RETENTION_DAYS", str(RETENTION_DAYS))))
    except ValueError:
        return RETENTION_DAYS


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _db():
    return members_store._db()


def _owner(email):
    """The workspace `email` is a member of, whichever it is, or None. Across
    workspaces (db.platform): a username is unique in all of them, and under
    row-level security another workspace's member is invisible otherwise."""
    with members_store._platform_db() as everyone:
        row = everyone.execute("SELECT workspace FROM members WHERE username = ?",
                               (email,)).fetchone()
    return row["workspace"] if row else None


# ---- Tokens --------------------------------------------------------------------
def _token(row):
    return {k: row[k] for k in ("id", "hint", "created_by", "created_at", "last_used_at",
                                "revoked_at")}


def create_token(workspace, created_by):
    """A new token for the workspace. Returns (token row, the token itself): the
    token is never stored, so this is the only time it can be shown. Raises
    ValueError when the workspace already has MAX_TOKENS active ones."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with members_store._lock, _db() as conn:
        members_store._ensure_workspace(conn, workspace)
        active = conn.execute("SELECT COUNT(*) FROM scim_tokens WHERE workspace = ? "
                              "AND revoked_at IS NULL", (workspace,)).fetchone()[0]
        if active >= MAX_TOKENS:
            raise ValueError(f"A workspace can have {MAX_TOKENS} active tokens. "
                             "Revoke one you no longer use first.")
        cur = conn.execute("INSERT INTO scim_tokens (workspace, hash, hint, created_by, "
                           "created_at) VALUES (?, ?, ?, ?, ?)",
                           (workspace, _hash(token), token[-4:], created_by, time.time()))
        row = conn.execute("SELECT * FROM scim_tokens WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _token(row), token


def list_tokens(workspace):
    with _db() as conn:
        return [_token(r) for r in conn.execute(
            "SELECT * FROM scim_tokens WHERE workspace = ? ORDER BY id DESC", (workspace,))]


def revoke_token(workspace, token_id):
    """The token stops working now. Returns it, or None if the workspace has no
    such active token."""
    with members_store._lock, _db() as conn:
        n = conn.execute("UPDATE scim_tokens SET revoked_at = ? WHERE id = ? AND workspace = ? "
                         "AND revoked_at IS NULL", (time.time(), token_id, workspace)).rowcount
        row = conn.execute("SELECT * FROM scim_tokens WHERE id = ?", (token_id,)).fetchone()
    return _token(row) if n else None


def check_token(token):
    """(workspace, token id) for an active token, or None. Notes when it was
    used (the admin screen's "last sync")."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    now = time.time()
    with _db() as conn:
        row = conn.execute("SELECT id, workspace, last_used_at FROM scim_tokens "
                           "WHERE hash = ? AND revoked_at IS NULL", (_hash(token),)).fetchone()
    if not row:
        return None
    if (row["last_used_at"] or 0) < now - USED_EVERY:
        with members_store._lock, _db() as conn:
            conn.execute("UPDATE scim_tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    return row["workspace"], row["id"]


def last_sync(workspace):
    """When the provider last used any of the workspace's tokens, or None."""
    with _db() as conn:
        return conn.execute("SELECT MAX(last_used_at) FROM scim_tokens WHERE workspace = ?",
                            (workspace,)).fetchone()[0]


# ---- Users ---------------------------------------------------------------------
# A user, as the protocol layer hands it here (and gets it back from user()):
#   user_name, email, display_name, given_name, family_name, middle_name,
#   formatted, external_id, active
VISIBLE = "workspace = ? AND scim_deleted_at IS NULL AND anonymised_at IS NULL"
PROFILE_KEYS = ("given_name", "family_name", "middle_name", "formatted")


def _with_ids(conn, workspace):
    """Give every member SCIM can see an id (members from before SCIM, or who
    signed in on their own, get one the first time SCIM looks)."""
    for row in conn.execute(f"SELECT username FROM members WHERE {VISIBLE} AND scim_id IS NULL",
                            (workspace,)).fetchall():
        conn.execute("UPDATE members SET scim_id = ? WHERE username = ?",
                     (str(uuid.uuid4()), row["username"]))


def _user(conn, row):
    """A member row as a SCIM user (the dict described above), plus its id,
    groups and times."""
    profile = json.loads(row["scim_profile"] or "{}")
    groups = [dict(r) for r in conn.execute(
        """SELECT g.id, g.display_name FROM scim_groups g
           JOIN scim_group_members m ON m.group_id = g.id
           WHERE g.workspace = ? AND m.username = ? ORDER BY g.display_name COLLATE NOCASE""",
        (row["workspace"], row["username"]))]
    return {"id": row["scim_id"], "username": row["username"],
            "user_name": row["scim_username"] or row["username"], "email": row["username"],
            "display_name": row["name"], "external_id": row["scim_external_id"],
            "active": not row["disabled"], "role": row["role"], "groups": groups,
            "created_at": row["created_at"],
            "updated_at": row["scim_updated_at"] or row["created_at"],
            **{k: profile.get(k) for k in PROFILE_KEYS}}


USER_FILTERS = {
    "username": "(lower(scim_username) = lower(?) OR (scim_username IS NULL AND username = lower(?)))",
    "externalid": "scim_external_id = ?",
    "emails": "username = lower(?)",
    "emails.value": "username = lower(?)",
    "id": "scim_id = ?",
}


def list_users(workspace, attr=None, value=None, start=1, count=100):
    """(total, users) of the workspace's members SCIM can see, by username;
    `attr` (a USER_FILTERS key) eq `value` if given. start is 1-based."""
    where, args = VISIBLE, [workspace]
    if attr:
        clause = USER_FILTERS[attr]
        where, args = f"{where} AND {clause}", args + [value] * clause.count("?")
    with members_store._lock, _db() as conn:
        _with_ids(conn, workspace)
        total = conn.execute(f"SELECT COUNT(*) FROM members WHERE {where}", args).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM members WHERE {where} ORDER BY created_at, username "
                            "LIMIT ? OFFSET ?", args + [count, max(0, start - 1)]).fetchall()
        return total, [_user(conn, r) for r in rows]


def get_user(workspace, scim_id):
    with members_store._lock, _db() as conn:
        _with_ids(conn, workspace)
        row = conn.execute(f"SELECT * FROM members WHERE {VISIBLE} AND scim_id = ?",
                           (workspace, scim_id)).fetchone()
        return _user(conn, row) if row else None


def _check_email(conn, workspace, email):
    """Raise Refused unless `email` may be provisioned in `workspace`. Returns
    the open invite for it, if that's what allows it."""
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise Refused(400, "The user needs an email address (a primary or work email, or a "
                           "userName that is one).", "scim_no_email", "invalidValue")
    domain = email.rpartition("@")[2]
    verified = conn.execute("SELECT 1 FROM workspace_domains WHERE workspace = ? AND domain = ? "
                            "AND verified_at IS NOT NULL", (workspace, domain)).fetchone()
    invite = conn.execute("SELECT * FROM invites WHERE workspace = ? AND email = ? "
                          "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ?",
                          (workspace, email, time.time())).fetchone()
    if not verified and not invite:
        raise Refused(403, f"{domain} isn't a verified domain of this workspace, and "
                           f"{email} hasn't been invited.", "scim_domain")
    return invite


def _check_user_name(conn, workspace, user_name, username):
    """userName is unique in a workspace, without regard to case."""
    taken = conn.execute(f"SELECT 1 FROM members WHERE {VISIBLE} AND username != ? AND "
                         "(lower(scim_username) = lower(?) OR "
                         "(scim_username IS NULL AND username = lower(?)))",
                         (workspace, username, user_name, user_name)).fetchone()
    if taken:
        raise Refused(409, f"Another user already has the userName {user_name}.",
                      "scim_taken", "uniqueness")


def _profile(user):
    return json.dumps({k: user.get(k) for k in PROFILE_KEYS if user.get(k)})


def create_user(workspace, user):
    """Provision a user. Returns (their SCIM user, the member's username).

    Someone already in the workspace whom SCIM hasn't provisioned (they signed in
    before SCIM was set up), or deleted, is taken over instead of refused. Raises
    Refused: not allowed (domain), or the email or userName is someone else's."""
    email, now = user["email"], time.time()
    with members_store._lock, _db() as conn:
        invite = _check_email(conn, workspace, email)
        _check_user_name(conn, workspace, user["user_name"], email)
        owner = _owner(email)
        if owner and owner != workspace:
            raise Refused(409, f"{email} already belongs to another workspace.", "scim_taken",
                          "uniqueness")
        existing = conn.execute("SELECT * FROM members WHERE username = ? AND workspace = ?",
                                (email, workspace)).fetchone()
        if (existing and existing["scim_username"] is not None
                and existing["scim_deleted_at"] is None):
            raise Refused(409, f"{email} has already been provisioned.", "scim_exists",
                          "uniqueness")
        before = members_store._admins(conn, workspace)
        fields = {"name": user["display_name"], "scim_username": user["user_name"],
                  "scim_external_id": user.get("external_id"), "scim_profile": _profile(user),
                  "disabled": int(not user["active"]), "scim_updated_at": now,
                  "scim_deleted_at": None}
        if existing:
            # Deleted earlier and provisioned again: a new user, with a new id.
            fields["scim_id"] = (str(uuid.uuid4()) if existing["scim_deleted_at"]
                                 else existing["scim_id"] or str(uuid.uuid4()))
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE members SET {cols} WHERE username = ?",
                         (*fields.values(), email))
        else:
            fields.update(username=email, workspace=workspace, created_at=now,
                          scim_id=str(uuid.uuid4()))
            conn.execute(f"INSERT INTO members ({', '.join(fields)}) "
                         f"VALUES ({', '.join('?' * len(fields))})", tuple(fields.values()))
        if invite:
            conn.execute("UPDATE invites SET accepted_at = ? WHERE id = ?", (now, invite["id"]))
            if not existing:
                conn.execute("UPDATE members SET override_role = ? WHERE username = ?",
                             (invite["role"], email))
        members_store._settle_role(conn, email, workspace)
        members_store._keep_an_admin(conn, workspace, before)   # taken over, inactive
        row = conn.execute("SELECT * FROM members WHERE username = ?", (email,)).fetchone()
        return _user(conn, row), bool(existing)


def _rename(conn, workspace, old, new):
    """A member's username changes (a new email, or anonymised): everything of
    theirs keyed by it follows. Their sessions end (callers end them too)."""
    conn.execute("UPDATE members SET username = ? WHERE username = ? AND workspace = ?",
                 (new, old, workspace))
    conn.execute("UPDATE scim_group_members SET username = ? WHERE username = ? AND group_id IN "
                 "(SELECT id FROM scim_groups WHERE workspace = ?)", (new, old, workspace))
    conn.execute("UPDATE conversations SET owner = ? WHERE owner = ? AND workspace = ?",
                 (new, old, workspace))
    conn.execute("UPDATE message_feedback SET user = ? WHERE user = ? AND workspace = ?",
                 (new, old, workspace))
    conn.execute("DELETE FROM sessions WHERE username = ?", (old,))


def update_user(workspace, scim_id, user):
    """Replace a user's attributes (PUT, or PATCH once applied). Returns (their
    SCIM user, what changed: {"email": (old, new)}, {"active": bool} ...).
    Raises Refused, or NoAdminLeft (nothing changed), or LookupError (no such user)."""
    now = time.time()
    with members_store._lock, _db() as conn:
        row = conn.execute(f"SELECT * FROM members WHERE {VISIBLE} AND scim_id = ?",
                           (workspace, scim_id)).fetchone()
        if not row:
            raise LookupError(scim_id)
        old, new = row["username"], user["email"]
        changed = {}
        before = members_store._admins(conn, workspace)
        if new != old:
            _check_email(conn, workspace, new)
            if _owner(new):                       # in this workspace or another
                raise Refused(409, f"{new} already belongs to another member.", "scim_taken",
                              "uniqueness")
            _rename(conn, workspace, old, new)
            changed["email"] = [old, new]
        _check_user_name(conn, workspace, user["user_name"], new)
        if bool(row["disabled"]) == user["active"]:
            changed["active"] = user["active"]
        if row["name"] != user["display_name"]:
            changed["name"] = user["display_name"]
        conn.execute("UPDATE members SET name = ?, scim_username = ?, scim_external_id = ?, "
                     "scim_profile = ?, disabled = ?, scim_updated_at = ? WHERE username = ?",
                     (user["display_name"], user["user_name"], user.get("external_id"),
                      _profile(user), int(not user["active"]), now, new))
        members_store._keep_an_admin(conn, workspace, before)
        row = conn.execute("SELECT * FROM members WHERE username = ?", (new,)).fetchone()
        return _user(conn, row), changed


def delete_user(workspace, scim_id):
    """DELETE: disable them, take them out of SCIM's groups and out of SCIM's
    sight. Returns their username, or None if there's no such user. Raises
    NoAdminLeft (nothing changed)."""
    now = time.time()
    with members_store._lock, _db() as conn:
        row = conn.execute(f"SELECT * FROM members WHERE {VISIBLE} AND scim_id = ?",
                           (workspace, scim_id)).fetchone()
        if not row:
            return None
        before = members_store._admins(conn, workspace)
        conn.execute("UPDATE members SET disabled = 1, scim_deleted_at = ?, scim_updated_at = ? "
                     "WHERE username = ?", (now, now, row["username"]))
        conn.execute("DELETE FROM scim_group_members WHERE username = ? AND group_id IN "
                     "(SELECT id FROM scim_groups WHERE workspace = ?)",
                     (row["username"], workspace))
        members_store.apply_scim_groups(conn, workspace, [row["username"]])
        members_store._keep_an_admin(conn, workspace, before)
        return row["username"]


def anonymise_due(days=None, now=None):
    """Anonymise every member SCIM deleted over `days` (default retention_days())
    ago, in every workspace: a new username that is no email, no name, no
    groups, no role. Their rows stay (so does the audit log, which can't be
    changed). Their open invites end with them: one to their address (it would
    let them back in) and one they sent (nobody stands behind it now) is
    revoked, and every invite of theirs, open or not, names the new username
    instead of their email. Returns [(workspace, new username, scim id,
    invites revoked)]."""
    days = retention_days() if days is None else days
    now = now or time.time()
    done = []
    # Every workspace's (a job of no one workspace): as the platform.
    with members_store._lock, members_store._platform_db() as conn:
        rows = conn.execute("SELECT * FROM members WHERE scim_deleted_at IS NOT NULL AND "
                            "anonymised_at IS NULL AND scim_deleted_at <= ?",
                            (now - days * 86400,)).fetchall()
        for row in rows:
            new = f"deleted-{row['scim_id'] or uuid.uuid4()}"
            revoked = conn.execute(
                "UPDATE invites SET revoked_at = ? WHERE workspace = ? AND (email = ? OR "
                "invited_by = ?) AND accepted_at IS NULL AND revoked_at IS NULL",
                (now, row["workspace"], row["username"], row["username"])).rowcount
            conn.execute("UPDATE invites SET email = ? WHERE workspace = ? AND email = ?",
                         (new, row["workspace"], row["username"]))
            conn.execute("UPDATE invites SET invited_by = ? WHERE workspace = ? "
                         "AND invited_by = ?", (new, row["workspace"], row["username"]))
            _rename(conn, row["workspace"], row["username"], new)
            conn.execute("UPDATE members SET name = 'Deleted user', password_hash = NULL, "
                         "groups = '[]', group_role = NULL, override_role = NULL, "
                         "group_source = NULL, scim_username = NULL, scim_external_id = NULL, "
                         "scim_profile = NULL, disabled = 1, anonymised_at = ? "
                         "WHERE username = ?", (now, new))
            members_store._settle_role(conn, new, row["workspace"])
            done.append((row["workspace"], new, row["scim_id"], revoked))
    return done


# ---- Groups --------------------------------------------------------------------
GROUP_FILTERS = {
    "displayname": "display_name = ? COLLATE NOCASE",
    "externalid": "external_id = ?",
    "id": "id = ?",
}


def _group(conn, row, with_members=True):
    g = {"id": row["id"], "display_name": row["display_name"],
         "external_id": row["external_id"], "created_at": row["created_at"],
         "updated_at": row["updated_at"]}
    if with_members:
        g["members"] = [dict(r) for r in conn.execute(
            """SELECT m.scim_id AS id, m.name AS display, m.username FROM scim_group_members gm
               JOIN members m ON m.username = gm.username AND m.workspace = ?
               WHERE gm.group_id = ? ORDER BY m.username""", (row["workspace"], row["id"]))]
    return g


def list_groups(workspace, attr=None, value=None, start=1, count=100, with_members=True):
    where, args = "workspace = ?", [workspace]
    if attr:
        where, args = f"{where} AND {GROUP_FILTERS[attr]}", args + [value]
    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM scim_groups WHERE {where}", args).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM scim_groups WHERE {where} "
                            "ORDER BY created_at, id LIMIT ? OFFSET ?",
                            args + [count, max(0, start - 1)]).fetchall()
        return total, [_group(conn, r, with_members) for r in rows]


def get_group(workspace, group_id, with_members=True):
    with _db() as conn:
        row = conn.execute("SELECT * FROM scim_groups WHERE id = ? AND workspace = ?",
                           (group_id, workspace)).fetchone()
        return _group(conn, row, with_members) if row else None


def _usernames(conn, workspace, scim_ids):
    """The workspace's members with these SCIM ids, and the ids it has no one for."""
    found, unknown = {}, []
    for sid in dict.fromkeys(scim_ids):
        row = conn.execute(f"SELECT username FROM members WHERE {VISIBLE} AND scim_id = ?",
                           (workspace, sid)).fetchone()
        if row:
            found[sid] = row["username"]
        else:
            unknown.append(sid)
    return found, unknown


def _check_name(conn, workspace, name, group_id=None):
    if not (name or "").strip():
        raise Refused(400, "A group needs a displayName.", "scim_invalid", "invalidValue")
    if conn.execute("SELECT 1 FROM scim_groups WHERE workspace = ? AND display_name = ? "
                    "COLLATE NOCASE AND id != ?", (workspace, name, group_id or "")).fetchone():
        raise Refused(409, f"There's already a group called {name}.", "scim_taken", "uniqueness")


def _set_members(conn, workspace, group_id, usernames):
    """Make the group's members exactly `usernames`; returns everyone whose groups
    changed."""
    had = {r["username"] for r in conn.execute(
        "SELECT username FROM scim_group_members WHERE group_id = ?", (group_id,))}
    want = set(usernames)
    for u in had - want:
        conn.execute("DELETE FROM scim_group_members WHERE group_id = ? AND username = ?",
                     (group_id, u))
    for u in want - had:
        conn.execute("INSERT INTO scim_group_members (group_id, username) VALUES (?, ?)",
                     (group_id, u))
    return sorted(had ^ want)


def save_group(workspace, group_id, display_name, external_id, member_ids):
    """Create the group (group_id None) or replace it: its name, external id and
    members (SCIM ids; ones the workspace has no user for are left out and
    returned). Returns (group, created, unknown ids, usernames whose groups
    changed). Raises Refused, LookupError (no such group), NoAdminLeft."""
    now = time.time()
    with members_store._lock, _db() as conn:
        _check_name(conn, workspace, display_name, group_id)
        before = members_store._admins(conn, workspace)
        created = group_id is None
        if created:
            group_id = str(uuid.uuid4())
            conn.execute("INSERT INTO scim_groups (id, workspace, display_name, external_id, "
                         "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (group_id, workspace, display_name.strip(), external_id, now, now))
        else:
            row = conn.execute("SELECT * FROM scim_groups WHERE id = ? AND workspace = ?",
                               (group_id, workspace)).fetchone()
            if not row:
                raise LookupError(group_id)
            conn.execute("UPDATE scim_groups SET display_name = ?, external_id = ?, "
                         "updated_at = ? WHERE id = ?",
                         (display_name.strip(), external_id, now, group_id))
        found, unknown = _usernames(conn, workspace, member_ids)
        changed = _set_members(conn, workspace, group_id, found.values())
        renamed = not created and row["display_name"] != display_name.strip()
        # A new name can change what the rules give everyone in it.
        affected = changed if not renamed else sorted(set(changed) | {
            r["username"] for r in conn.execute(
                "SELECT username FROM scim_group_members WHERE group_id = ?", (group_id,))})
        members_store.apply_scim_groups(conn, workspace, affected)
        members_store._keep_an_admin(conn, workspace, before)
        row = conn.execute("SELECT * FROM scim_groups WHERE id = ?", (group_id,)).fetchone()
        return _group(conn, row), created, unknown, changed


def delete_group(workspace, group_id):
    """Returns the group's name, or None if there's no such group. Raises
    NoAdminLeft (nothing changed)."""
    with members_store._lock, _db() as conn:
        row = conn.execute("SELECT * FROM scim_groups WHERE id = ? AND workspace = ?",
                           (group_id, workspace)).fetchone()
        if not row:
            return None
        before = members_store._admins(conn, workspace)
        members = [r["username"] for r in conn.execute(
            "SELECT username FROM scim_group_members WHERE group_id = ?", (group_id,))]
        conn.execute("DELETE FROM scim_groups WHERE id = ?", (group_id,))
        conn.execute("DELETE FROM scim_group_members WHERE group_id = ?", (group_id,))
        members_store.apply_scim_groups(conn, workspace, members)
        members_store._keep_an_admin(conn, workspace, before)
        return row["display_name"]


def counts(workspace):
    """{"users": provisioned by SCIM, "groups"} for the admin screen."""
    with _db() as conn:
        users = conn.execute(f"SELECT COUNT(*) FROM members WHERE {VISIBLE} AND "
                             "scim_username IS NOT NULL", (workspace,)).fetchone()[0]
        groups = conn.execute("SELECT COUNT(*) FROM scim_groups WHERE workspace = ?",
                              (workspace,)).fetchone()[0]
    return {"users": users, "groups": groups}
