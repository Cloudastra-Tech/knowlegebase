"""Admin insights (kb/services/insights.py), the question log's settings, and the
test-set candidates promoted from answer feedback.

HR and admins (viewing the app as HR) read insights and feedback; only admins
change the logging settings or the test set. Everyone signed in may read how
their questions are logged (the chat says so)."""

import csv
import io
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import insights_store
import question_log_store
from kb.audit import audited
from kb.deps import current_role, current_user, current_viewer, require_admin, require_hr, ws_of
from kb.services import conversations, insights, question_log

router = APIRouter()

DAY = r"^\d{4}-\d{2}-\d{2}$"
MAX_DAYS = 366
DEFAULT_DAYS = 30
EXPECTED_CHARS = 4000
FORMULA = ("=", "+", "-", "@", "\t", "\r")       # a spreadsheet would run these
TABLES = {
    "daily": ("day", "questions", "active_users", "answered", "partial", "not_found",
              "blocked", "general_knowledge", "error", "stopped"),
    "surfaces": ("surface", "questions", "answered"),
    "sources": ("title", "provider", "access", "cited", "url", "doc_key"),
    "never_cited": ("title", "provider", "access", "first_seen", "modified", "url", "doc_key"),
    "stale": ("title", "provider", "access", "modified", "cited", "url", "doc_key"),
    "gaps": ("label", "size", "not_found", "general_knowledge", "samples"),
    "connectors": ("name", "status", "last_status", "last_run_at", "last_success_at",
                   "last_error", "items", "skipped", "failures"),
}
REPORT_KEYS = {"sources": "top_sources"}
TIMES = ("first_seen", "modified", "last_run_at", "last_success_at")


class LogSettingsIn(BaseModel):
    mode: str
    days: int


class EvalCaseIn(BaseModel):
    message_id: str
    expected_answer: str | None = None


# ---- The question log's settings ------------------------------------------------------
@router.get("/api/question-log/settings")
def log_settings(user=Depends(current_user)):
    """How this workspace logs questions: {mode, days, logged} (logged: any text is kept)."""
    s = question_log_store.settings(ws_of(user))
    return {**s, "logged": s["mode"] != "metadata"}


@router.put("/api/admin/question-log/settings")
def set_log_settings(body: LogSettingsIn, user=Depends(require_admin),
                     audit=audited("question_log.settings")):
    if body.mode not in question_log_store.MODES:
        raise HTTPException(400, "Choose full, redacted or metadata.")
    if body.days not in question_log_store.DAY_CHOICES:
        raise HTTPException(400, "Keep question text for "
                            + ", ".join(map(str, question_log_store.DAY_CHOICES)) + " days.")
    ws = ws_of(user)
    s = question_log.change_settings(ws, body.mode, body.days)
    audit.record("workspace", ws, s, user=user)
    return {**s, "logged": s["mode"] != "metadata"}


# ---- Insights ---------------------------------------------------------------------------
def _range(since, until):
    until = until or insights.today()
    if not since:
        since = (insights.date.fromisoformat(until)
                 - insights.timedelta(days=DEFAULT_DAYS - 1)).isoformat()
    try:
        span = (insights.date.fromisoformat(until) - insights.date.fromisoformat(since)).days
    except ValueError:
        raise HTTPException(400, "Dates are YYYY-MM-DD.")
    if span < 0:
        raise HTTPException(400, "The range ends before it starts.")
    if span >= MAX_DAYS:
        raise HTTPException(400, f"Pick at most {MAX_DAYS} days.")
    return since, until


@router.get("/api/admin/insights")
def get_insights(since: str | None = Query(None, alias="from", pattern=DAY),
                 until: str | None = Query(None, alias="to", pattern=DAY),
                 user=Depends(require_hr), role=Depends(current_role)):
    """Usage, outcomes, latency, cost, sources, content gaps and connector health
    for the days from..to (UTC, inclusive; default the last 30). Aggregates only:
    sample questions follow the retention mode and the viewer's role."""
    since, until = _range(since, until)
    return insights.report(ws_of(user), role, since, until)


def _cell(value):
    if isinstance(value, list):
        value = " | ".join(map(str, value))
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA) else text


def _when(value):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value)) if value else None


@router.get("/api/admin/insights/export")
def export_insights(table: str = Query(..., pattern="^(" + "|".join(TABLES) + ")$"),
                    since: str | None = Query(None, alias="from", pattern=DAY),
                    until: str | None = Query(None, alias="to", pattern=DAY),
                    user=Depends(require_hr), role=Depends(current_role),
                    audit=audited("insights.export")):
    """One of the report's tables as CSV (a cell that would start a formula gets a
    leading ')."""
    since, until = _range(since, until)
    ws = ws_of(user)
    rows = insights.report(ws, role, since, until)[REPORT_KEYS.get(table, table)]
    audit.record("insights", table, {"from": since, "to": until}, user=user)
    columns = TABLES[table]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(_when(row.get(c)) if c in TIMES else row.get(c))
                         for c in columns])
    name = f"insights-{table}-{ws}-{since}-{until}.csv"
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---- Test-set candidates (chat.md C17) --------------------------------------------------
def _golden(case):
    """The case as a line of eval/golden/*.jsonl would have it."""
    return {"id": f"feedback-{case['id']}", "question": case["question"], "role": case["role"],
            "expect": "answer",
            "sources": [s.get("doc_key") or s.get("title") for s in case["sources"]
                        if s.get("kind") != "general_knowledge"],
            "notes": case["expected_answer"] or "", "origin": "feedback"}


def _case(c):
    return {**{k: c[k] for k in ("id", "message_id", "question", "answer", "expected_answer",
                                 "sources", "reasons", "comment", "role", "created_by",
                                 "created_at")}, "golden": _golden(c)}


@router.get("/api/admin/eval-cases")
def list_eval_cases(user=Depends(require_hr), role=Depends(current_role)):
    """Answers promoted to the test set, newest first, as the viewer's role may see them."""
    return {"cases": [_case(c) for c in insights_store.candidates(ws_of(user))
                      if conversations.visible_to(role, {"author": "assistant", **c})]}


@router.post("/api/admin/eval-cases")
def add_eval_case(body: EvalCaseIn, user=Depends(require_admin),
                  viewer=Depends(current_viewer), audit=audited("eval_case.create")):
    """Promote a rated answer to the test set, with what it should have said. The
    question, answer and sources are copied from the feedback, so the case
    outlives the chat (chat.md D2)."""
    ws = ws_of(user)
    feedback = insights_store.feedback_for(ws, body.message_id)
    snap = feedback["snapshot"] if feedback else None
    if not snap or not snap.get("question") or not conversations.visible_to(
            viewer, {"author": "assistant", **snap}):
        raise HTTPException(404, "There's no feedback on that answer.")
    expected = (body.expected_answer or "").strip()[:EXPECTED_CHARS] or None
    access = snap.get("max_access") or "restricted"
    case = insights_store.add_candidate(
        ws, body.message_id, snap["question"], snap.get("answer"), expected,
        snap.get("sources") or [], feedback["reasons"], feedback["comment"],
        "public" if access == "public" else "hr", access, user["username"])
    audit.record("eval_case", str(case["id"]), {"message_id": body.message_id}, user=user)
    return _case(case)


@router.delete("/api/admin/eval-cases/{case_id}")
def delete_eval_case(case_id: int, user=Depends(require_admin),
                     audit=audited("eval_case.delete")):
    if not insights_store.delete_candidate(ws_of(user), case_id):
        raise HTTPException(404, "That test case doesn't exist.")
    audit.record("eval_case", str(case_id), user=user)
    return {"ok": True}
