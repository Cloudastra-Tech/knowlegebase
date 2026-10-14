"""
sessions_store.py  —  login sessions that survive restarts and deploys.

Sessions used to live in the API's memory, so every restart signed everyone
out. They now live in the same small SQLite file as the integrations
(config/app.db). Only a SHA-256 hash of each token is stored, so a copy of the
database can't be used to sign in.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.getenv("APP_DB", os.path.join("config", "app.db"))
_lock = threading.Lock()


def _db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user       TEXT NOT NULL,     -- JSON: username, name, workspace
        expires    REAL NOT NULL)""")
    return conn


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create(token, user, seconds):
    with _lock, _db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))  # tidy up
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                     (_hash(token), json.dumps(user), time.time() + seconds))


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
