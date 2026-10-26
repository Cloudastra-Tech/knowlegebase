"""
sessions_store.py  —  login sessions that survive restarts and deploys.

Sessions used to live in the API's memory, so every restart signed everyone
out. They now live in the same small SQLite file as the integrations
(config/app.db, see db.py). Only a SHA-256 hash of each token is stored, so a
copy of the database can't be used to sign in.

Each session also records whose it is (username, workspace), so all of one
person's sessions can be ended at once: "sign out everywhere", or an admin
turning their access off. Sessions from before those columns had them filled
in from the stored user (migrations/legacy.py), so they keep working.
"""

import hashlib
import json
import threading
import time

import db

_lock = threading.Lock()


def _db():
    return db.connect()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _username(user):
    return (user.get("username") or "").strip().lower() or None


def create(token, user, seconds):
    with _lock, _db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))  # tidy up
        conn.execute("INSERT INTO sessions (token_hash, user, expires, username, workspace) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (_hash(token), json.dumps(user), time.time() + seconds, _username(user),
                      user.get("workspace")))


def get(token):
    """The signed-in user for a token, or None if unknown / expired."""
    if not token:
        return None
    with _lock, _db() as conn:
        row = conn.execute("SELECT user, expires FROM sessions WHERE token_hash = ?",
                           (_hash(token),)).fetchone()
    if not row or row[1] < time.time():
        return None
    return json.loads(row[0])


def delete(token):
    with _lock, _db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


def delete_user(username, workspace=None):
    """End every session one person has (in `workspace`, if given; a session
    with no workspace recorded is theirs too). Returns how many ended."""
    sql, args = "DELETE FROM sessions WHERE username = ?", [(username or "").strip().lower()]
    if workspace:
        sql, args = sql + " AND (workspace = ? OR workspace IS NULL)", args + [workspace]
    with _lock, _db() as conn:
        return conn.execute(sql, args).rowcount
