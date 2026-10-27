"""
spaces_store.py  —  spaces (config/app.db, tables made by
migrations/versions/0018_spaces_attachments_prompts.py):

    spaces          a person's own space (kind personal, one each) or a team's
    space_members   who is in a team space, as owner or member
    space_sources   what a space searches: a document key, a folder or a provider
                    of the workspace's index (the source rules' match types)

Only SQL lives here. Who may see or change a space, and what asking in one
searches, is kb/services/spaces.py. Every read names the workspace.
"""

import contextlib
import sqlite3
import time
import uuid

import db

KINDS = ("personal", "team")
ROLES = ("owner", "member")
MATCH_TYPES = ("doc_key", "folder", "provider")
NAME_CHARS = 60
DESCRIPTION_CHARS = 200
INSTRUCTIONS_CHARS = 1500     # as the assistant's own instructions (assistant_store)
MAX_SOURCES = 200
MAX_MEMBERS = 500


@contextlib.contextmanager
def _db():
    conn = db.connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _space(row):
    return dict(row) if row else None


def get(workspace, space_id):
    with _db() as conn:
        return _space(conn.execute("SELECT * FROM spaces WHERE id = ? AND workspace = ?",
                                   (space_id, workspace)).fetchone())


def personal(workspace, username):
    """The person's own space, made the first time it's asked for."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM spaces WHERE workspace = ? AND owner = ? AND "
                           "kind = 'personal'", (workspace, username)).fetchone()
        if row:
            return _space(row)
        now = time.time()
        sid = uuid.uuid4().hex
        try:
            conn.execute("INSERT INTO spaces (id, workspace, kind, name, owner, created_by, "
                         "created_at, updated_at) VALUES (?, ?, 'personal', 'My space', ?, ?, "
                         "?, ?)", (sid, workspace, username, username, now, now))
        except sqlite3.IntegrityError:      # made by another request just now
            pass
        return _space(conn.execute("SELECT * FROM spaces WHERE workspace = ? AND owner = ? AND "
                                   "kind = 'personal'", (workspace, username)).fetchone())


def create_team(workspace, name, description, instructions, created_by):
    """A team space; its maker is its first owner."""
    now = time.time()
    sid = uuid.uuid4().hex
    with _db() as conn:
        conn.execute("INSERT INTO spaces (id, workspace, kind, name, description, instructions, "
                     "created_by, created_at, updated_at) VALUES (?, ?, 'team', ?, ?, ?, ?, ?, ?)",
                     (sid, workspace, name, description, instructions, created_by, now, now))
        conn.execute("INSERT INTO space_members (space_id, workspace, username, role, added_by, "
                     "added_at) VALUES (?, ?, ?, 'owner', ?, ?)",
                     (sid, workspace, created_by, created_by, now))
    return get(workspace, sid)


def update(workspace, space_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("name", "description", "instructions")}
    if not allowed:
        return get(workspace, space_id)
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with _db() as conn:
        conn.execute(f"UPDATE spaces SET {sets}, updated_at = ? WHERE id = ? AND workspace = ?",
                     (*allowed.values(), time.time(), space_id, workspace))
    return get(workspace, space_id)


def delete(workspace, space_id):
    """Gone with its members, sources and files (cascade). Its chats stay, outside
    any space."""
    with _db() as conn:
        conn.execute("UPDATE conversations SET space_id = NULL WHERE workspace = ? AND "
                     "space_id = ?", (workspace, space_id))
        return conn.execute("DELETE FROM spaces WHERE id = ? AND workspace = ?",
                            (space_id, workspace)).rowcount > 0


def team_spaces_of(workspace, username):
    """The team spaces the person is in, with their role in each."""
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT s.*, m.role AS my_role FROM spaces s JOIN space_members m ON "
            "m.space_id = s.id WHERE s.workspace = ? AND m.username = ? AND s.kind = 'team' "
            "ORDER BY s.name COLLATE NOCASE", (workspace, username))]


def all_team_spaces(workspace):
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM spaces WHERE workspace = ? AND kind = 'team' "
            "ORDER BY name COLLATE NOCASE", (workspace,))]


# ---- Members --------------------------------------------------------------------------
def members(workspace, space_id):
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT username, role, added_by, added_at FROM space_members WHERE workspace = ? "
            "AND space_id = ? ORDER BY role DESC, username", (workspace, space_id))]


def member_role(workspace, space_id, username):
    with _db() as conn:
        row = conn.execute("SELECT role FROM space_members WHERE workspace = ? AND space_id = ? "
                           "AND username = ?", (workspace, space_id, username)).fetchone()
    return row[0] if row else None


def set_member(workspace, space_id, username, role, added_by):
    with _db() as conn:
        conn.execute("INSERT INTO space_members (space_id, workspace, username, role, added_by, "
                     "added_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (space_id, username) DO "
                     "UPDATE SET role = excluded.role",
                     (space_id, workspace, username, role, added_by, time.time()))


def remove_member(workspace, space_id, username):
    with _db() as conn:
        return conn.execute("DELETE FROM space_members WHERE workspace = ? AND space_id = ? AND "
                            "username = ?", (workspace, space_id, username)).rowcount > 0


def owners(workspace, space_id):
    return [m["username"] for m in members(workspace, space_id) if m["role"] == "owner"]



# ---- Sources --------------------------------------------------------------------------
def sources(workspace, space_id):
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, match_type, match_value, added_by, added_at FROM space_sources WHERE "
            "workspace = ? AND space_id = ? ORDER BY match_type, match_value",
            (workspace, space_id))]


def add_source(workspace, space_id, match_type, match_value, added_by):
    with _db() as conn:
        conn.execute("INSERT OR IGNORE INTO space_sources (space_id, workspace, match_type, "
                     "match_value, added_by, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (space_id, workspace, match_type, match_value, added_by, time.time()))
        return conn.execute("SELECT id, match_type, match_value, added_by, added_at FROM "
                            "space_sources WHERE space_id = ? AND match_type = ? AND "
                            "match_value = ?", (space_id, match_type, match_value)).fetchone()


def remove_source(workspace, space_id, source_id):
    with _db() as conn:
        return conn.execute("DELETE FROM space_sources WHERE workspace = ? AND space_id = ? AND "
                            "id = ?", (workspace, space_id, source_id)).rowcount > 0
