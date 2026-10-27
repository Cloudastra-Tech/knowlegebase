"""
digests_store.py  —  the change feed, digest subscriptions and document
summaries, in config/app.db (migration 0016; platform.md PL-30).

    doc_changes           one row per document added, modified or removed (an
                          upload, an edit, a connector sync), with the ACL (and
                          access level) it had then. Kept CHANGES_KEPT_DAYS.
    digest_subscriptions  a member's digest: daily or weekly, of some sources or
                          folders (none: everything), by email or Slack DM. The
                          email's unsubscribe link carries a token; only its
                          sha256 is looked up, and the token itself is kept sealed
                          under the workspace's key (it goes in every email).
    doc_summaries         a short summary of one version of a document (its
                          content's sha256), made once and reused by every
                          digest and folder summary that needs it

Nothing here decides who may read what: kb/services/digests.py filters each
recipient's changes (kb/services/access.py) when the digest is sent.
"""

import hashlib
import json
import secrets
import time

import db
import secret_box

CHANGES = ("added", "modified", "removed")
FREQUENCIES = {"daily": 86400, "weekly": 7 * 86400}
CHANNELS = ("email", "slack")
CHANGES_KEPT_DAYS = 60
MAX_SUBSCRIPTIONS = 10          # per member
# The unsubscribe token is sealed under the workspace's key: rotation reaches it.
secret_box.register("digest_subscriptions", "unsubscribe_token", ("id",))
_SUB_FIELDS = ("id", "workspace", "username", "frequency", "sources", "folders", "channel",
               "enabled", "last_sent_at", "next_due_at", "last_error", "created_at",
               "updated_at")


def _db():
    return db.connect()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---- doc_changes ---------------------------------------------------------------------
def record_change(workspace, doc_key, change, *, provider, acl, access, title=None,
                  folder=None, version=None, actor=None, at=None):
    """acl: the document's principals then (access.py); access: its level then."""
    if change not in CHANGES:
        raise ValueError(f"change must be one of: {', '.join(CHANGES)}")
    with _db() as conn:
        conn.execute("""INSERT INTO doc_changes (workspace, doc_key, provider, folder, title,
                            change, access, acl, version, actor, at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (workspace, doc_key, provider, folder, (title or "")[:300] or None, change,
                      access or "restricted", json.dumps(sorted(acl or ["nobody"])), version,
                      actor, at or time.time()))


def changes(workspace, since, until=None):
    """The workspace's changes in [since, until), oldest first."""
    until = time.time() if until is None else until
    with _db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM doc_changes WHERE workspace = ? AND at >= ? AND at < ? "
            "ORDER BY at, id", (workspace, since, until))]
    for r in rows:
        r["acl"] = json.loads(r["acl"] or '["nobody"]')
    return rows


def prune_changes(now=None, days=CHANGES_KEPT_DAYS):
    now = time.time() if now is None else now
    with _db() as conn:
        return conn.execute("DELETE FROM doc_changes WHERE at < ?",
                            (now - days * 86400,)).rowcount


# ---- digest_subscriptions -------------------------------------------------------------
def _sub(row):
    if not row:
        return None
    d = {k: row[k] for k in _SUB_FIELDS}
    d["sources"] = json.loads(d["sources"] or "[]")
    d["folders"] = json.loads(d["folders"] or "[]")
    d["enabled"] = bool(d["enabled"])
    return d


def subscriptions(workspace, username):
    with _db() as conn:
        return [_sub(r) for r in conn.execute(
            "SELECT * FROM digest_subscriptions WHERE workspace = ? AND username = ? "
            "ORDER BY id", (workspace, username))]


def subscription(workspace, sub_id):
    with _db() as conn:
        return _sub(conn.execute("SELECT * FROM digest_subscriptions WHERE workspace = ? "
                                 "AND id = ?", (workspace, sub_id)).fetchone())


def subscribe(workspace, username, frequency, channel, sources=(), folders=(), now=None):
    """A new subscription; returns it. The first digest goes out one period from now."""
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(24)
    with _db() as conn:
        sub_id = conn.execute(
            """INSERT INTO digest_subscriptions (workspace, username, frequency, sources,
                   folders, channel, unsubscribe_hash, unsubscribe_token, next_due_at,
                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (workspace, username, frequency, json.dumps(sorted(set(sources))),
             json.dumps(sorted(set(folders))), channel, _hash(token),
             secret_box.seal(token, workspace), now + FREQUENCIES[frequency], now,
             now)).lastrowid
    return subscription(workspace, sub_id)


def update(workspace, sub_id, now=None, **fields):
    """Change frequency, channel, sources, folders or enabled. A new frequency
    moves the next digest to one period after the last (or from now)."""
    now = time.time() if now is None else now
    current = subscription(workspace, sub_id)
    if not current:
        return None
    sets = {}
    for key in ("frequency", "channel"):
        if fields.get(key) is not None:
            sets[key] = fields[key]
    for key in ("sources", "folders"):
        if fields.get(key) is not None:
            sets[key] = json.dumps(sorted(set(fields[key])))
    if fields.get("enabled") is not None:
        sets["enabled"] = int(bool(fields["enabled"]))
    if "frequency" in sets or (sets.get("enabled") and not current["enabled"]):
        start = current["last_sent_at"] or now
        sets["next_due_at"] = max(now, start + FREQUENCIES[sets.get("frequency",
                                                                     current["frequency"])])
    if not sets:
        return current
    sets["updated_at"] = now
    cols = ", ".join(f"{k} = ?" for k in sets)
    with _db() as conn:
        conn.execute(f"UPDATE digest_subscriptions SET {cols} WHERE workspace = ? AND id = ?",
                     (*sets.values(), workspace, sub_id))
    return subscription(workspace, sub_id)


def delete(workspace, sub_id):
    with _db() as conn:
        return conn.execute("DELETE FROM digest_subscriptions WHERE workspace = ? AND id = ?",
                            (workspace, sub_id)).rowcount == 1


def by_token(token):
    """The subscription an unsubscribe link's token names, or None."""
    if not token or len(token) > 200:
        return None
    with _db() as conn:
        return _sub(conn.execute("SELECT * FROM digest_subscriptions WHERE unsubscribe_hash = ?",
                                 (_hash(token),)).fetchone())


def unsubscribe_token(sub):
    """The token for a subscription's unsubscribe link (sealed in the database)."""
    with _db() as conn:
        row = conn.execute("SELECT unsubscribe_token FROM digest_subscriptions WHERE id = ?",
                           (sub["id"],)).fetchone()
    return secret_box.unseal(row[0], sub["workspace"]) if row else None


def due(now=None, limit=500):
    """Enabled subscriptions whose digest is due."""
    now = time.time() if now is None else now
    with _db() as conn:
        return [_sub(r) for r in conn.execute(
            "SELECT * FROM digest_subscriptions WHERE enabled = 1 AND next_due_at <= ? "
            "ORDER BY next_due_at LIMIT ?", (now, limit))]


def mark_sent(sub, sent_at, error=None):
    """The digest went out (or couldn't, `error`): the next one is a period on."""
    with _db() as conn:
        conn.execute("UPDATE digest_subscriptions SET last_sent_at = ?, next_due_at = ?, "
                     "last_error = ? WHERE id = ?",
                     (sent_at if error is None else sub["last_sent_at"],
                      sent_at + FREQUENCIES[sub["frequency"]], error, sub["id"]))


# ---- doc_summaries --------------------------------------------------------------------
def summary(workspace, doc_key, version):
    with _db() as conn:
        row = conn.execute("SELECT summary FROM doc_summaries WHERE workspace = ? AND "
                           "doc_key = ? AND version = ?", (workspace, doc_key, version)).fetchone()
    return row[0] if row else None


def save_summary(workspace, doc_key, version, text, model=None):
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO doc_summaries (workspace, doc_key, version, "
                     "summary, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (workspace, doc_key, version, text, model, time.time()))
        # Older versions of the same document aren't needed again.
        conn.execute("DELETE FROM doc_summaries WHERE workspace = ? AND doc_key = ? AND "
                     "version != ?", (workspace, doc_key, version))
