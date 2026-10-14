"""
integrations_store.py  —  which tools each workspace has connected.

A tiny SQLite database (config/app.db, gitignored and mounted in Docker).
OAuth tokens are ENCRYPTED with a key from INTEGRATIONS_KEY, or — if that is
not set — a key generated once into config/integrations.key.
"""

import json
import os
import sqlite3
import threading
import time

from cryptography.fernet import Fernet

DB_PATH = os.getenv("APP_DB", os.path.join("config", "app.db"))
KEY_PATH = os.path.join(os.path.dirname(DB_PATH) or ".", "integrations.key")
_lock = threading.Lock()


def _fernet():
    key = os.getenv("INTEGRATIONS_KEY")
    if not key:
        if not os.path.exists(KEY_PATH):
            os.makedirs(os.path.dirname(KEY_PATH) or ".", exist_ok=True)
            with open(KEY_PATH, "wb") as f:
                f.write(Fernet.generate_key())
            try:
                os.chmod(KEY_PATH, 0o600)
            except OSError:
                pass
        with open(KEY_PATH, "rb") as f:
            key = f.read().strip()
    return Fernet(key)


def _db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS integrations (
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
        PRIMARY KEY (workspace, provider))""")
    return conn


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
    blob = _fernet().encrypt(json.dumps(secret).encode())
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
    return json.loads(_fernet().decrypt(r["secret"])) if r and r["secret"] else None


def update(workspace, provider, **fields):
    if "settings" in fields:
        fields["settings"] = json.dumps(fields["settings"])
    if "secret" in fields:
        fields["secret"] = _fernet().encrypt(json.dumps(fields["secret"]).encode())
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, _db() as conn:
        conn.execute(f"UPDATE integrations SET {cols} WHERE workspace=? AND provider=?",
                     (*fields.values(), workspace, provider))


def remove(workspace, provider):
    with _lock, _db() as conn:
        conn.execute("DELETE FROM integrations WHERE workspace=? AND provider=?",
                     (workspace, provider))
