"""
sessions_store.py  —  login sessions that survive restarts and deploys.

Sessions used to live in the API's memory, so every restart signed everyone
out. They now live in the same small SQLite file as the integrations
(config/app.db). Only a SHA-256 hash of each token is stored, so a copy of the
database can't be used to sign in.

Each session also records whose it is (username, workspace), so all of one
person's sessions can be ended at once: "sign out everywhere", or an admin
turning their access off. A table from before those columns gets them on first
use, filled in from the stored user, so existing sessions keep working.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.getenv("APP_DB", os.path.join("config", "app.db"))
_lock = threading.Lock()
_migrated = set()         # database files whose sessions table has the new columns


def _db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user       TEXT NOT NULL,     -- JSON: username, name, workspace
        expires    REAL NOT NULL,
        username   TEXT,              -- lower case: whose session it is
        workspace  TEXT)""")
    if DB_PATH not in _migrated:
        _migrate(conn)
        _migrated.add(DB_PATH)
    return conn


def _migrate(conn):
    """Give an older table the username / workspace columns and fill them in.
    Safe to run again."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for column in ("username", "workspace"):
        if column not in columns:
            try:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError as e:   # another worker added it just now
                if "duplicate column" not in str(e):
                    raise
    rows = conn.execute("SELECT token_hash, user FROM sessions WHERE username IS NULL").fetchall()
    for token_hash, user in rows:
        try:
            user = json.loads(user)
        except ValueError:
            continue
        if isinstance(user, dict):
            conn.execute("UPDATE sessions SET username = ?, workspace = ? WHERE token_hash = ?",
                         (_username(user), user.get("workspace"), token_hash))
    conn.execute("CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (username)")
    conn.commit()


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
