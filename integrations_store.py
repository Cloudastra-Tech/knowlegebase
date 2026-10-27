"""
integrations_store.py  —  which tools each workspace has connected.

A tiny SQLite database (config/app.db, gitignored and mounted in Docker; see db.py).
OAuth tokens are ENCRYPTED (secret_box.py).

Each connection also says WHO may ask about the files it syncs (settings["access"]):
  restricted  HR and admins only, like docs/<workspace>/restricted/ (the default)
  everyone    everyone in the workspace
A connection without the key (new, or made before the setting existed) is
restricted: an admin opens it up on purpose, never by accident.
"""

import json
import threading
import time

import db
import secret_box

_lock = threading.Lock()

ACCESS_LEVELS = ("restricted", "everyone")
DEFAULT_ACCESS = "restricted"


def _db():
    return db.connect()


def _row(r):
    if not r:
        return None
    d = dict(r)
    d["settings"] = json.loads(d.get("settings") or "{}")
    d.pop("secret", None)          # never hand tokens to callers by accident
    return d


def get(workspace, provider):
    with _lock, _db() as conn:
        return _row(conn.execute("SELECT * FROM integrations WHERE workspace=? AND provider=?",
                                 (workspace, provider)).fetchone())


def list_for(workspace):
    with _lock, _db() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM integrations WHERE workspace=?", (workspace,))]


def all_connected(provider):
    with _lock, _db() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM integrations WHERE provider=? AND status != 'disconnected'",
            (provider,))]


def connect(workspace, provider, account, secret, user):
    blob = secret_box.seal(secret, workspace)
    with _lock, _db() as conn:
        conn.execute("""INSERT INTO integrations
            (workspace, provider, status, account, secret, connected_by, connected_at)
            VALUES (?, ?, 'connected', ?, ?, ?, ?)
            ON CONFLICT(workspace, provider) DO UPDATE SET status='connected',
              account=excluded.account, secret=excluded.secret,
              connected_by=excluded.connected_by, connected_at=excluded.connected_at,
              last_error=NULL""",
            (workspace, provider, account, blob, user, time.time()))


def secret(workspace, provider):
    with _lock, _db() as conn:
        r = conn.execute("SELECT secret FROM integrations WHERE workspace=? AND provider=?",
                         (workspace, provider)).fetchone()
    return secret_box.unseal(r["secret"], workspace) if r else None


def update(workspace, provider, **fields):
    if "settings" in fields:
        fields["settings"] = json.dumps(fields["settings"])
    if "secret" in fields:
        fields["secret"] = secret_box.seal(fields["secret"], workspace)
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, _db() as conn:
        conn.execute(f"UPDATE integrations SET {cols} WHERE workspace=? AND provider=?",
                     (*fields.values(), workspace, provider))


def remove(workspace, provider):
    with _lock, _db() as conn:
        conn.execute("DELETE FROM integrations WHERE workspace=? AND provider=?",
                     (workspace, provider))


def access_of(row):
    """Who may ask about a connection's files: "restricted" or "everyone"."""
    level = ((row or {}).get("settings") or {}).get("access")
    return level if level in ACCESS_LEVELS else DEFAULT_ACCESS


def chunk_access(level):
    """The access tag a connection's chunks get in the index (rag.ROLE_ACCESS)."""
    return "public" if level == "everyone" else "restricted"


def chunk_access_levels(workspace):
    """{provider: chunk access tag} for every connection of a workspace. Synced
    files of a provider missing here (e.g. left behind) count as restricted."""
    return {r["provider"]: chunk_access(access_of(r)) for r in list_for(workspace)}
