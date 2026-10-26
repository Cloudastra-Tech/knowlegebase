"""
audit_store.py  —  who changed what in a workspace: its admin audit log.

One row per change (audit_events, in config/app.db; made by migrations/), kept
for good: sign-in settings, members, invites, documents, integrations,
re-indexing, a new workspace. The API writes them through kb/audit.py's
audited(...) dependency; `python auth.py` writes the operator's.

TAMPER EVIDENCE. Each workspace's rows form a hash chain:

    hash = sha256(prev_hash + canonical(row))

where prev_hash is the workspace's previous row's hash (GENESIS for its first)
and canonical(row) is the row's fields as JSON with sorted keys (its id
excluded). Editing a row changes its hash; deleting one breaks the link to the
next. verify() walks the chain. It can't see the NEWEST rows cut off the end:
keep the head hash it returns somewhere else to check against later.

Rows are added under BEGIN IMMEDIATE, so two processes can't both chain onto the
same previous row.
"""

import hashlib
import json
import threading
import time

import db

GENESIS = "0" * 64
ACTOR_TYPES = ("user", "api_key", "scim", "system", "operator")
FIELDS = ("workspace", "at", "actor", "actor_type", "action", "target_type", "target_id",
          "ip", "user_agent", "details", "prev_hash")
PAGE = 50                          # rows per page of list()
MAX_PAGE = 200

_lock = threading.Lock()


def _canonical_details(details):
    return json.dumps(details or {}, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(row):
    """sha256 of the previous hash and the row's canonical form (FIELDS)."""
    body = json.dumps({k: row[k] for k in FIELDS if k != "prev_hash"}, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256((row["prev_hash"] + body).encode()).hexdigest()


def record(workspace, action, actor=None, actor_type="user", target_type=None, target_id=None,
           details=None, ip=None, user_agent=None):
    """Add one event to the workspace's chain. Returns it (a dict, with id and hash)."""
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"actor_type must be one of: {', '.join(ACTOR_TYPES)}.")
    row = {"workspace": workspace, "at": time.time(), "actor": actor, "actor_type": actor_type,
           "action": action, "target_type": target_type,
           "target_id": None if target_id is None else str(target_id),
           "ip": ip, "user_agent": (user_agent or "")[:300] or None,
           "details": _canonical_details(details)}
    with _lock:
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute("SELECT hash FROM audit_events WHERE workspace = ? "
                                "ORDER BY id DESC LIMIT 1", (workspace,)).fetchone()
            row["prev_hash"] = last["hash"] if last else GENESIS
            row["hash"] = row_hash(row)
            cols = (*FIELDS, "hash")
            cur = conn.execute(f"INSERT INTO audit_events ({', '.join(cols)}) "
                               f"VALUES ({', '.join('?' for _ in cols)})",
                               [row[c] for c in cols])
            conn.execute("COMMIT")
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    return {**row, "id": cur.lastrowid}


def _event(row):
    e = dict(row)
    e["details"] = json.loads(e["details"] or "{}")
    return e


def _where(workspace, actor=None, action=None, since=None, until=None):
    """SQL and arguments for the filters list() and export() share. `action`
    ending in "." matches every action it starts (e.g. "member.")."""
    sql, args = ["workspace = ?"], [workspace]
    if actor:
        sql.append("actor LIKE ? ESCAPE '\\'")
        args.append("%" + actor.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if action:
        if action.endswith("."):
            sql.append("substr(action, 1, ?) = ?")
            args += [len(action), action]
        else:
            sql.append("action = ?")
            args.append(action)
    if since is not None:
        sql.append("at >= ?")
        args.append(since)
    if until is not None:
        sql.append("at < ?")
        args.append(until)
    return " AND ".join(sql), args


def list_events(workspace, actor=None, action=None, since=None, until=None, cursor=None,
                limit=PAGE):
    """A page of events, newest first: (events, next cursor or None). The cursor
    is the id to continue below."""
    where, args = _where(workspace, actor, action, since, until)
    if cursor is not None:
        where, args = where + " AND id < ?", args + [cursor]
    limit = max(1, min(int(limit), MAX_PAGE))
    conn = db.connect()
    try:
        rows = conn.execute(f"SELECT * FROM audit_events WHERE {where} ORDER BY id DESC "
                            "LIMIT ?", args + [limit + 1]).fetchall()
    finally:
        conn.close()
    more = len(rows) > limit
    rows = rows[:limit]
    return [_event(r) for r in rows], (rows[-1]["id"] if more and rows else None)


def iter_events(workspace, actor=None, action=None, since=None, until=None, batch=500):
    """Every matching event, oldest first, read a batch at a time (for export)."""
    where, args = _where(workspace, actor, action, since, until)
    after = 0
    while True:
        conn = db.connect()
        try:
            rows = conn.execute(f"SELECT * FROM audit_events WHERE {where} AND id > ? "
                                "ORDER BY id LIMIT ?", args + [after, batch]).fetchall()
        finally:
            conn.close()
        if not rows:
            return
        for r in rows:
            yield _event(r)
        after = rows[-1]["id"]


def verify(workspace):
    """Walk the workspace's chain. {"ok", "count", "head", "broken_at"}: the id of
    the first row whose hash or link doesn't match, if any."""
    prev, count = GENESIS, 0
    conn = db.connect()
    try:
        for row in conn.execute("SELECT * FROM audit_events WHERE workspace = ? ORDER BY id",
                                (workspace,)):
            count += 1
            if row["prev_hash"] != prev or row_hash(row) != row["hash"]:
                return {"ok": False, "count": count, "head": None, "broken_at": row["id"]}
            prev = row["hash"]
    finally:
        conn.close()
    return {"ok": True, "count": count, "head": prev if count else None, "broken_at": None}
