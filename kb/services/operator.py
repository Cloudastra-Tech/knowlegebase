"""
What the operator console shows of every workspace (platform.md PL-18), from
the tables the app already keeps: no content, only counts and states.

    plan            its plan and trial (kb/services/billing.py), whether or not
                    BILLING is on: None for a workspace billing doesn't apply to
    seats           members not turned off who were active in the last 30 days
                    (signed in, or asked: billing.seats)
    usage           questions, tokens and model cost in the last 30 days
    last_activity   the latest sign-in, question or change by a person
    connectors      how many, and the ones in error (provider and error code)
    index           the search index's status (kb/services/indexing.py)
    status          active / suspended / deleting, and support access
    spend           today's cost and the daily spend caps (kb/services/spend.py)
    open_alerts     the abuse and cost monitor's alerts not yet acknowledged
"""

import time

import billing_store
import db
import lifecycle_store
from kb.services import billing, indexing, spend

SEAT_DAYS = billing.SEAT_DAYS


def overview(now=None):
    now = now or time.time()
    since = now - SEAT_DAYS * 86400
    since_day = time.strftime("%Y-%m-%d", time.gmtime(since))
    conn = db.connect()
    try:
        rows = conn.execute("SELECT slug, name, created_at FROM workspaces ORDER BY slug").fetchall()

        def one(sql, *args):
            return conn.execute(sql, args).fetchone()[0]

        out = []
        for w in rows:
            ws = w["slug"]
            seats = billing.seats(ws, now)
            usage = conn.execute("SELECT COALESCE(SUM(questions), 0), COALESCE(SUM(tokens_in), 0) "
                                 "+ COALESCE(SUM(tokens_out), 0), COALESCE(SUM(cost_usd), 0) "
                                 "FROM insights_daily WHERE workspace = ? AND day >= ?",
                                 (ws, since_day)).fetchone()
            questions = one("SELECT COUNT(*) FROM question_log WHERE workspace = ? AND at >= ?",
                            ws, since)
            last = max(filter(None, (
                one("SELECT MAX(last_login_at) FROM members WHERE workspace = ?", ws),
                one("SELECT MAX(at) FROM question_log WHERE workspace = ?", ws),
                one("SELECT MAX(at) FROM audit_events WHERE workspace = ? AND actor_type "
                    "IN ('user', 'api_key')", ws))), default=None)
            connectors = conn.execute("SELECT provider, status, last_error FROM integrations "
                                      "WHERE workspace = ?", (ws,)).fetchall()
            st = lifecycle_store.state(ws)
            index = indexing.index_status(ws)
            soft, hard = spend.caps(ws)
            alerts = one("SELECT COUNT(*) FROM abuse_alerts WHERE workspace = ? AND "
                         "acknowledged_at IS NULL", ws)
            out.append({
                "slug": ws, "name": w["name"], "created_at": w["created_at"],
                "plan": _plan(ws, now),
                "members": one("SELECT COUNT(*) FROM members WHERE workspace = ?", ws),
                "seats": seats,
                "usage": {"questions": max(questions, usage[0]), "tokens": usage[1],
                          "cost_usd": round(usage[2], 4)},
                "last_activity": last,
                "connectors": len(connectors),
                "connector_errors": [{"provider": c["provider"], "error": c["last_error"]}
                                     for c in connectors
                                     if c["status"] == "error" or c["last_error"]],
                "index": {"status": index["status"], "message": index["message"],
                          "finished_at": index["finished_at"]},
                "spend": {"today_usd": spend.cost(ws, spend.day_of(now)), "soft_usd": soft,
                          "hard_usd": hard},
                "open_alerts": alerts,
                "status": st["status"], "suspended_reason": st["suspended_reason"],
                "deleting_at": st["deleting_at"], "support_until": st["support_until"]})
    finally:
        conn.close()
    return out


def _plan(ws, now):
    """{"id", "name", "status", "trial_ends_at", "trial_expired", "seats_paid", "owners"},
    or None when the workspace has no subscription."""
    sub = billing_store.subscription(ws)
    if not sub:
        return None
    plan = billing_store.plan(sub["plan"]) or {}
    ends = sub["trial_ends_at"]
    return {"id": sub["plan"], "name": plan.get("name") or sub["plan"], "status": sub["status"],
            "trial_ends_at": ends,
            "trial_expired": sub["status"] == "trialing" and ends is not None and ends <= now,
            "seats_paid": sub["seats"],
            "owners": [o["username"] for o in billing_store.owners(ws)]}
