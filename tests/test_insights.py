"""
The question log and admin insights (platform.md PL-11 part 2, PL-14; chat.md C17):
every question is logged with its outcome, timings and tokens, its text kept only
as the workspace's retention mode says (applied when it's written); rollups,
never-cited and stale documents, content-gap clustering, the privacy rules on
what an admin sees, promoting feedback to the test set, and the CSV export.

As in test_conversations.py, OpenAI and the search index are the only things
faked; the rollups run on rows with fixed times, so their numbers are exact.
"""

import calendar
import csv
import io
import os
import uuid

import numpy as np
import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

import agent
import audit_store
import db
import insights_store
import question_log_store as log_store
from kb.services import insights, question_log
from test_chat import SALARY, FakeAgentModel
from test_conversations import ask, client, fakes, headers, new_chat  # noqa: F401

EMAIL_QUESTION = "What is the refund policy? Reply to pat.doe@example.com or 9876543210"


def at(day, hour=12):
    """Unix seconds of a UTC day and hour."""
    return calendar.timegm(tuple(map(int, day.split("-"))) + (hour, 0, 0))


def logged(ws="default"):
    return log_store.rows(ws)


@pytest.fixture
def fresh_default():
    """The default workspace's log and settings as new (the API tests ask there)."""
    conn = db.connect()
    with conn:
        for table in ("question_log", "insights_daily", "source_stats", "insights_docs",
                      "content_gaps", "eval_candidates"):
            conn.execute(f"DELETE FROM {table} WHERE workspace = 'default'")
    conn.close()
    log_store.set_settings("default", log_store.DEFAULT_MODE, log_store.DEFAULT_DAYS)
    yield


@pytest.fixture
def docs_root(monkeypatch, tmp_path):
    """Each workspace's documents in a temporary folder."""
    import workspaces
    monkeypatch.setattr(workspaces, "docs_root", lambda ws: str(tmp_path / ws))
    return lambda ws: tmp_path / ws


def put_doc(root, access, name, days_old, now):
    path = root / access / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("text")
    os.utime(path, (now - days_old * 86400, now - days_old * 86400))


def new_ws():
    return f"ins-{uuid.uuid4().hex[:8]}"


# ---- The log: one row per question, any surface ---------------------------------------------
def test_a_saved_question_is_logged_with_its_outcome_tools_and_sources(client, fresh_default):
    cid = new_chat(client, "pat")
    events = ask(client, "pat", "What is the refund policy?", conversation_id=cid)
    done = events[-1]

    (row,) = logged()
    assert (row["user"], row["surface"], row["outcome"], row["role"]) == (
        "pat", "web", "answered", "public")
    assert row["message_id"] == done["message_id"]
    assert row["question"] == "What is the refund policy?"
    assert "thirty days" in row["answer"]
    assert row["tools"] == ["search_docs"]
    assert [s["title"] for s in row["sources"]] == ["refund.md"]
    assert row["max_access"] == "public" and row["latency_ms"] >= 0
    assert row["text_mode"] == "redacted"


def test_the_api_and_one_off_questions_are_logged_too(client, fresh_default):
    r = client.post("/api/chat", json={"question": "How many leave days?"},
                    headers=headers(client, "sam"))
    assert r.status_code == 200
    ask(client, "sam", "What is the refund policy?")             # streamed, no conversation
    assert [(r["surface"], r["message_id"]) for r in logged()] == [("api", None), ("web", None)]


def test_a_refused_question_is_logged_with_why(client, fresh_default):
    ask(client, "pat", SALARY, role="public")
    (row,) = logged()
    assert (row["outcome"], row["block_reason"], row["sources"]) == (
        "blocked", "restricted_topic", [])


def test_a_failed_question_is_logged_as_an_error(client, fresh_default, monkeypatch):
    from kb.services import agents

    def broken(question, **kwargs):
        raise RuntimeError("OpenAI is down")
    monkeypatch.setattr(agents, "get_agent", lambda ws, role: broken)
    r = client.post("/api/chat", json={"question": "Anything?"}, headers=headers(client, "pat"))
    assert r.status_code == 502
    (row,) = logged()
    assert row["outcome"] == "error" and row["answer"] is None


class UsageModel(FakeAgentModel):
    """The agent's model, reporting its token usage at the end of the stream as
    OpenAI does."""

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        yield from super()._stream(messages, stop, run_manager, **kwargs)
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", usage_metadata={"input_tokens": 120, "output_tokens": 30,
                                        "total_tokens": 150},
            response_metadata={"model_name": "gpt-4o-mini"}))


def test_tokens_and_the_model_come_from_the_model_calls(client, fakes, fresh_default,
                                                        monkeypatch):
    from kb import state
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: UsageModel(fakes=fakes))
    state.agents.clear()
    ask(client, "pat", "What is the refund policy?")
    (row,) = logged()
    assert (row["tokens_in"], row["tokens_out"], row["model"]) == (120, 30, "gpt-4o-mini")


def test_counting_only_counts_inside_its_context():
    from langchain_core.outputs import ChatGeneration, LLMResult
    from langchain_core.messages import AIMessage
    usage = {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content="x", usage_metadata=usage, response_metadata={"model_name": "m"}))]])
    with question_log.counting() as counter:
        counter.on_llm_end(result)
    assert (counter.tokens_in, counter.tokens_out, counter.model) == (5, 2, "m")
    assert question_log._counter.get() is None


# ---- Retention modes: applied when the row is written --------------------------------------
@pytest.mark.parametrize("mode,question", [
    ("full", EMAIL_QUESTION),
    ("redacted", "What is the refund policy? Reply to [REDACTED_EMAIL] or [REDACTED_PHONE]"),
    ("metadata", None),
])
def test_the_text_logged_follows_the_workspace_mode(client, fresh_default, mode, question):
    r = client.put("/api/admin/question-log/settings", json={"mode": mode, "days": 90},
                   headers=headers(client, "ada"))
    assert r.status_code == 200 and r.json()["mode"] == mode
    client.post("/api/chat", json={"question": EMAIL_QUESTION}, headers=headers(client, "pat"))
    (row,) = logged()
    assert (row["text_mode"], row["question"]) == (mode, question)
    assert (row["answer"] is None) == (mode == "metadata")
    assert row["outcome"] == "answered" and row["sources"]       # metadata is kept in any mode


def test_everyone_can_read_how_their_questions_are_logged(client, fresh_default):
    r = client.get("/api/question-log/settings", headers=headers(client, "pat", "public"))
    assert r.json() == {"mode": "redacted", "days": 90, "logged": True}
    client.put("/api/admin/question-log/settings", json={"mode": "metadata", "days": 30},
               headers=headers(client, "ada"))
    r = client.get("/api/question-log/settings", headers=headers(client, "pat", "public"))
    assert r.json() == {"mode": "metadata", "days": 30, "logged": False}


def test_only_admins_change_the_logging_and_only_to_known_values(client, fresh_default):
    body = {"mode": "full", "days": 90}
    for who, role in (("hana", "hr"), ("pat", "public"), ("ada", "public")):
        r = client.put("/api/admin/question-log/settings", json=body,
                       headers=headers(client, who, role))
        assert r.status_code == 403, who
    for bad in ({"mode": "everything", "days": 90}, {"mode": "full", "days": 7}):
        r = client.put("/api/admin/question-log/settings", json=bad,
                       headers=headers(client, "ada"))
        assert r.status_code == 400
    assert log_store.settings("default")["mode"] == "redacted"


def test_a_stricter_mode_applies_to_what_was_logged_before():
    ws = new_ws()
    log_store.set_settings(ws, "full", 90)
    question_log.record(ws, "pat", "public", "web", EMAIL_QUESTION, "answered", answer="ok")
    question_log.change_settings(ws, "redacted", 90)
    (row,) = logged(ws)
    assert "pat.doe@example.com" not in row["question"] and row["text_mode"] == "redacted"
    question_log.change_settings(ws, "metadata", 90)
    (row,) = logged(ws)
    assert (row["question"], row["answer"], row["text_mode"]) == (None, None, "metadata")


def test_deleting_a_chat_clears_its_words_from_the_log(client, fresh_default):
    cid = new_chat(client, "pat")
    ask(client, "pat", "What is the refund policy?", conversation_id=cid)
    assert client.delete(f"/api/conversations/{cid}",
                         headers=headers(client, "pat")).status_code == 200
    (row,) = logged()
    assert (row["question"], row["answer"], row["message_id"]) == (None, None, None)
    assert row["outcome"] == "answered"                     # still counted


def test_the_nightly_job_clears_old_text_and_deletes_very_old_rows():
    ws = new_ws()
    now = at("2026-10-22")
    log_store.set_settings(ws, "full", 30)
    for days_ago in (5, 40, 500):
        log_store.add(ws, "pat", "web", "answered", text_mode="full", question="q",
                      answer="a", at=now - days_ago * 86400)
    insights.nightly(now=now)
    assert [(r["question"], r["answer"]) for r in logged(ws)] == [(None, None), ("q", "a")]


# ---- Rollups -------------------------------------------------------------------------------
HANDBOOK = {"title": "handbook.txt", "provider": "Company documents"}


def seed(ws):
    """Six questions over two days, and one today."""
    rows = [
        ("2026-10-20", 9, "ann", "answered", 1000, 1000, 100, [HANDBOOK]),
        ("2026-10-20", 10, "bob", "partial", 2000, 0, 0, [HANDBOOK]),
        ("2026-10-20", 11, "ann", "not_found", 3000, 0, 0, []),
        ("2026-10-20", 12, "cat", "blocked", 100, 0, 0, []),
        ("2026-10-21", 9, "dan", "general_knowledge", 4000, 2000, 400,
         [{"title": "Leave", "provider": "Wikipedia", "kind": "general_knowledge"}]),
        ("2026-10-21", 10, "ann", "error", 500, 0, 0, []),
        ("2026-10-22", 8, "eve", "answered", 900, 0, 0, [HANDBOOK]),
    ]
    for day, hour, user, outcome, ms, tin, tout, sources in rows:
        log_store.add(ws, user, "web", outcome, text_mode="redacted", question=f"{user}?",
                      latency_ms=ms, tokens_in=tin, tokens_out=tout, model="gpt-4o-mini",
                      sources=sources, max_access="public", at=at(day, hour))


def test_the_nightly_rollup_is_exact_and_leaves_today_alone(docs_root):
    ws = new_ws()
    now = at("2026-10-22", 10)
    put_doc(docs_root(ws), "public", "handbook.txt", 10, now)
    seed(ws)
    assert insights.rollup(ws, now) == ["2026-10-20", "2026-10-21"]

    days = insights_store.days(ws, "2026-10-01", "2026-10-31")
    assert set(days) == {"2026-10-20", "2026-10-21"}
    first = days["2026-10-20"]
    assert {k: first[k] for k in ("questions", "active_users", "answered", "partial",
                                  "not_found", "blocked", "tokens_in", "tokens_out")} == {
        "questions": 4, "active_users": 3, "answered": 1, "partial": 1, "not_found": 1,
        "blocked": 1, "tokens_in": 1000, "tokens_out": 100}
    assert first["cost_usd"] == pytest.approx(0.00021)
    assert insights_store.cited(ws, "2026-10-20", "2026-10-21") == [
        {"doc_key": "public/handbook.txt", "title": "handbook.txt",
         "provider": "Company documents", "url": None, "access": "public", "cited": 2}]
    assert insights.rollup(ws, now) == ["2026-10-20", "2026-10-21"]   # again: the same
    assert insights_store.days(ws, "2026-10-01", "2026-10-31")["2026-10-20"]["questions"] == 4


def test_the_report_adds_today_and_works_out_rates_latency_and_cost(docs_root):
    ws = new_ws()
    now = at("2026-10-22", 10)
    put_doc(docs_root(ws), "public", "handbook.txt", 10, now)
    seed(ws)
    insights.rollup(ws, now)
    report = insights.report(ws, "hr", "2026-10-20", "2026-10-22", now)

    t = report["totals"]
    assert (t["questions"], t["active_users"]) == (7, 5)
    assert t["rates"] == {"answered": round(3 / 7, 4), "not_found": round(1 / 7, 4),
                          "blocked": round(1 / 7, 4), "general_knowledge": round(1 / 7, 4)}
    # Latencies 100, 1000, 2000, 3000, 4000 and today's 900 (errors left out).
    assert (t["latency_p50_ms"], t["latency_p95_ms"]) == (1000, 3700)
    assert t["cost_usd"] == pytest.approx(0.00021 + (2000 * 0.15 + 400 * 0.6) / 1e6)
    assert [(d["day"], d["questions"], d["answered"]) for d in report["daily"]] == [
        ("2026-10-20", 4, 1), ("2026-10-21", 2, 0), ("2026-10-22", 1, 1)]
    assert [(s["title"], s["cited"]) for s in report["top_sources"]] == [("handbook.txt", 3)]
    assert "2026-10-22" not in insights_store.days(ws, "2026-10-22", "2026-10-22")


def test_a_day_the_job_missed_is_rolled_up_when_asked_for(docs_root):
    ws = new_ws()
    now = at("2026-10-22", 10)
    seed(ws)
    report = insights.report(ws, "hr", "2026-10-20", "2026-10-21", now)
    assert report["totals"]["questions"] == 6
    assert set(insights_store.days(ws, "2026-10-20", "2026-10-21")) == {"2026-10-20",
                                                                         "2026-10-21"}


def test_percentiles_interpolate_inside_a_bucket():
    hist = insights.latency_hist([100, 1000, 2000, 3000, 4000])
    assert (insights.percentile(hist, 50), insights.percentile(hist, 95)) == (1750, 3750)
    assert insights.percentile(insights.latency_hist([]), 50) is None
    assert insights.percentile(insights.latency_hist([999999]), 50) == 120000


# ---- Never cited and stale -----------------------------------------------------------------
def test_never_cited_and_stale_documents(docs_root):
    ws = new_ws()
    now = at("2026-10-22", 10)
    root = docs_root(ws)
    put_doc(root, "public", "handbook.txt", 300, now)      # old, cited often: stale
    put_doc(root, "public", "old_faq.txt", 60, now)        # in for 60 days, never cited
    put_doc(root, "public", "new.txt", 2, now)             # too new to judge
    put_doc(root, "restricted", "salaries.txt", 90, now)   # never cited, HR only
    for hour in (8, 9, 10):
        log_store.add(ws, "ann", "web", "answered", text_mode="metadata", max_access="public",
                      sources=[{"doc_key": "public/handbook.txt", "title": "handbook.txt",
                                "provider": "Company documents"}],
                      at=at("2026-10-21", hour))

    hr = insights.report(ws, "hr", "2026-10-01", "2026-10-22", now)
    assert [d["doc_key"] for d in hr["never_cited"]] == ["restricted/salaries.txt",
                                                        "public/old_faq.txt"]
    assert [(d["doc_key"], d["cited"]) for d in hr["stale"]] == [("public/handbook.txt", 3)]

    public = insights.report(ws, "public", "2026-10-01", "2026-10-22", now)
    assert [d["doc_key"] for d in public["never_cited"]] == ["public/old_faq.txt"]

    # Cited twice only: not stale (the default threshold is three).
    other = new_ws()
    put_doc(docs_root(other), "public", "handbook.txt", 300, now)
    for hour in (8, 9):
        log_store.add(other, "ann", "web", "answered", text_mode="metadata",
                      max_access="public", sources=[HANDBOOK], at=at("2026-10-21", hour))
    assert insights.report(other, "hr", "2026-10-01", "2026-10-22", now)["stale"] == []


def test_a_document_seen_recently_waits_its_thirty_days(docs_root):
    ws = new_ws()
    now = at("2026-10-22", 10)
    put_doc(docs_root(ws), "public", "a.txt", 0, now)
    assert insights.report(ws, "hr", "2026-11-01", "2026-11-01",
                           now + 10 * 86400)["never_cited"] == []
    later = insights.report(ws, "hr", "2026-12-01", "2026-12-01", now + 40 * 86400)
    assert [d["doc_key"] for d in later["never_cited"]] == ["public/a.txt"]
    assert later["never_cited_count"] == 1


# ---- Content gaps --------------------------------------------------------------------------
def vec(*xs):
    return np.array(xs, dtype=np.float32)


def test_clustering_groups_similar_questions_on_fixed_embeddings():
    vectors = [vec(1, 0, 0), vec(0.95, 0.05, 0), vec(0.9, 0.1, 0),
               vec(0, 1, 0), vec(0.05, 0.98, 0), vec(0.1, 0.9, 0.05),
               vec(0, 0, 1)]
    assert insights.cluster(vectors, 0.75) == [[0, 1, 2], [3, 4, 5], [6]]
    assert insights.cluster(vectors, 0.0) == [[0, 1, 2, 3, 4, 5, 6]]
    assert insights.cluster([], 0.75) == []


GAP_QUESTIONS = {
    "parental leave in Germany?": vec(1, 0, 0),
    "how long is parental leave for German staff?": vec(0.95, 0.05, 0),
    "Germany paternity leave rules": vec(0.9, 0.1, 0),
    "who is the CEO of Acme Corp?": vec(0, 1, 0),
    "Acme Corp chief executive": vec(0.05, 0.98, 0),
    "name of Acme's CEO": vec(0.1, 0.9, 0.05),
    "what is the wifi password": vec(0, 0, 1),
}


@pytest.fixture
def fake_gaps(monkeypatch):
    calls = {"embed": [], "label": []}

    def embed(texts):
        calls["embed"].append(list(texts))
        return [GAP_QUESTIONS[t].tolist() for t in texts]

    def label(questions):
        calls["label"].append(list(questions))
        return '"Leave."' if any("leave" in q for q in questions) else "  CEO "
    monkeypatch.setattr(insights, "embed", embed)
    monkeypatch.setattr(insights, "label", label)
    return calls


def seed_gaps(ws, now, restricted=()):
    for i, (q, _) in enumerate(GAP_QUESTIONS.items()):
        log_store.add(ws, f"u{i}", "web", "not_found" if i % 2 else "general_knowledge",
                      text_mode="redacted", question=q,
                      max_access="restricted" if q in restricted else "public",
                      at=now - (i + 1) * 3600)
    log_store.add(ws, "u9", "web", "answered", text_mode="redacted", question="answered one",
                  max_access="public", at=now - 3600)


def test_content_gaps_are_clustered_labelled_and_small_ones_hidden(fake_gaps):
    ws = new_ws()
    now = at("2026-10-22", 10)
    seed_gaps(ws, now)
    clusters = insights.compute_gaps(ws, now)
    assert sorted((c["label"], c["size"]) for c in clusters) == [("CEO", 3), ("Leave", 3)]
    assert len(fake_gaps["label"]) == 2                   # one cheap call per shown cluster
    assert "answered one" not in sum(fake_gaps["embed"], [])   # answered: not a gap

    gaps = {g["label"]: g for g in insights.gaps_for(ws, "hr", "redacted")}
    assert set(gaps) == {"CEO", "Leave"}                  # the wifi question alone: hidden
    assert set(gaps["Leave"]["samples"]) == {"parental leave in Germany?",
                                             "how long is parental leave for German staff?",
                                             "Germany paternity leave rules"}
    assert (gaps["Leave"]["not_found"], gaps["Leave"]["general_knowledge"]) == (1, 2)

    insights.compute_gaps(ws, now)                        # embeddings are kept, not redone
    assert len(fake_gaps["embed"]) == 1


def test_no_gaps_without_question_text(fake_gaps):
    ws = new_ws()
    now = at("2026-10-22", 10)
    seed_gaps(ws, now)
    log_store.set_settings(ws, "metadata", 90)
    assert insights.compute_gaps(ws, now) == [] and fake_gaps["embed"] == []


def test_a_gap_from_restricted_answers_shows_only_its_size_to_others(fake_gaps):
    ws = new_ws()
    now = at("2026-10-22", 10)
    seed_gaps(ws, now, restricted={"Acme Corp chief executive"})
    insights.compute_gaps(ws, now)
    public = insights.gaps_for(ws, "public", "redacted")
    hidden = [g for g in public if g["hidden"]]
    assert len(public) == 2 and len(hidden) == 1
    assert (hidden[0]["label"], hidden[0]["samples"], hidden[0]["size"]) == (None, [], 3)
    assert [g["label"] for g in public if not g["hidden"]] == ["Leave"]
    hr = insights.gaps_for(ws, "hr", "redacted")
    assert all(not g["hidden"] and len(g["samples"]) == 3 for g in hr)
    # A workspace now logging metadata only shows no question text at all.
    assert all(g["samples"] == [] for g in insights.gaps_for(ws, "hr", "metadata"))


def test_a_cluster_label_is_cleaned_and_a_failed_label_is_left_empty(monkeypatch, fake_gaps):
    assert insights._clean_label('  "Parental   leave".  ') == "Parental leave"
    assert insights._clean_label("") is None

    def broken(questions):
        raise RuntimeError("no model")
    monkeypatch.setattr(insights, "label", broken)
    ws = new_ws()
    now = at("2026-10-22", 10)
    seed_gaps(ws, now)
    assert [c["label"] for c in insights.compute_gaps(ws, now)] == [None, None]


# ---- Privacy of sources --------------------------------------------------------------------
def test_restricted_sources_are_not_listed_for_roles_that_cant_see_them(docs_root):
    ws = new_ws()
    now = at("2026-10-22", 10)
    put_doc(docs_root(ws), "restricted", "salaries.txt", 10, now)
    put_doc(docs_root(ws), "public", "handbook.txt", 10, now)
    log_store.add(ws, "ann", "web", "answered", text_mode="metadata", max_access="restricted",
                  sources=[{"title": "salaries.txt", "provider": "Company documents"},
                           HANDBOOK], at=at("2026-10-21"))
    # A source no longer searchable takes the answer's access level.
    log_store.add(ws, "ann", "web", "answered", text_mode="metadata", max_access="restricted",
                  sources=[{"title": "Gone", "provider": "Outline wiki", "access": "public"}],
                  at=at("2026-10-21", 13))
    hr = insights.report(ws, "hr", "2026-10-21", "2026-10-21", now)
    assert {s["title"] for s in hr["top_sources"]} == {"salaries.txt", "handbook.txt", "Gone"}
    public = insights.report(ws, "public", "2026-10-21", "2026-10-21", now)
    assert {s["title"] for s in public["top_sources"]} == {"handbook.txt"}


# ---- The API, CSV export ---------------------------------------------------------------------
def test_insights_are_for_hr_and_admins_viewing_as_hr(client, fresh_default, docs_root):
    ask(client, "pat", "What is the refund policy?")
    for who, role, status in (("hana", "hr", 200), ("ada", "hr", 200), ("pat", "public", 403),
                              ("ada", "public", 403)):
        r = client.get("/api/admin/insights", headers=headers(client, who, role))
        assert r.status_code == status, who
    body = client.get("/api/admin/insights", headers=headers(client, "hana")).json()
    assert body["totals"]["questions"] == 1 and len(body["daily"]) == 30
    assert body["logging"] == {"mode": "redacted", "days": 90}
    assert "pat" not in str(body)                         # who asked is never shown


def test_bad_ranges_are_refused(client, fresh_default):
    h = headers(client, "hana")
    for params in ({"from": "2026-10-10", "to": "2026-10-01"},
                   {"from": "2024-01-01", "to": "2026-10-01"},
                   {"from": "2026-13-45", "to": "2026-10-01"},
                   {"from": "yesterday"}):
        assert client.get("/api/admin/insights", params=params, headers=h).status_code in (
            400, 422), params


def test_each_table_exports_as_csv_and_the_export_is_audited(client, fresh_default, docs_root):
    from kb.services import insights as service
    day = service.today()
    log_store.add("default", "pat", "web", "answered", text_mode="redacted", max_access="public",
                  sources=[{"title": "=HYPERLINK(\"http://x\")", "provider": "Notion"}],
                  latency_ms=800)
    h = headers(client, "hana")
    r = client.get("/api/admin/insights/export", params={"table": "sources"}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert f"insights-sources-default-" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["title", "provider", "access", "cited", "url", "doc_key"]
    assert rows[1][:4] == ["'=HYPERLINK(\"http://x\")", "Notion", "public", "1"]
    daily = list(csv.reader(io.StringIO(client.get(
        "/api/admin/insights/export", params={"table": "daily", "from": day, "to": day},
        headers=h).text)))
    assert daily[1][:3] == [day, "1", "1"]
    for table in ("never_cited", "stale", "gaps", "connectors"):
        assert client.get("/api/admin/insights/export", params={"table": table},
                          headers=h).status_code == 200
    assert client.get("/api/admin/insights/export", params={"table": "users"},
                      headers=h).status_code == 422
    actions = [e["action"] for e in audit_store.list_events("default", limit=20)[0]]
    assert actions[:6] == ["insights.export"] * 6


# ---- Feedback promoted to the test set (C17) ---------------------------------------------------
def rated(client, who="pat"):
    cid = new_chat(client, who)
    done = ask(client, who, "What is the refund policy?", conversation_id=cid)[-1]
    r = client.put(f"/api/messages/{done['message_id']}/feedback", headers=headers(client, who),
                   json={"rating": -1, "reasons": ["outdated"], "comment": "Now 14 days"})
    assert r.status_code == 200
    return cid, done["message_id"]


def test_an_admin_promotes_a_rated_answer_to_the_test_set(client, fresh_default):
    cid, mid = rated(client)
    r = client.post("/api/admin/eval-cases", headers=headers(client, "ada"),
                    json={"message_id": mid, "expected_answer": "Within 14 days."})
    assert r.status_code == 200, r.text
    case = r.json()
    assert (case["question"], case["expected_answer"], case["reasons"], case["role"]) == (
        "What is the refund policy?", "Within 14 days.", ["outdated"], "public")
    assert "thirty days" in case["answer"] and case["created_by"] == "ada"
    assert case["golden"] == {"id": f"feedback-{case['id']}",
                              "question": "What is the refund policy?", "role": "public",
                              "expect": "answer", "sources": ["refund.md"],
                              "notes": "Within 14 days.", "origin": "feedback"}

    # Promoting again updates it; the case outlives the chat (D2).
    client.post("/api/admin/eval-cases", headers=headers(client, "ada"),
                json={"message_id": mid, "expected_answer": "Within 15 days."})
    client.delete(f"/api/conversations/{cid}", headers=headers(client, "pat"))
    cases = client.get("/api/admin/eval-cases", headers=headers(client, "hana")).json()["cases"]
    assert [(c["id"], c["expected_answer"]) for c in cases] == [(case["id"], "Within 15 days.")]

    assert client.delete(f"/api/admin/eval-cases/{case['id']}",
                         headers=headers(client, "ada")).json() == {"ok": True}
    assert client.get("/api/admin/eval-cases",
                      headers=headers(client, "hana")).json()["cases"] == []


def test_only_admins_promote_and_only_answers_with_feedback(client, fresh_default):
    _, mid = rated(client)
    r = client.post("/api/admin/eval-cases", headers=headers(client, "hana"),
                    json={"message_id": mid})
    assert r.status_code == 403
    r = client.post("/api/admin/eval-cases", headers=headers(client, "ada"),
                    json={"message_id": "no-such-answer"})
    assert r.status_code == 404
    assert client.delete("/api/admin/eval-cases/999999",
                         headers=headers(client, "ada")).status_code == 404


def test_test_cases_built_from_restricted_answers_stay_with_hr(fresh_default):
    insights_store.add_candidate("default", "m1", "pay bands?", "…", None, [], [], None, "hr",
                                 "restricted", "ada")
    from kb.routers import insights as router
    from kb.services import conversations
    cases = insights_store.candidates("default")
    assert [c["message_id"] for c in cases
            if conversations.visible_to("public", {"author": "assistant", **c})] == []
    assert router._golden(cases[0])["role"] == "hr"
