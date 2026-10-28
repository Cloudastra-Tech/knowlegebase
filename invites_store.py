"""
invites_store.py  —  an admin invites someone to their workspace by email.

The invite (the invites table, config/app.db) holds the email, the role they'll
have, who invited them and when it expires. The emailed link carries a random
token; only its SHA-256 hash is stored, so a copy of the database can't be used
to accept one. A link works once: accepting marks the invite in the same
transaction that adds the member, so two clicks can't both get in.

An invite is, in turn:
    pending     sent, not yet used, not expired
    expired     not used in time (INVITE_DAYS, default 7); "resend" makes a new link
    accepted    used: the person is a member
    revoked     withdrawn by an admin

Accepting adds them to the workspace with the invited role (set as if by an
admin, so it wins over their groups): with a password, or, when their email is
on a domain the workspace has verified and requires single sign-on for, with
no password; they then sign in through the workspace's provider.
"""

import hashlib
import os
import secrets
import time

import db
import members_store

STATUSES = ("pending", "expired", "accepted", "revoked")


class InviteError(Exception):
    """`reason`: why an invite can't be made or used. One of "member" (they're
    already in this workspace), "taken" (the address belongs to another
    workspace), "pending" (already invited), or a STATUSES value / "invalid"
    for an invite that can't be accepted."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def invite_days():
    try:
        return max(1, int(os.getenv("INVITE_DAYS", "7")))
    except ValueError:
        return 7


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def status(invite, now=None):
    if invite["accepted_at"]:
        return "accepted"
    if invite["revoked_at"]:
        return "revoked"
    if invite["expires_at"] <= (now or time.time()):
        return "expired"
    return "pending"


def _invite(row):
    if not row:
        return None
    i = dict(row)
    i.pop("token_hash", None)
    i["status"] = status(i)
    return i


def _check_address(conn, workspace, email):
    """Raise InviteError if `email` can't join `workspace`: a member already, or
    an account of (or on a domain verified by) another workspace."""
    if getattr(conn, "workspace", None):    # a tenant's (Postgres): blind to the others
        platform = db.connect_platform()
        try:
            return _check_address(platform, workspace, email)
        finally:
            platform.close()
    member = conn.execute("SELECT workspace FROM members WHERE username = ?",
                          (email,)).fetchone()
    if member:
        raise InviteError("member" if member["workspace"] == workspace else "taken")
    owner = conn.execute("SELECT workspace FROM workspace_domains WHERE domain = ? "
                         "AND verified_at IS NOT NULL", (email.rpartition("@")[2],)).fetchone()
    if owner and owner["workspace"] != workspace:
        raise InviteError("taken")


def create(workspace, email, role, invited_by):
    """A new invite. Returns (invite, token): the token goes in the emailed link
    and nowhere else. Raises InviteError."""
    if role not in members_store.ROLES:
        raise ValueError(f"Role must be one of: {', '.join(members_store.ROLES)}.")
    email, now, token = email.strip().lower(), time.time(), secrets.token_urlsafe(32)
    with members_store._lock, members_store._db() as conn:
        _check_address(conn, workspace, email)
        if conn.execute("SELECT 1 FROM invites WHERE workspace = ? AND email = ? "
                        "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ?",
                        (workspace, email, now)).fetchone():
            raise InviteError("pending")
        cur = conn.execute("""INSERT INTO invites (token_hash, workspace, email, role, invited_by,
                                                   created_at, expires_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?)""",
                           (_hash(token), workspace, email, role, invited_by, now,
                            now + invite_days() * 86400))
        row = conn.execute("SELECT * FROM invites WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _invite(row), token


def renew(workspace, invite_id):
    """"Resend": a new link (the old one stops working) and a new expiry, for an
    invite that is pending or expired. Returns (invite, token), or None."""
    now, token = time.time(), secrets.token_urlsafe(32)
    with members_store._lock, members_store._db() as conn:
        n = conn.execute("UPDATE invites SET token_hash = ?, expires_at = ? WHERE id = ? "
                         "AND workspace = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                         (_hash(token), now + invite_days() * 86400, invite_id,
                          workspace)).rowcount
        row = conn.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
    return (_invite(row), token) if n else None


def mark_sent(invite_id):
    with members_store._lock, members_store._db() as conn:
        conn.execute("UPDATE invites SET sent_at = ? WHERE id = ?", (time.time(), invite_id))


def delete(workspace, invite_id):
    """Forget an invite whose email never went out."""
    with members_store._lock, members_store._db() as conn:
        conn.execute("DELETE FROM invites WHERE id = ? AND workspace = ? AND sent_at IS NULL",
                     (invite_id, workspace))


def revoke(workspace, invite_id):
    """Withdraw a pending (or expired) invite. Returns it, or None if the
    workspace has no such invite still open."""
    with members_store._lock, members_store._db() as conn:
        n = conn.execute("UPDATE invites SET revoked_at = ? WHERE id = ? AND workspace = ? "
                         "AND accepted_at IS NULL AND revoked_at IS NULL",
                         (time.time(), invite_id, workspace)).rowcount
        row = conn.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
    return _invite(row) if n else None


def list_open(workspace):
    """Invites not yet accepted or revoked (pending, and expired ones to resend),
    newest first."""
    with members_store._db() as conn:
        return [_invite(r) for r in conn.execute(
            "SELECT * FROM invites WHERE workspace = ? AND accepted_at IS NULL "
            "AND revoked_at IS NULL ORDER BY id DESC", (workspace,))]


def find(token):
    """The invite a link's token is for (any status), or None."""
    if not token:
        return None
    with members_store._db() as conn:
        return _invite(conn.execute("SELECT * FROM invites WHERE token_hash = ?",
                                    (_hash(token),)).fetchone())


def accept(token, name, password_hash=None):
    """Use an invite: mark it accepted and add its person to the workspace, with
    `password_hash` (None: single sign-on only), all or nothing. Returns the
    invite. Raises InviteError: its status, "invalid" (no such link), or
    "member" / "taken" (see _check_address)."""
    return _accept("token_hash = ?", _hash(token or ""), name, password_hash)


def accept_id(invite_id, name):
    """The same for a single sign-on that finds a pending invite to its workspace
    (join_store.take_invite): their provider proved the address, so no link is
    needed, and no password is set."""
    return _accept("id = ?", invite_id, name, None)


def _accept(where, value, name, password_hash):
    now = time.time()
    with members_store._lock, members_store._db() as conn:
        row = conn.execute(f"SELECT * FROM invites WHERE {where}", (value,)).fetchone()
        if not row:
            raise InviteError("invalid")
        if status(row, now) != "pending":
            raise InviteError(status(row, now))
        n = conn.execute("UPDATE invites SET accepted_at = ? WHERE id = ? AND accepted_at IS NULL "
                         "AND revoked_at IS NULL AND expires_at > ?", (now, row["id"], now)).rowcount
        if not n:                                      # used meanwhile
            raise InviteError("accepted")
        _check_address(conn, row["workspace"], row["email"])
        members_store._ensure_workspace(conn, row["workspace"])
        conn.execute("""INSERT INTO members (username, workspace, name, password_hash,
                                             override_role, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (row["email"], row["workspace"], name or row["email"].split("@")[0],
                      password_hash, row["role"], now))
        members_store._settle_role(conn, row["email"], row["workspace"])
        accepted = conn.execute("SELECT * FROM invites WHERE id = ?", (row["id"],)).fetchone()
    return _invite(accepted)
