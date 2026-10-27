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

ERASED PEOPLE (a data request, PL-12): their rows aren't edited (that would
break the chain). The person is mapped to a pseudonym (audit_pseudonyms, by a
hash of their username, never the name), and every read here shows the
pseudonym instead: as the actor, the target, and any detail that was their
username. verify() still checks the rows as hashed.

RETENTION: a workspace may keep events for a number of days instead of for good
(lifecycle_store.audit_days). purge_before() deletes the oldest ones and keeps
the hash of the last one deleted (audit_anchors), so the chain still verifies
from there.

PLATFORM: the operator's own events (operators signing in, a workspace purged)
are a chain of their own, workspace PLATFORM, which no workspace can be called.
"""

import hashlib
import json
import logging
import secrets
import threading
import time

import db

GENESIS = "0" * 64
PLATFORM = "_platform"             # the operators' chain (not a valid workspace slug)
ACTOR_TYPES = ("user", "api_key", "scim", "system", "operator")
FIELDS = ("workspace", "at", "actor", "actor_type", "action", "target_type", "target_id",
          "ip", "user_agent", "details", "prev_hash")
PAGE = 50                          # rows per page of list()
MAX_PAGE = 200

_lock = threading.Lock()
log = logging.getLogger("uvicorn.error")
# Called with each event once it's written (kb/services/webhooks.py: audit.event).
LISTENERS = []


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
        if workspace == db.tenant():
            conn = db.connect()
        else:          # e.g. "_platform" (operator-level), from inside a workspace's request
            with db.platform():
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
    event = {**row, "id": cur.lastrowid}
    for listener in list(LISTENERS):
        try:
            listener(event)
        except Exception:               # a listener never fails the change itself
            log.exception("Audit: a listener failed on %s", action)
    return event


def listen(fn):
    """Call fn(event) after every event is written (each function once)."""
    if fn not in LISTENERS:
        LISTENERS.append(fn)


def _actor_hash(workspace, username):
    return hashlib.sha256(f"{workspace}\0{(username or '').strip().lower()}".encode()).hexdigest()


def _pseudonyms(conn, workspace):
    """{actor hash: pseudonym} of the people erased in the workspace."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT actor_hash, pseudonym FROM audit_pseudonyms WHERE workspace = ?", (workspace,))}


def _shown(workspace, names):
    """The function a value goes through on its way out: an erased person's
    username becomes their pseudonym, anything else is itself."""
    if not names:
        return lambda v: v
    return lambda v: (names.get(_actor_hash(workspace, v), v)
                      if isinstance(v, str) and v else v)


def _masked(value, shown):
    if isinstance(value, dict):
        return {k: _masked(v, shown) for k, v in value.items()}
    if isinstance(value, list):
        return [_masked(v, shown) for v in value]
    return shown(value)


def _event(row, shown=None):
    e = dict(row)
    e["details"] = json.loads(e["details"] or "{}")
    if shown is not None:
        actor = shown(e["actor"])
        if actor != e["actor"]:                 # erased: where they were isn't shown either
            e["ip"] = e["user_agent"] = None
        e["actor"], e["target_id"] = actor, shown(e["target_id"])
        e["details"] = _masked(e["details"], shown)
    return e


def _reading(conn, workspace):
    """Ready a connection to read the workspace's events: shown_actor(actor) in SQL
    is what the actor filter matches. Returns the function for _event()."""
    shown = _shown(workspace, _pseudonyms(conn, workspace))
    if not db.is_postgres():           # Postgres can't call Python: see _matching_actors
        conn.create_function("shown_actor", 1, shown, deterministic=True)
    return shown


def _matching_actors(conn, workspace, actor, shown):
    """Postgres: the workspace's actors whose shown name contains `actor` (as
    shown_actor(actor) LIKE '%actor%' matches them on SQLite, case aside)."""
    needle = actor.lower()
    return [r[0] for r in conn.execute("SELECT DISTINCT actor FROM audit_events WHERE "
                                       "workspace = ? AND actor IS NOT NULL", (workspace,))
            if needle in str(shown(r[0])).lower()]


def _where(workspace, actor=None, action=None, since=None, until=None, actors=None):
    """SQL and arguments for the filters list() and export() share. `action`
    ending in "." matches every action it starts (e.g. "member."). actors: on
    Postgres, the actor filter as the names it matches (_matching_actors)."""
    sql, args = ["workspace = ?"], [workspace]
    if actor and actors is not None:
        sql.append(f"actor IN ({', '.join('?' * len(actors))})" if actors else "1 = 0")
        args += actors
    elif actor:
        sql.append("shown_actor(actor) LIKE ? ESCAPE '\\'")
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
    limit = max(1, min(int(limit), MAX_PAGE))
    conn = db.connect()
    try:
        shown = _reading(conn, workspace)
        where, args = _where(workspace, actor, action, since, until,
                             _matching_actors(conn, workspace, actor, shown)
                             if actor and db.is_postgres() else None)
        if cursor is not None:
            where, args = where + " AND id < ?", args + [cursor]
        rows = conn.execute(f"SELECT * FROM audit_events WHERE {where} ORDER BY id DESC "
                            "LIMIT ?", args + [limit + 1]).fetchall()
    finally:
        conn.close()
    more = len(rows) > limit
    rows = rows[:limit]
    return [_event(r, shown) for r in rows], (rows[-1]["id"] if more and rows else None)


def iter_events(workspace, actor=None, action=None, since=None, until=None, batch=500):
    """Every matching event, oldest first, read a batch at a time (for export)."""
    where = args = None
    after = 0
    while True:
        conn = db.connect()
        try:
            shown = _reading(conn, workspace)
            if where is None:
                where, args = _where(workspace, actor, action, since, until,
                                     _matching_actors(conn, workspace, actor, shown)
                                     if actor and db.is_postgres() else None)
            rows = conn.execute(f"SELECT * FROM audit_events WHERE {where} AND id > ? "
                                "ORDER BY id LIMIT ?", args + [after, batch]).fetchall()
        finally:
            conn.close()
        if not rows:
            return
        for r in rows:
            yield _event(r, shown)
        after = rows[-1]["id"]


def by_actor(workspace, username):
    """Every event this person did (by their username exactly), oldest first (for
    their own data request): others erased in them still show as pseudonyms."""
    conn = db.connect()
    try:
        shown = _reading(conn, workspace)
        rows = conn.execute("SELECT * FROM audit_events WHERE workspace = ? AND lower(actor) = ? "
                            "ORDER BY id", (workspace, (username or "").strip().lower())).fetchall()
    finally:
        conn.close()
    events = [_event(r, shown) for r in rows]
    for e in events:                                   # their own name, not a pseudonym
        e["actor"] = username
    return events


def erase_actor(workspace, username):
    """Show this person under a pseudonym from now on (their rows are untouched,
    so the chain still verifies). Returns the pseudonym (the same one if they
    were erased before)."""
    h = _actor_hash(workspace, username)
    conn = db.connect()
    try:
        with conn:
            conn.execute("INSERT OR IGNORE INTO audit_pseudonyms (workspace, actor_hash, "
                         "pseudonym, erased_at) VALUES (?, ?, ?, ?)",
                         (workspace, h, "erased:" + secrets.token_hex(6), time.time()))
            return conn.execute("SELECT pseudonym FROM audit_pseudonyms WHERE workspace = ? AND "
                                "actor_hash = ?", (workspace, h)).fetchone()[0]
    finally:
        conn.close()


def anchor(workspace):
    """(last id deleted for retention, its hash), or (0, GENESIS)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT last_id, last_hash FROM audit_anchors WHERE workspace = ?",
                           (workspace,)).fetchone()
    finally:
        conn.close()
    return (row[0], row[1]) if row else (0, GENESIS)


def purge_before(workspace, before):
    """Delete the workspace's oldest events, up to the first one at or after
    `before` (a time): only from the start of the chain, so what's left still
    links. The hash of the last one deleted becomes the chain's anchor. Returns
    how many were deleted."""
    with _lock:
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            keep = conn.execute("SELECT MIN(id) FROM audit_events WHERE workspace = ? AND at >= ?",
                                (workspace, before)).fetchone()[0]
            sql, args = "SELECT id, hash FROM audit_events WHERE workspace = ?", [workspace]
            if keep is not None:
                sql, args = sql + " AND id < ?", args + [keep]
            cut = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
            if not cut:
                conn.execute("COMMIT")
                return 0
            n = conn.execute("DELETE FROM audit_events WHERE workspace = ? AND id <= ?",
                             (workspace, cut["id"])).rowcount
            conn.execute("""INSERT INTO audit_anchors (workspace, last_id, last_hash, purged,
                                updated_at) VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT (workspace) DO UPDATE SET last_id = excluded.last_id,
                                last_hash = excluded.last_hash,
                                purged = audit_anchors.purged + excluded.purged,
                                updated_at = excluded.updated_at""",
                         (workspace, cut["id"], cut["hash"], n, time.time()))
            conn.execute("COMMIT")
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    return n


def verify(workspace):
    """Walk the workspace's chain (from its anchor, if old events were deleted for
    retention). {"ok", "count", "head", "broken_at"}: the id of the first row whose
    hash or link doesn't match, if any."""
    after, prev = anchor(workspace)
    count = 0
    conn = db.connect()
    try:
        for row in conn.execute("SELECT * FROM audit_events WHERE workspace = ? AND id > ? "
                                "ORDER BY id", (workspace, after)):
            count += 1
            if row["prev_hash"] != prev or row_hash(row) != row["hash"]:
                return {"ok": False, "count": count, "head": None, "broken_at": row["id"]}
            prev = row["hash"]
    finally:
        conn.close()
    return {"ok": True, "count": count, "head": prev if count else None, "broken_at": None}
