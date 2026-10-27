"""
abuse_store.py  —  usage, spend caps and abuse alerts (config/app.db, tables made
by migrations/versions/0018_cache_quality_pii_abuse.py; platform.md PL-36, PL-02).

    usage_daily       questions, people, cached answers, tokens and cost per
                      workspace and day (kb/services/abuse.py rolls it up daily)
    workspace_limits  a workspace's daily spend caps, set by an operator; NULL: the
                      server's (SPEND_SOFT_USD_PER_DAY / SPEND_HARD_USD_PER_DAY)
    abuse_alerts      what the monitor found: one row per kind, workspace and
                      dedupe key (e.g. the day), so an alert is raised and mailed once

Only SQL lives here.
"""

import json
import threading
import time

import db

_lock = threading.Lock()


def _db():
    return db.connect()


# ---- usage_daily ----------------------------------------------------------------------------
def save_usage(workspace, day, questions, users, cached, tokens_in, tokens_out, cost_usd):
    with _lock, _db() as conn:
        conn.execute("""INSERT OR REPLACE INTO usage_daily (workspace, day, questions, users,
                            cached, tokens_in, tokens_out, cost_usd, computed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (workspace, day, questions, users, cached, tokens_in, tokens_out,
                      round(cost_usd, 6), time.time()))


def usage(workspace, since_day, until_day):
    """{day: row} in [since_day, until_day]."""
    with _db() as conn:
        return {r["day"]: dict(r) for r in conn.execute(
            "SELECT * FROM usage_daily WHERE workspace = ? AND day >= ? AND day <= ? "
            "ORDER BY day", (workspace, since_day, until_day))}


# ---- workspace_limits -----------------------------------------------------------------------
def limits(workspace):
    """{"spend_soft_usd", "spend_hard_usd"} as set for the workspace (None: not set)."""
    with _db() as conn:
        r = conn.execute("SELECT * FROM workspace_limits WHERE workspace = ?",
                         (workspace,)).fetchone()
    return {"spend_soft_usd": r["spend_soft_usd"] if r else None,
            "spend_hard_usd": r["spend_hard_usd"] if r else None}


def set_limits(workspace, soft, hard, by):
    with _lock, _db() as conn:
        conn.execute("""INSERT INTO workspace_limits (workspace, spend_soft_usd, spend_hard_usd,
                            updated_by, updated_at) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(workspace) DO UPDATE SET
                            spend_soft_usd = excluded.spend_soft_usd,
                            spend_hard_usd = excluded.spend_hard_usd,
                            updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                     (workspace, soft, hard, by, time.time()))


# ---- abuse_alerts ---------------------------------------------------------------------------
def _alert(r):
    a = dict(r)
    a["details"] = json.loads(a.get("details") or "{}")
    a["notified"] = json.loads(a.get("notified") or "[]")
    return a


def raise_alert(workspace, kind, dedupe, details, subject=None, now=None):
    """The new alert, or None when the same one (kind, dedupe) was raised already."""
    now = time.time() if now is None else now
    with _lock, _db() as conn:
        cur = conn.execute("""INSERT OR IGNORE INTO abuse_alerts (workspace, kind, dedupe,
                                  subject, details, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                           (workspace, kind, dedupe, subject, json.dumps(details, sort_keys=True),
                            now))
        if not cur.rowcount:
            return None
        return _alert(conn.execute("SELECT * FROM abuse_alerts WHERE id = ?",
                                   (cur.lastrowid,)).fetchone())


def set_notified(alert_id, who):
    with _lock, _db() as conn:
        conn.execute("UPDATE abuse_alerts SET notified = ? WHERE id = ?",
                     (json.dumps(sorted(who)), alert_id))


def alerts(limit=100, open_only=False, workspace=None):
    """The newest alerts first."""
    sql, args = "SELECT * FROM abuse_alerts WHERE 1 = 1", []
    if open_only:
        sql += " AND acknowledged_at IS NULL"
    if workspace:
        sql, args = sql + " AND workspace = ?", args + [workspace]
    with _db() as conn:
        return [_alert(r) for r in conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?",
                                                (*args, limit))]


def acknowledge(alert_id, by, now=None):
    """The alert, acknowledged; None when there's no such alert."""
    with _lock, _db() as conn:
        conn.execute("UPDATE abuse_alerts SET acknowledged_by = ?, acknowledged_at = ? "
                     "WHERE id = ? AND acknowledged_at IS NULL",
                     (by, time.time() if now is None else now, alert_id))
        r = conn.execute("SELECT * FROM abuse_alerts WHERE id = ?", (alert_id,)).fetchone()
    return _alert(r) if r else None
