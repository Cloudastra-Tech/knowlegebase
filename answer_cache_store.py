"""
answer_cache_store.py  —  answers kept for a few minutes (config/app.db, table made
by migrations/versions/0018_cache_quality_pii_abuse.py). Only SQL lives here; who
may reuse which answer, and when, is kb/services/answer_cache.py.

    answer_cache    one row per (workspace, key): the key hashes the question with
                    its PARTITION, and the partition hashes everything the answer
                    depends on besides the question: the asker's principals, the
                    index, settings and verified answers as they were, the space
                    and the release. A row is only ever found again through its
                    key or its partition, so a changed principal set, index or
                    setting never finds it.
"""

import json
import threading
import time

import db

_lock = threading.Lock()


def _db():
    return db.connect()


def _row(r):
    if r is None:
        return None
    out = dict(r)
    for k in ("sources", "trace"):
        out[k] = json.loads(out.get(k) or "[]")
    out["acl"] = json.loads(out["acl"]) if out.get("acl") is not None else None
    return out


def get(workspace, key, now=None):
    """The live row for a key, or None."""
    now = time.time() if now is None else now
    with _db() as conn:
        return _row(conn.execute("SELECT * FROM answer_cache WHERE workspace = ? AND key = ? "
                                 "AND expires_at > ?", (workspace, key, now)).fetchone())


def partition(workspace, part, now=None):
    """The live rows of a partition that have an embedding (the semantic cache)."""
    now = time.time() if now is None else now
    with _db() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM answer_cache WHERE workspace = ? AND partition = ? AND "
            "expires_at > ? AND embedding IS NOT NULL ORDER BY created_at DESC LIMIT 500",
            (workspace, part, now))]


def put(workspace, row, keep=2000, now=None):
    """Keep one answer (replacing the key's old one), then drop the workspace's
    expired rows and those made under another index, settings or verified answers
    (they can't be found again), and the oldest beyond `keep`."""
    now = time.time() if now is None else now
    cols = ("workspace", "key", "partition", "fingerprint", "username", "index_version",
            "settings_version", "verified_stamp", "question", "embedding", "answer", "sources",
            "state", "max_access", "acl", "trace", "created_at", "expires_at")
    values = {**row, "workspace": workspace, "created_at": now,
              "sources": json.dumps(row.get("sources") or []),
              "trace": json.dumps(row.get("trace") or []),
              "acl": None if row.get("acl") is None else json.dumps(row["acl"])}
    with _lock, _db() as conn:
        conn.execute(f"INSERT OR REPLACE INTO answer_cache ({', '.join(cols)}) "
                     f"VALUES ({', '.join('?' * len(cols))})",
                     [values.get(c) for c in cols])
        conn.execute("DELETE FROM answer_cache WHERE workspace = ? AND (expires_at <= ? OR "
                     "index_version != ? OR settings_version != ? OR verified_stamp != ?)",
                     (workspace, now, row["index_version"], row["settings_version"],
                      row["verified_stamp"]))
        conn.execute("DELETE FROM answer_cache WHERE workspace = ? AND id NOT IN (SELECT id FROM "
                     "answer_cache WHERE workspace = ? ORDER BY created_at DESC LIMIT ?)",
                     (workspace, workspace, keep))


def hit(row_id):
    with _lock, _db() as conn:
        conn.execute("UPDATE answer_cache SET hits = hits + 1 WHERE id = ?", (row_id,))


def clear(workspace):
    """Forget every answer of a workspace. Returns how many."""
    with _lock, _db() as conn:
        return conn.execute("DELETE FROM answer_cache WHERE workspace = ?",
                            (workspace,)).rowcount


def count(workspace):
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM answer_cache WHERE workspace = ?",
                            (workspace,)).fetchone()[0]
