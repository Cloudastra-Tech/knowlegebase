"""
What the operator console shows of every workspace (platform.md PL-18), from
the tables the app already keeps: no content, only counts and states.

    plan            a placeholder until billing (PL-16) exists
    seats           members active in the last 30 days (signed in, or asked)
    usage           questions, tokens and model cost in the last 30 days
    last_activity   the latest sign-in, question or change by a person
    connectors      how many, and the ones in error (provider and error code)
    index           the search index's status (kb/services/indexing.py)
    status          active / suspended / deleting, and support access
"""

import time

import db
import lifecycle_store
from kb.services import indexing

SEAT_DAYS = 30


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
            seats = one("""SELECT COUNT(*) FROM (
                               SELECT username FROM members WHERE workspace = ?
                                  AND last_login_at >= ? AND disabled = 0
                               UNION SELECT user FROM question_log WHERE workspace = ? AND at >= ?)""",
                        ws, since, ws, since)
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
            out.append({
                "slug": ws, "name": w["name"], "created_at": w["created_at"], "plan": None,
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
                "status": st["status"], "suspended_reason": st["suspended_reason"],
                "deleting_at": st["deleting_at"], "support_until": st["support_until"]})
    finally:
        conn.close()
    return out
