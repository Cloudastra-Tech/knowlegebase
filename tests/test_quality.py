"""
The online feedback loop (pipeline.md P2-3, kb/services/quality.py): the quality
trend on the Insights screen (answer rate, thumbs, faithfulness flags, cached
answers, per day) and the weekly job that turns thumbs-down answers and
not-found clusters into test-set candidates (eval_candidates) for an admin.

Rows have fixed times, so the numbers are exact.
"""

import json
import uuid

import access
import conversations_store
import db
import insights_store
import question_log_store as log_store
from kb.routers import insights as router
from kb.services import conversations, quality
from test_conversations import client, fakes  # noqa: F401  (fixtures)
from test_insights import at, docs_root, fresh_default  # noqa: F401  (fixtures)


def new_ws():
    return f"q-{uuid.uuid4().hex[:8]}"


def answer_with_feedback(ws, rating, when, question="What is the refund policy?",
                         level="public", reasons=("outdated",)):
    """A saved answer, rated by its asker at `when`. Returns its message id."""
    user = f"pat@{ws}.example"
    conv = conversations_store.create(ws, user, "public")
    turn = conversations.start_turn(ws, user, access.for_role("public"), conv["id"], question)
    conversations_store.finish_message(turn["message_id"], "Thirty days.", "answered",
                                       max_access=level)
    conversations_store.set_feedback(turn["message_id"], user, ws, rating, list(reasons),
                                     "Now 14 days", snapshot={
                                         "question": question, "answer": "Thirty days.",
                                         "sources": [{"doc_key": "public/refund.md"}],
                                         "max_access": level})
    conn = db.connect()
    with conn:
        conn.execute("UPDATE message_feedback SET updated_at = ? WHERE message_id = ?",
                     (when, turn["message_id"]))
    conn.close()
    return turn["message_id"]


def test_the_quality_trend_is_exact_per_day():
    ws = new_ws()
    d1, d2 = "2026-10-20", "2026-10-21"
    for outcome, unsupported, cached in (("answered", 0, None), ("answered", 2, None),
                                         ("partial", None, "exact"), ("not_found", None, None)):
        log_store.add(ws, "u", "web", outcome, text_mode="metadata", at=at(d1),
                      unsupported=unsupported, cache_hit=cached)
    log_store.add(ws, "u", "web", "blocked", text_mode="metadata", at=at(d2))
    answer_with_feedback(ws, 1, at(d1))
    answer_with_feedback(ws, -1, at(d1, 13))
    answer_with_feedback(ws, -1, at(d2))
    trend = quality.trend(ws, d1, "2026-10-22")
    first, second, third = trend["daily"]
    assert first == {"day": d1, "questions": 4, "answered": 3, "answer_rate": 0.75,
                     "thumbs_up": 1, "thumbs_down": 1, "thumbs_up_share": 0.5,
                     "faithfulness_flags": 1, "cached": 1}
    assert (second["questions"], second["answer_rate"], second["thumbs_down"],
            second["thumbs_up_share"]) == (1, 0.0, 1, 0.0)
    assert third["answer_rate"] is None and third["thumbs_up_share"] is None   # nothing yet
    assert trend["totals"] == {"questions": 5, "answered": 3, "thumbs_up": 1,
                               "thumbs_down": 2, "faithfulness_flags": 1, "cached": 1,
                               "answer_rate": 0.6, "thumbs_up_share": 0.3333}


def test_the_insights_report_carries_the_trend(client, fresh_default, docs_root):  # noqa: F811
    from test_conversations import headers
    r = client.get("/api/admin/insights?from=2026-10-01&to=2026-10-03",
                   headers=headers(client, "hana"))
    assert r.status_code == 200
    assert [d["day"] for d in r.json()["quality"]["daily"]] == ["2026-10-01", "2026-10-02",
                                                                "2026-10-03"]


def test_the_weekly_job_suggests_thumbs_down_and_not_found_cases():
    ws = new_ws()
    now = at("2026-10-25")
    down = answer_with_feedback(ws, -1, now - 86400)
    answer_with_feedback(ws, 1, now - 86400, question="Support hours?")          # thumbs up
    answer_with_feedback(ws, -1, now - 9 * 86400, question="Old one?")           # last week's
    hr_only = answer_with_feedback(ws, -1, now - 3600, question="Pay bands?", level="restricted")
    first = log_store.add(ws, "u1", "web", "not_found", text_mode="redacted",
                          question="parental leave in Germany?", max_access="public",
                          at=now - 7200)
    insights_store.save_gaps(ws, now - 30 * 86400, [
        {"label": "Parental leave in Germany", "size": 4, "not_found": 4, "max_access": "public",
         "log_ids": [first]},
        {"label": "Too small", "size": 1, "not_found": 1, "max_access": "public",
         "log_ids": [first]}], now)

    assert quality.suggest(ws, now) == {"feedback": 2, "not_found": 1}
    cases = {c["message_id"]: c for c in insights_store.candidates(ws)}
    assert set(cases) == {down, hr_only, f"log:{first}"}
    assert cases[down]["origin"] == "feedback" and cases[down]["created_by"] == "system"
    assert cases[down]["reasons"] == ["outdated"] and cases[down]["role"] == "public"
    assert cases[hr_only]["role"] == "hr"            # stays with HR (visible_to)
    gap = cases[f"log:{first}"]
    assert gap["origin"] == "not_found" and gap["question"] == "parental leave in Germany?"
    assert router._golden(gap) == {"id": f"gap-{gap['id']}", "question": gap["question"],
                                   "role": "public", "expect": "not_found", "sources": [],
                                   "notes": "", "origin": "feedback"}
    # Run again: nothing new, nothing changed.
    assert quality.suggest(ws, now) == {"feedback": 0, "not_found": 0}


def test_an_admins_case_is_never_replaced_and_promoting_takes_a_suggestion_over():
    ws = new_ws()
    now = at("2026-10-25")
    mid = answer_with_feedback(ws, -1, now - 3600)
    insights_store.add_candidate(ws, mid, "What is the refund policy?", "x", "Within 14 days.",
                                 [], [], None, "public", "public", "ada")
    quality.suggest(ws, now)
    (case,) = insights_store.candidates(ws)
    assert (case["origin"], case["created_by"], case["expected_answer"]) == (
        "admin", "ada", "Within 14 days.")

    ws2 = new_ws()
    mid2 = answer_with_feedback(ws2, -1, now - 3600)
    quality.suggest(ws2, now)
    insights_store.add_candidate(ws2, mid2, "q", "a", "Fixed.", [], [], None, "public", "public",
                                 "ada")
    (case,) = insights_store.candidates(ws2)
    assert (case["origin"], case["created_by"]) == ("admin", "ada")


def test_no_not_found_case_without_question_text():
    ws = new_ws()
    now = at("2026-10-25")
    first = log_store.add(ws, "u1", "web", "not_found", text_mode="metadata", at=now - 60)
    insights_store.save_gaps(ws, now - 86400, [{"label": "x", "size": 5, "max_access": "public",
                                                "log_ids": [first]}], now)
    assert quality.suggest(ws, now) == {"feedback": 0, "not_found": 0}


def test_the_job_runs_for_every_workspace_that_logged_questions():
    ws = new_ws()
    now = at("2026-10-25")
    log_store.add(ws, "u", "web", "answered", text_mode="metadata", at=now - 60)
    answer_with_feedback(ws, -1, now - 60)
    assert quality.weekly(now=now)[ws] == {"feedback": 1, "not_found": 0}
    assert json.loads(json.dumps(insights_store.candidates(ws)))[0]["origin"] == "feedback"
