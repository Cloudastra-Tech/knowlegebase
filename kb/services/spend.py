"""
Daily spend caps per workspace (platform.md PL-02's remainder, PL-36): what a
workspace's questions cost today (UTC), from the question log's tokens priced as
Insights prices them (insights.cost_of), against two caps:

    soft   SPEND_SOFT_USD_PER_DAY, or the workspace's own (an operator sets it in
           the console): the first question past it emails the workspace's admins
           and the operators, once a day; questions go on
    hard   SPEND_HARD_USD_PER_DAY, or the workspace's own: past it, every surface
           refuses questions until midnight UTC with a 429 and a plain sentence
           saying so (kb/rate_limit.user_limit calls enforce() for every question)

No cap is set by default: a server sets its own in the environment.
"""

import logging
import os
import threading
import time

from fastapi import HTTPException

import abuse_store
import db

log = logging.getLogger("uvicorn.error")


def _env_usd(name):
    try:
        value = float(os.getenv(name, "") or 0)
    except ValueError:
        return None
    return value if value > 0 else None


def caps(ws):
    """(soft, hard) in USD per day; None where there's no cap."""
    own = abuse_store.limits(ws)
    soft = own["spend_soft_usd"] if own["spend_soft_usd"] is not None else \
        _env_usd("SPEND_SOFT_USD_PER_DAY")
    hard = own["spend_hard_usd"] if own["spend_hard_usd"] is not None else \
        _env_usd("SPEND_HARD_USD_PER_DAY")
    return (soft or None), (hard or None)


def day_of(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def cost(ws, day):
    """What the workspace's questions cost on `day` (USD), by the tokens logged."""
    from kb.services.insights import cost_of
    conn = db.connect()
    try:
        rows = conn.execute("SELECT model, SUM(tokens_in), SUM(tokens_out) FROM question_log "
                            "WHERE workspace = ? AND day = ? GROUP BY model",
                            (ws, day)).fetchall()
    finally:
        conn.close()
    return round(sum(cost_of(r[0], r[1], r[2]) for r in rows), 6)


def until_midnight(now):
    return max(1, int(86400 - now % 86400))


def status(ws, now=None):
    """{"day", "cost_usd", "soft_usd", "hard_usd", "over_soft", "over_hard"}."""
    now = time.time() if now is None else now
    soft, hard = caps(ws)
    spent = cost(ws, day_of(now)) if (soft or hard) else 0.0
    return {"day": day_of(now), "cost_usd": spent, "soft_usd": soft, "hard_usd": hard,
            "over_soft": bool(soft and spent >= soft), "over_hard": bool(hard and spent >= hard)}


def _alert(ws, kind, s, now):
    """Raise the day's alert once, and email it (in the background)."""
    alert = abuse_store.raise_alert(ws, kind, s["day"], {
        "cost_usd": s["cost_usd"], "cap_usd": s["soft_usd" if kind == "spend_soft" else "hard_usd"]},
        now=now)
    if alert:
        from kb.services import abuse
        threading.Thread(target=abuse.notify, args=(alert,), kwargs={"tenant": True},
                         daemon=True).start()


def enforce(ws, now=None):
    """Raise a 429 when the workspace is past its hard cap today; raise the soft
    cap's alert the first time it's passed. Errors reading the caps never refuse
    a question (they're logged)."""
    now = time.time() if now is None else now
    try:
        s = status(ws, now)
        if s["over_soft"]:
            _alert(ws, "spend_soft", s, now)
        if s["over_hard"]:
            _alert(ws, "spend_hard", s, now)
    except Exception:
        log.exception("Spend caps: couldn't check workspace %s; not limiting it", ws)
        return
    if s["over_hard"]:
        wait = until_midnight(now)
        hours, minutes = divmod(wait // 60, 60)
        raise HTTPException(
            429, f"Your workspace has used today's budget for the assistant "
                 f"(${s['cost_usd']:.2f} of ${s['hard_usd']:.2f}). Questions start again at "
                 f"midnight UTC, in {hours} h {minutes} min. Your admin can ask for a higher "
                 "limit.", headers={"Retry-After": str(wait)})
