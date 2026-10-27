"""
The online feedback loop (pipeline.md P2-3): what people say about answers, and
the answers' own checks, back into the test set and onto the Insights screen.

    trend(ws, since, until)   the QUALITY TREND per day: answer rate, thumbs up and
                              down (and the share up), faithfulness flags (answers
                              with a figure the check couldn't find in what they
                              cited) and answers served from the cache. Aggregates
                              only, like the rest of Insights.
    suggest(ws)               the WEEKLY JOB: the week's thumbs-down answers and the
                              latest not-found clusters (insights.compute_gaps)
                              become test-set candidates (eval_candidates, origin
                              feedback / not_found) for an admin to review on the
                              Feedback tab, beside the ones promoted by hand. One
                              already there is left alone.

A thumbs-down candidate copies the feedback's snapshot (question, answer,
sources), as promoting by hand does. A not-found candidate is the cluster's most
typical question as the question log kept it (its retention mode: none under
metadata, masked under redacted), expected to be answered "not found" until the
documents cover it.
"""

import calendar
import json
import logging
import time

import db
import insights_store
import question_log_store as log_store
from kb import jobs
from kb.services import insights

log = logging.getLogger("uvicorn.error")

WEEK = 7 * 24 * 60 * 60
MAX_PER_RUN = 50                 # candidates of each kind a run adds, at most


def _rate(n, total):
    return round(n / total, 4) if total else None


def trend(ws, since_day, until_day):
    """{"daily": [...], "totals": {...}} for [since_day, until_day] (see the top)."""
    conn = db.connect()
    try:
        asked = {r["day"]: r for r in conn.execute(
            "SELECT day, COUNT(*) AS questions, "
            "SUM(outcome IN ('answered', 'partial')) AS answered, "
            "SUM(COALESCE(unsupported, 0) > 0) AS flagged, "
            "SUM(cache_hit IS NOT NULL) AS cached FROM question_log "
            "WHERE workspace = ? AND day >= ? AND day <= ? GROUP BY day",
            (ws, since_day, until_day))}
        start = calendar.timegm(time.strptime(since_day, "%Y-%m-%d"))
        end = calendar.timegm(time.strptime(until_day, "%Y-%m-%d")) + 86400
        rated = {}
        # Counted per UTC day here rather than by SQLite's strftime(), so Postgres runs it too.
        for at, rating in conn.execute(
                "SELECT updated_at, rating FROM message_feedback WHERE workspace = ? AND "
                "updated_at >= ? AND updated_at < ?", (ws, start, end)):
            counts = rated.setdefault(time.strftime("%Y-%m-%d", time.gmtime(at)), {1: 0, -1: 0})
            counts[rating] = counts.get(rating, 0) + 1
    finally:
        conn.close()
    daily = []
    totals = {"questions": 0, "answered": 0, "thumbs_up": 0, "thumbs_down": 0,
              "faithfulness_flags": 0, "cached": 0}
    for day in insights.day_range(since_day, until_day):
        a = asked.get(day)
        q = a["questions"] if a else 0
        answered = (a["answered"] or 0) if a else 0
        up, down = (rated.get(day) or {}).get(1, 0), (rated.get(day) or {}).get(-1, 0)
        flagged = (a["flagged"] or 0) if a else 0
        cached = (a["cached"] or 0) if a else 0
        daily.append({"day": day, "questions": q, "answered": answered,
                      "answer_rate": _rate(answered, q), "thumbs_up": up, "thumbs_down": down,
                      "thumbs_up_share": _rate(up, up + down), "faithfulness_flags": flagged,
                      "cached": cached})
        for k, v in (("questions", q), ("answered", answered), ("thumbs_up", up),
                     ("thumbs_down", down), ("faithfulness_flags", flagged), ("cached", cached)):
            totals[k] += v
    totals["answer_rate"] = _rate(totals["answered"], totals["questions"])
    totals["thumbs_up_share"] = _rate(totals["thumbs_up"],
                                      totals["thumbs_up"] + totals["thumbs_down"])
    return {"daily": daily, "totals": totals}


def _role(max_access):
    return "public" if max_access == "public" else "hr"


def golden(case):
    """A test-set case (insights_store.candidates) as a line of eval/golden/*.jsonl
    has it (eval/harness.py reads both). A not-found question the weekly job
    suggested is expected to stay "not found" (no sources) until the documents
    cover it."""
    not_found = case.get("origin") == "not_found"
    return {"id": f"{'gap' if not_found else 'feedback'}-{case['id']}",
            "question": case["question"], "role": case["role"],
            "expect": "not_found" if not_found else "answer",
            "sources": [] if not_found else [s.get("doc_key") or s.get("title")
                                             for s in case["sources"]
                                             if s.get("kind") != "general_knowledge"],
            "notes": case["expected_answer"] or "", "origin": "feedback"}


def suggest(ws, now=None):
    """Add the week's candidates (see the top). Returns {"feedback": n, "not_found": n}."""
    now = time.time() if now is None else now
    added = {"feedback": 0, "not_found": 0}
    conn = db.connect()
    try:
        downs = conn.execute(
            "SELECT * FROM message_feedback WHERE workspace = ? AND rating = -1 AND "
            "updated_at >= ? ORDER BY updated_at DESC LIMIT ?",
            (ws, now - WEEK, MAX_PER_RUN)).fetchall()
    finally:
        conn.close()
    for f in downs:
        snap = json.loads(f["snapshot"] or "{}")
        if not snap.get("question"):
            continue
        level = snap.get("max_access") or "restricted"
        added["feedback"] += insights_store.suggest_candidate(
            ws, f["message_id"], snap["question"], snap.get("answer"), snap.get("sources") or [],
            json.loads(f["reasons"] or "[]"), f["comment"], _role(level), level, "feedback",
            now, acl=snap.get("acl"))
    gaps = [g for g in insights_store.gaps(ws) if g["size"] >= insights.GAP_MIN_SIZE]
    rows = log_store.get_many(g["log_ids"][0] for g in gaps if g["log_ids"])
    for g in gaps[:MAX_PER_RUN]:
        row = rows.get(g["log_ids"][0]) if g["log_ids"] else None
        if not row or not row.get("question"):
            continue                       # metadata mode, or its text has expired
        level = g["max_access"] if g["max_access"] == "restricted" else row["max_access"]
        label = g.get("label") or "Unanswered questions"
        added["not_found"] += insights_store.suggest_candidate(
            ws, row["message_id"] or f"log:{row['id']}", row["question"], row.get("answer"),
            [], ["not_found"], f"{label}: {g['size']} similar questions weren't answered.",
            _role(level), level, "not_found", now, acl=g.get("acl"))
    return added


@jobs.periodic("eval_candidates", lambda: WEEK)
def weekly(job=None, now=None):
    """Every workspace that logged questions: suggest its week's test-set candidates."""
    done = {}
    for ws in log_store.workspaces():
        try:
            done[ws] = suggest(ws, now)
        except Exception:
            log.exception("Quality: suggesting test cases failed for workspace %s", ws)
    return done
