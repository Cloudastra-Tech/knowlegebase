"""
drafts_store.py  —  documents the assistant wrote for review (migration 0016;
platform.md PL-31): a folder's or a source's summary, or a doc for a content gap.

A draft's row says what it is, what it was written from (sources, and the
highest access level among them) and where it is in its life:

    generating -> draft -> published        (or failed, if writing it failed)

Its text is a file, <docs>/drafts/<id>.txt. ingest.doc_files never reads the
drafts/ folder, so a draft is in no search index until it's published, which
copies it into public/ or restricted/ and indexes it there.
"""

import json
import os
import secrets
import time

import db
import workspaces

FOLDER = "drafts"                 # under a workspace's docs root; ingest skips it
STATUSES = ("generating", "draft", "failed", "published")
KINDS = ("summary", "gap")
MAX_CHARS = 200_000


def _db():
    return db.connect()


def folder(ws):
    return os.path.join(workspaces.docs_root(ws), FOLDER)


def path(ws, draft_id):
    return os.path.join(folder(ws), f"{draft_id}.txt")


def _row(r):
    if not r:
        return None
    d = dict(r)
    d["scope"] = json.loads(d["scope"] or "{}")
    d["sources"] = json.loads(d["sources"] or "[]")
    return d


def create(ws, kind, title, scope, created_by, now=None):
    now = time.time() if now is None else now
    draft_id = secrets.token_hex(8)
    with _db() as conn:
        conn.execute("""INSERT INTO drafts (id, workspace, kind, title, scope, created_by,
                            created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (draft_id, ws, kind, title[:200], json.dumps(scope), created_by, now, now))
    return get(ws, draft_id)


def get(ws, draft_id):
    with _db() as conn:
        return _row(conn.execute("SELECT * FROM drafts WHERE workspace = ? AND id = ?",
                                 (ws, draft_id)).fetchone())


def list_drafts(ws, include_published=False):
    sql = "SELECT * FROM drafts WHERE workspace = ?"
    if not include_published:
        sql += " AND status != 'published'"
    with _db() as conn:
        return [_row(r) for r in conn.execute(sql + " ORDER BY created_at DESC", (ws,))]


def update(ws, draft_id, **fields):
    for key in ("scope", "sources"):
        if key in fields:
            fields[key] = json.dumps(fields[key])
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _db() as conn:
        conn.execute(f"UPDATE drafts SET {cols} WHERE workspace = ? AND id = ?",
                     (*fields.values(), ws, draft_id))
    return get(ws, draft_id)


def delete(ws, draft_id):
    remove_text(ws, draft_id)
    with _db() as conn:
        return conn.execute("DELETE FROM drafts WHERE workspace = ? AND id = ?",
                            (ws, draft_id)).rowcount == 1


def read_text(ws, draft_id):
    try:
        with open(path(ws, draft_id), encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write_text(ws, draft_id, text):
    os.makedirs(folder(ws), exist_ok=True)
    target = path(ws, draft_id)
    partial = target + ".part"
    with open(partial, "w", encoding="utf-8") as f:
        f.write(text[:MAX_CHARS])
    os.replace(partial, target)


def remove_text(ws, draft_id):
    try:
        os.remove(path(ws, draft_id))
    except FileNotFoundError:
        pass
