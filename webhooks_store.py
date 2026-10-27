"""
webhooks_store.py  —  an admin's outbound webhooks and what was sent to them
(migration 0016; platform.md PL-20).

    webhooks             the URL, the events it wants, enabled or not, and its
                         signing secret, SEALED under the workspace's data key
                         (secret_box): shown once when it's made, then only its
                         last 4 characters
    webhook_deliveries   one row per event per endpoint: the body sent, its
                         status (pending, delivered, failed), attempts, when it's
                         tried next and the last response code. Kept KEEP_DAYS.
"""

import json
import secrets
import time

import db
import secret_box

secret_box.register("webhooks", "secret", ("id",))

KEEP_DAYS = 30
MAX_PER_WORKSPACE = 20
_PUBLIC = ("id", "workspace", "url", "description", "events", "secret_hint", "enabled",
           "created_by", "created_at", "updated_at")
_DELIVERY = ("id", "webhook_id", "event", "event_id", "status", "attempts", "next_at",
             "response_code", "error", "created_at", "last_attempt_at", "delivered_at")


def _db():
    return db.connect()


def _hook(row):
    if not row:
        return None
    d = {k: row[k] for k in _PUBLIC}
    d["events"] = json.loads(d["events"] or "[]")
    d["enabled"] = bool(d["enabled"])
    return d


def new_secret():
    return "whsec_" + secrets.token_urlsafe(32)


def create(workspace, url, events, description, created_by, secret, now=None):
    now = time.time() if now is None else now
    hook_id = secrets.token_hex(8)
    with _db() as conn:
        conn.execute("""INSERT INTO webhooks (id, workspace, url, description, events, secret,
                            secret_hint, enabled, created_by, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                     (hook_id, workspace, url, description, json.dumps(sorted(set(events))),
                      secret_box.seal(secret, workspace), secret[-4:], created_by, now, now))
    return get(workspace, hook_id)


def get(workspace, hook_id):
    with _db() as conn:
        return _hook(conn.execute("SELECT * FROM webhooks WHERE workspace = ? AND id = ?",
                                  (workspace, hook_id)).fetchone())


def list_hooks(workspace):
    with _db() as conn:
        return [_hook(r) for r in conn.execute(
            "SELECT * FROM webhooks WHERE workspace = ? ORDER BY created_at", (workspace,))]


def count(workspace):
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM webhooks WHERE workspace = ?",
                            (workspace,)).fetchone()[0]


def subscribed(workspace, event):
    """The workspace's enabled webhooks that want `event`."""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM webhooks WHERE workspace = ? AND enabled = 1",
                            (workspace,)).fetchall()
    return [h for h in map(_hook, rows) if event in h["events"]]


def secret(workspace, hook_id):
    with _db() as conn:
        row = conn.execute("SELECT secret FROM webhooks WHERE workspace = ? AND id = ?",
                           (workspace, hook_id)).fetchone()
    return secret_box.unseal(row[0], workspace) if row else None


def update(workspace, hook_id, **fields):
    if "events" in fields:
        fields["events"] = json.dumps(sorted(set(fields["events"])))
    if "enabled" in fields:
        fields["enabled"] = int(bool(fields["enabled"]))
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _db() as conn:
        conn.execute(f"UPDATE webhooks SET {cols} WHERE workspace = ? AND id = ?",
                     (*fields.values(), workspace, hook_id))
    return get(workspace, hook_id)


def delete(workspace, hook_id):
    with _db() as conn:
        conn.execute("DELETE FROM webhook_deliveries WHERE workspace = ? AND webhook_id = ?",
                     (workspace, hook_id))
        return conn.execute("DELETE FROM webhooks WHERE workspace = ? AND id = ?",
                            (workspace, hook_id)).rowcount == 1


# ---- Deliveries --------------------------------------------------------------------------
def _delivery(row):
    return {k: row[k] for k in _DELIVERY} if row else None


def add_delivery(workspace, hook_id, event, event_id, payload, now=None):
    now = time.time() if now is None else now
    delivery_id = secrets.token_hex(12)
    with _db() as conn:
        conn.execute("""INSERT INTO webhook_deliveries (id, webhook_id, workspace, event,
                            event_id, payload, status, next_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                     (delivery_id, hook_id, workspace, event, event_id, payload, now, now))
    return delivery_id


def delivery(workspace, delivery_id):
    """A delivery with its payload."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM webhook_deliveries WHERE workspace = ? AND id = ?",
                           (workspace, delivery_id)).fetchone()
    return {**_delivery(row), "payload": row["payload"]} if row else None


def deliveries(workspace, hook_id, limit=50):
    with _db() as conn:
        return [_delivery(r) for r in conn.execute(
            "SELECT * FROM webhook_deliveries WHERE workspace = ? AND webhook_id = ? "
            "ORDER BY created_at DESC LIMIT ?", (workspace, hook_id, limit))]


def due(workspace, now=None, limit=100):
    """Pending deliveries of the workspace whose time has come, oldest first."""
    now = time.time() if now is None else now
    with _db() as conn:
        rows = conn.execute("SELECT * FROM webhook_deliveries WHERE workspace = ? AND "
                            "status = 'pending' AND next_at <= ? ORDER BY next_at LIMIT ?",
                            (workspace, now, limit)).fetchall()
    return [{**_delivery(r), "payload": r["payload"]} for r in rows]


def next_due(workspace):
    """When the workspace's next pending delivery is due, or None."""
    with _db() as conn:
        return conn.execute("SELECT MIN(next_at) FROM webhook_deliveries WHERE workspace = ? "
                            "AND status = 'pending'", (workspace,)).fetchone()[0]


def attempted(delivery_id, status, response_code=None, error=None, next_at=None, now=None):
    now = time.time() if now is None else now
    with _db() as conn:
        conn.execute("""UPDATE webhook_deliveries SET status = ?, attempts = attempts + 1,
                            response_code = ?, error = ?, next_at = ?, last_attempt_at = ?,
                            delivered_at = CASE WHEN ? = 'delivered' THEN ? END
                        WHERE id = ?""",
                     (status, response_code, (error or "")[:300] or None, next_at, now, status,
                      now, delivery_id))


def fail_pending(workspace, hook_id, error):
    """A disabled webhook: what was waiting for it isn't sent."""
    with _db() as conn:
        conn.execute("UPDATE webhook_deliveries SET status = 'failed', error = ?, next_at = NULL "
                     "WHERE workspace = ? AND webhook_id = ? AND status = 'pending'",
                     (error, workspace, hook_id))


def prune(now=None, days=KEEP_DAYS):
    now = time.time() if now is None else now
    with _db() as conn:
        return conn.execute("DELETE FROM webhook_deliveries WHERE status != 'pending' AND "
                            "created_at < ?", (now - days * 86400,)).rowcount


def prune_event(workspace, event, before):
    """Delete a workspace's deliveries of one event made before `before` (any status)."""
    with _db() as conn:
        return conn.execute("DELETE FROM webhook_deliveries WHERE workspace = ? AND event = ? "
                            "AND created_at < ?", (workspace, event, before)).rowcount


def workspaces_with(event):
    with _db() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT workspace FROM webhook_deliveries WHERE event = ?", (event,))]
