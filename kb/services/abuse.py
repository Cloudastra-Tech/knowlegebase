"""
Abuse and cost monitoring (platform.md PL-36): runaway usage and compromised
accounts caught early, from the tables the app already keeps.

    usage_daily (daily job)   per workspace and day: questions, people, cached
                              answers, tokens and cost (abuse_store.usage_daily)
    monitor (hourly job)      the checks below; each finding is an alert in the
                              operator console (kb/routers/operator.py), raised
                              once, and emailed to the operators

THE CHECKS (thresholds from the environment):

    questions_spike   a workspace's questions today exceed ABUSE_SPIKE_FACTOR (3)
                      times the median of its previous 7 days, and at least
                      ABUSE_SPIKE_MIN (50)
    user_burst        one member asked more than ABUSE_USER_PER_HOUR (120) in the
                      last hour
    failed_sign_ins   failed or refused sign-ins (sso_events) in the last hour reach
                      ABUSE_FAILED_SIGN_INS (20) and ABUSE_SPIKE_FACTOR times the
                      workspace's hourly average over the previous 7 days
    signups_per_ip    one address created ABUSE_SIGNUPS_PER_IP (3) workspaces in a
                      day (the audit log's workspace.create; a platform alert)
    spend_soft / spend_hard   a workspace passed a daily spend cap (spend.py)

WHO IS TOLD: ABUSE_ALERT_EMAILS (default: OPERATOR_EMAILS) for every alert. The
workspace's admins too for a spend cap, and for the others with
ABUSE_NOTIFY_TENANT_ADMINS=on (off by default: a burst can be a false alarm, and
naming a member to their admin is the operator's call). What the admins are told
is also an abuse.alert webhook event (kb/services/webhooks.py).
"""

import logging
import os
import statistics
import time

import abuse_store
import audit_store
import db
import mailer
import members_store
from kb import jobs
from kb.settings import ON, env_int

log = logging.getLogger("uvicorn.error")

PLATFORM = audit_store.PLATFORM
KINDS = {
    "questions_spike": "Questions spiked",
    "user_burst": "One member asked very many questions",
    "failed_sign_ins": "Failed sign-ins spiked",
    "signups_per_ip": "Many workspaces signed up from one address",
    "spend_soft": "Daily spend passed the soft limit",
    "spend_hard": "Daily spend reached the hard limit: questions are paused",
}
TENANT_ALWAYS = ("spend_soft", "spend_hard")


def _factor():
    try:
        return max(1.0, float(os.getenv("ABUSE_SPIKE_FACTOR", "") or 3))
    except ValueError:
        return 3.0


def thresholds():
    return {"spike_factor": _factor(), "spike_min": env_int("ABUSE_SPIKE_MIN", 50),
            "user_per_hour": env_int("ABUSE_USER_PER_HOUR", 120),
            "failed_sign_ins": env_int("ABUSE_FAILED_SIGN_INS", 20),
            "signups_per_ip": env_int("ABUSE_SIGNUPS_PER_IP", 3)}


def day_of(at):
    return time.strftime("%Y-%m-%d", time.gmtime(at))


def _workspaces(conn, since):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT workspace FROM question_log WHERE at >= ? UNION "
        "SELECT DISTINCT workspace FROM sso_events WHERE at >= ?", (since, since))]


# ---- Usage per day --------------------------------------------------------------------------
def rollup_usage(now=None, days_back=7):
    """usage_daily for every workspace with questions, for today and the days_back
    before it. Returns {workspace: days rolled up}."""
    from kb.services.insights import cost_of
    now = time.time() if now is None else now
    since = day_of(now - days_back * 86400)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT workspace, day, model, COUNT(*), COUNT(DISTINCT user), "
            "SUM(cache_hit IS NOT NULL), SUM(tokens_in), SUM(tokens_out) FROM question_log "
            "WHERE day >= ? GROUP BY workspace, day, model", (since,)).fetchall()
        users = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT workspace, day, COUNT(DISTINCT user) FROM question_log WHERE day >= ? "
            "GROUP BY workspace, day", (since,))}
    finally:
        conn.close()
    totals = {}
    for ws, day, model, n, _, cached, t_in, t_out in rows:
        t = totals.setdefault((ws, day), [0, 0, 0, 0, 0.0])
        t[0] += n
        t[1] += cached or 0
        t[2] += t_in or 0
        t[3] += t_out or 0
        t[4] += cost_of(model, t_in, t_out) or 0.0      # an unknown price counts as none
    done = {}
    for (ws, day), (n, cached, t_in, t_out, cost) in totals.items():
        abuse_store.save_usage(ws, day, n, users.get((ws, day), 0), cached, t_in, t_out, cost)
        done[ws] = done.get(ws, 0) + 1
    return done


# ---- The checks -----------------------------------------------------------------------------
def checks(now=None, limits=None):
    """Every finding now: [(workspace, kind, dedupe, subject, details)]. Pure reads."""
    now = time.time() if now is None else now
    t = limits or thresholds()
    today, hour = day_of(now), int(now // 3600)
    found = []
    conn = db.connect()
    try:
        for ws in _workspaces(conn, now - 8 * 86400):
            # Questions today against the median of the 7 days before.
            per_day = dict(conn.execute(
                "SELECT day, COUNT(*) FROM question_log WHERE workspace = ? AND day >= ? "
                "GROUP BY day", (ws, day_of(now - 7 * 86400))).fetchall())
            asked = per_day.get(today, 0)
            before = [per_day.get(day_of(now - d * 86400), 0) for d in range(1, 8)]
            median = statistics.median(before)
            if asked >= t["spike_min"] and asked > t["spike_factor"] * median:
                found.append((ws, "questions_spike", today, None,
                              {"questions_today": asked, "median_7_days": median,
                               "factor": t["spike_factor"]}))
            # One member, the last hour.
            for user, n in conn.execute(
                    "SELECT user, COUNT(*) FROM question_log WHERE workspace = ? AND at >= ? "
                    "GROUP BY user HAVING COUNT(*) > ?", (ws, now - 3600, t["user_per_hour"])):
                found.append((ws, "user_burst", f"{user}:{hour}", user,
                              {"questions_last_hour": n, "limit": t["user_per_hour"]}))
            # Failed sign-ins, the last hour against the hourly average of the week before.
            failed = conn.execute(
                "SELECT COUNT(*) FROM sso_events WHERE workspace = ? AND outcome IN "
                "('failed', 'refused') AND at >= ?", (ws, now - 3600)).fetchone()[0]
            week = conn.execute(
                "SELECT COUNT(*) FROM sso_events WHERE workspace = ? AND outcome IN "
                "('failed', 'refused') AND at >= ? AND at < ?",
                (ws, now - 3600 - 7 * 86400, now - 3600)).fetchone()[0]
            hourly = week / (7 * 24)
            if failed >= t["failed_sign_ins"] and failed > t["spike_factor"] * hourly:
                found.append((ws, "failed_sign_ins", str(hour), None,
                              {"failed_last_hour": failed, "hourly_average": round(hourly, 2)}))
        # Workspaces signed up from one address in the last day.
        # (Grouped here, not by SQLite's GROUP_CONCAT, so Postgres runs it too.)
        made = {}
        for ip, workspace in conn.execute(
                "SELECT ip, workspace FROM audit_events WHERE action = 'workspace.create' "
                "AND at >= ? AND ip IS NOT NULL", (now - 86400,)):
            made.setdefault(ip, []).append(workspace or "")
        for ip, created in sorted(made.items()):
            if len(created) >= t["signups_per_ip"]:
                found.append((PLATFORM, "signups_per_ip", f"{ip}:{today}", ip,
                              {"workspaces": sorted(set(created)), "count": len(created)}))
    finally:
        conn.close()
    return found


# ---- Telling people -------------------------------------------------------------------------
def operator_emails():
    raw = os.getenv("ABUSE_ALERT_EMAILS") or os.getenv("OPERATOR_EMAILS") or ""
    return sorted({e.strip().lower() for e in raw.split(",") if "@" in e})


def _summary(alert):
    d = alert["details"]
    kind = alert["kind"]
    if kind == "questions_spike":
        return (f"{d['questions_today']} questions today, against a median of "
                f"{d['median_7_days']:g} a day over the last week.")
    if kind == "user_burst":
        return f"{alert['subject']} asked {d['questions_last_hour']} questions in the last hour."
    if kind == "failed_sign_ins":
        return (f"{d['failed_last_hour']} failed or refused sign-ins in the last hour "
                f"(about {d['hourly_average']:g} an hour usually).")
    if kind == "signups_per_ip":
        return (f"{d['count']} workspaces were created from {alert['subject']} today: "
                f"{', '.join(d['workspaces'])}.")
    return f"Spent ${d['cost_usd']:.2f} today; the limit is ${d['cap_usd']:.2f}."


def notify(alert, tenant=None):
    """Email an alert to the operators and, for a spend cap or with
    ABUSE_NOTIFY_TENANT_ADMINS=on, the workspace's admins. Never raises."""
    if tenant is None:
        tenant = (alert["kind"] in TENANT_ALWAYS
                  or os.getenv("ABUSE_NOTIFY_TENANT_ADMINS", "off").strip().lower() in ON)
    ws = alert["workspace"]
    to = set(operator_emails())
    if tenant and ws != PLATFORM:
        to |= {a for a in members_store.enabled_admins(ws) if "@" in a}
        # ...and the workspace's own systems, when a webhook wants abuse.alert (#75).
        from kb.services import webhooks
        webhooks.abuse_alert(ws, alert)
    sent = []
    for email in sorted(to):
        try:
            mailer.send(email, "notice", title=f"{KINDS.get(alert['kind'], alert['kind'])}"
                                               f"{'' if ws == PLATFORM else f' in {ws}'}",
                        body=_summary(alert))
            sent.append(email)
        except Exception as e:
            log.warning("Abuse monitor: couldn't email an alert (%s)", type(e).__name__)
    try:
        abuse_store.set_notified(alert["id"], sent)
    except Exception:
        log.exception("Abuse monitor: couldn't note who was told")
    return sent


def monitor(now=None):
    """Run the checks and raise (and mail) each new finding. Returns the new alerts."""
    now = time.time() if now is None else now
    new = []
    for ws, kind, dedupe, subject, details in checks(now):
        alert = abuse_store.raise_alert(ws, kind, dedupe, details, subject, now)
        if alert:
            log.warning("Abuse monitor: %s in %s", kind, ws)
            notify(alert)
            new.append(alert)
    return new


def view(alert):
    """An alert as the operator console shows it."""
    return {**{k: alert[k] for k in ("id", "workspace", "kind", "subject", "details",
                                     "created_at", "notified", "acknowledged_by",
                                     "acknowledged_at")},
            "title": KINDS.get(alert["kind"], alert["kind"]), "summary": _summary(alert)}


# ---- Jobs -----------------------------------------------------------------------------------
@jobs.periodic("usage_daily", lambda: 24 * 60 * 60)
def daily(job=None, now=None):
    return rollup_usage(now)


@jobs.periodic("abuse_monitor", lambda: 60 * 60)
def hourly(job=None, now=None):
    return {"alerts": len(monitor(now))}
