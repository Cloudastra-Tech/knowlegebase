"""
api_keys_store.py  —  keys for the public API (/api/v1) and the MCP server (/mcp),
and what each workspace's admins expose through them.

A key is "kb_live_<prefix>_<secret>", shown once when it's made. Only a SHA-256
hash of the whole key is kept, with its prefix to find it by: the secret is 256
random bits, so a slow hash would add nothing. A revoked or expired key keeps
its row (who made it, when, and what it could do).

KINDS
  personal   any member makes one for themselves. It acts AS them: their role is
             looked up on every call (kb/api_auth.py), so removing them, turning
             them off or demoting them limits the key at once.
  service    an admin makes one for the workspace, e.g. for an intranet page. It
             acts as a PUBLIC user only, never HR: such a key ends up in places
             anyone can read. It stops working once the admin who made it isn't one.

CLIENT   what a key was made for, when not by hand: "extension", a personal key with
         the ask scope that the browser extension got when its person connected it
         (kb/routers/extension.py). Its questions are logged with surface "extension".

SCOPES   what a key may do: ask, search, docs:read, docs:write, audit:read.
         docs:write needs an HR / Admin role, audit:read an admin; checked when
         the key is made and again on every call. A service key gets ask, search
         and docs:read at most.

SETTINGS (api_settings; no row = DEFAULTS): the public API on or off, the MCP
server on or off, and which MCP tools it exposes. read_document is off unless
an admin turns it on: it returns whole documents.
"""

import contextlib
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time

import db
import members_store
from members_store import STAFF_ROLES

PREFIX = "kb_live_"
SCOPES = ("ask", "search", "docs:read", "docs:write", "audit:read")
SERVICE_SCOPES = ("ask", "search", "docs:read")
SCOPE_ROLES = {"docs:write": STAFF_ROLES, "audit:read": ("admin",)}   # the role a scope needs
KINDS = ("personal", "service")
CLIENTS = ("extension",)              # api_keys.client (migration 0013)
EXPIRY_DAYS = (7, 30, 90, 365)
DEFAULT_EXPIRY_DAYS = 90
MAX_PERSONAL = 10                  # active keys per person
MAX_SERVICE = 10                   # active service keys per workspace
NAME_CHARS = 80
USED_EVERY = 60                    # last_used_at is written at most this often
MCP_TOOLS = ("search_knowledge", "ask", "read_document", "list_sources")
DEFAULTS = {"api_enabled": True, "mcp_enabled": True,
            "mcp_tools": ["search_knowledge", "ask", "list_sources"]}

_KEY = re.compile(r"^kb_live_([0-9a-f]{12})_([A-Za-z0-9_-]{20,})$")


class Refused(ValueError):
    """A key can't be made: the message says why, for the person making it."""


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


@contextlib.contextmanager
def _db():
    conn = db.connect()
    try:
        with conn:                 # commits, or rolls back on an error
            yield conn
    finally:
        conn.close()


def status(row, now=None):
    if row["revoked_at"]:
        return "revoked"
    if row["expires_at"] and row["expires_at"] <= (now or time.time()):
        return "expired"
    return "active"


def _key(row):
    if not row:
        return None
    out = {k: row[k] for k in ("id", "workspace", "name", "kind", "prefix", "created_by",
                               "created_at", "last_used_at", "expires_at", "revoked_at",
                               "revoked_by", "client")}
    out["scopes"] = json.loads(row["scopes"] or "[]")
    out["status"] = status(row)
    out["hint"] = f"{PREFIX}{row['prefix']}_…"
    return out


def scopes_for(role, kind="personal"):
    """The scopes a key may have: for a person with `role`, or a service key."""
    if kind == "service":
        return list(SERVICE_SCOPES)
    return [s for s in SCOPES if role in SCOPE_ROLES.get(s, (role,))]


def create(workspace, created_by, name, scopes, role, kind="personal",
           days=DEFAULT_EXPIRY_DAYS, client=None):
    """A new key -> (its row, the key itself): the key is never stored, so this is
    the only time it can be shown. `role`: the maker's role now (a service key
    needs an admin). client: None, or one of CLIENTS: what the key was made for
    (the browser extension's questions are logged as surface "extension").
    Raises Refused with what's wrong."""
    name = " ".join((name or "").split())[:NAME_CHARS]
    scopes = list(dict.fromkeys(scopes or []))
    if kind not in KINDS:
        raise Refused("A key is personal or a service key.")
    if client is not None and (client not in CLIENTS or kind != "personal"):
        raise Refused("Unknown client.")
    if kind == "service" and role != "admin":
        raise Refused("Only admins can make service keys.")
    if not name:
        raise Refused("Give the key a name, so you can tell it apart later.")
    if not scopes:
        raise Refused("Choose at least one thing the key may do.")
    allowed = scopes_for(role, kind)
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise Refused(f"Unknown scope: {', '.join(unknown)}.")
    refused = [s for s in scopes if s not in allowed]
    if refused:
        raise Refused(f"{'A service key' if kind == 'service' else 'Your role'} can't have: "
                        f"{', '.join(refused)}.")
    if days not in EXPIRY_DAYS:
        raise Refused(f"A key lasts {', '.join(map(str, EXPIRY_DAYS))} days.")
    now = time.time()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        members_store._ensure_workspace(conn, workspace)
        if kind == "service":
            active = conn.execute("SELECT COUNT(*) FROM api_keys WHERE workspace = ? AND "
                                  "kind = 'service' AND revoked_at IS NULL AND "
                                  "(expires_at IS NULL OR expires_at > ?)",
                                  (workspace, now)).fetchone()[0]
            limit = MAX_SERVICE
        else:
            active = conn.execute("SELECT COUNT(*) FROM api_keys WHERE workspace = ? AND "
                                  "created_by = ? AND kind = 'personal' AND revoked_at IS NULL "
                                  "AND (expires_at IS NULL OR expires_at > ?)",
                                  (workspace, created_by, now)).fetchone()[0]
            limit = MAX_PERSONAL
        if active >= limit:
            raise Refused(f"There can be {limit} active {kind} keys. "
                            "Revoke one you no longer use first.")
        for _ in range(5):                         # a prefix already taken: another one
            prefix = secrets.token_hex(6)
            key = f"{PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
            try:
                cur = conn.execute(
                    "INSERT INTO api_keys (workspace, name, kind, prefix, hash, scopes, "
                    "created_by, created_at, expires_at, client) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (workspace, name, kind, prefix, _hash(key), json.dumps(scopes), created_by,
                     now, now + days * 86400, client))
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise RuntimeError("couldn't pick a free key prefix")
        row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _key(row), key


def list_keys(workspace, created_by=None, kind=None):
    """The workspace's keys (only one person's, or one kind), newest first."""
    sql, args = "SELECT * FROM api_keys WHERE workspace = ?", [workspace]
    if created_by is not None:
        sql, args = sql + " AND created_by = ?", args + [created_by]
    if kind is not None:
        sql, args = sql + " AND kind = ?", args + [kind]
    with _db() as conn:
        return [_key(r) for r in conn.execute(sql + " ORDER BY id DESC", args)]


def revoke(workspace, key_id, by, created_by=None):
    """The key stops working now. created_by: only if it's theirs (their own
    personal key). Returns it, or None if there's no such active key."""
    sql = ("UPDATE api_keys SET revoked_at = ?, revoked_by = ? WHERE id = ? AND workspace = ? "
           "AND revoked_at IS NULL")
    args = [time.time(), by, key_id, workspace]
    if created_by is not None:
        sql, args = sql + " AND created_by = ? AND kind = 'personal'", args + [created_by]
    with _db() as conn:
        n = conn.execute(sql, args).rowcount
        row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    return _key(row) if n else None


def find(key):
    """The row of an active key (known, not revoked, not expired), or None. Notes
    when it was used, at most every USED_EVERY seconds."""
    m = _KEY.match(key or "")
    if not m:
        return None
    with _db() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE prefix = ?", (m.group(1),)).fetchone()
    if not row or not hmac.compare_digest(row["hash"], _hash(key)) or status(row) != "active":
        return None
    now = time.time()
    if (row["last_used_at"] or 0) < now - USED_EVERY:
        with _db() as conn:
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    return _key(row)


# ---- Settings ------------------------------------------------------------------------
def settings(workspace):
    """{api_enabled, mcp_enabled, mcp_tools, updated_by, updated_at}."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM api_settings WHERE workspace = ?",
                           (workspace,)).fetchone()
    if not row:
        return {**DEFAULTS, "mcp_tools": list(DEFAULTS["mcp_tools"]), "updated_by": None,
                "updated_at": None}
    tools = json.loads(row["mcp_tools"]) if row["mcp_tools"] is not None else DEFAULTS["mcp_tools"]
    return {"api_enabled": bool(row["api_enabled"]), "mcp_enabled": bool(row["mcp_enabled"]),
            "mcp_tools": [t for t in MCP_TOOLS if t in tools],
            "updated_by": row["updated_by"], "updated_at": row["updated_at"]}


def set_settings(workspace, by, api_enabled=None, mcp_enabled=None, mcp_tools=None):
    """Change any of them; the rest stay. Returns the settings now."""
    if mcp_tools is not None:
        unknown = [t for t in mcp_tools if t not in MCP_TOOLS]
        if unknown:
            raise Refused(f"Unknown MCP tool: {', '.join(unknown)}.")
    now = settings(workspace)
    new = {"api_enabled": now["api_enabled"] if api_enabled is None else bool(api_enabled),
           "mcp_enabled": now["mcp_enabled"] if mcp_enabled is None else bool(mcp_enabled),
           "mcp_tools": now["mcp_tools"] if mcp_tools is None else
           [t for t in MCP_TOOLS if t in mcp_tools]}
    with _db() as conn:
        members_store._ensure_workspace(conn, workspace)
        conn.execute("INSERT INTO api_settings (workspace, api_enabled, mcp_enabled, mcp_tools, "
                     "updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(workspace) DO UPDATE SET api_enabled = excluded.api_enabled, "
                     "mcp_enabled = excluded.mcp_enabled, mcp_tools = excluded.mcp_tools, "
                     "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                     (workspace, int(new["api_enabled"]), int(new["mcp_enabled"]),
                      json.dumps(new["mcp_tools"]), by, time.time()))
    return settings(workspace)
