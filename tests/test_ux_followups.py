"""
The API behind the frontend follow-ups recorded during the roadmap:

  - the index status says WHY a run failed as a code (reason) every member may
    see, and only an admin gets the exception's text;
  - admins read their workspace's recent background jobs (GET /api/admin/jobs);
  - members a workspace's identity provider manages through SCIM are marked,
    and the changes SCIM would undo are refused;
  - Insights' Not found tab: unanswered questions, by retention mode and role,
    marked resolved or drafted as a document;
  - a conversation exported as markdown, and "Continue in my chats" from a link.
"""

import json
import time

import pytest

import audit_store
import db
import members_store
import question_log_store as log_store
import scim_store
from kb import jobs
from kb.services import indexing, insights
from test_chat_history import (BONUS_Q, REFUND_Q, hr_chat_with_a_restricted_and_a_public_answer,
                               open_link, share)
from test_conversations import ask, client, fakes, headers, new_chat, read  # noqa: F401
from test_drafts import ready, setup  # noqa: F401
from test_indexing import env  # noqa: F401


def events_of(ws, action):
    return [e for e in audit_store.list_events(ws, limit=200)[0] if e["action"] == action]


# ---- Why indexing failed -------------------------------------------------------------------
@pytest.mark.parametrize("error, reason", [
    ("OpenAIError: Missing credentials. Please pass an `api_key`, or set the "
     "`OPENAI_API_KEY` environment variable.", "no_ai_key"),
    ("ProviderError: Bedrock is selected with access keys, but none are saved.", "no_ai_key"),
    ("AuthenticationError: Error code: 401 - Incorrect API key provided", "ai_key_rejected"),
    ("RateLimitError: Error code: 429 - insufficient_quota", "ai_quota"),
    ("APIConnectionError: Connection error.", "ai_unreachable"),
    ("OSError: disk full", "unknown"),
    (None, None),
])
def test_an_index_error_has_a_reason_code(error, reason):
    assert indexing.reason_of(error) == reason


def fail_index(monkeypatch, w, error):
    """Run one index job for `w` that fails with `error` (no retries)."""
    def broken(ws, full=False, docs=None, progress=None):
        raise RuntimeError(error)
    monkeypatch.setattr(indexing, "index", broken)
    job_id = jobs.enqueue("index", w, {"full": True}, lane=indexing.lane(w), max_attempts=1)
    runner = jobs.Runner(handlers={"index": jobs.HANDLERS["index"]}, threads=0)
    while jobs.get(job_id)["status"] in ("queued", "running") and runner.run_once():
        pass
    assert jobs.get(job_id)["status"] == "failed"
    return job_id


def test_only_an_admin_sees_the_error_text_everyone_sees_why(env, monkeypatch,  # noqa: F811
                                                             jobs_paused):
    w = env.workspace()
    fail_index(monkeypatch, w, "Missing credentials. Please pass an `api_key`.")
    staff = env.client.get("/api/index/status", headers=env.login(w, "staff")).json()
    assert (staff["status"], staff["reason"], staff["error"]) == ("error", "no_ai_key", None)
    assert "credentials" not in staff["message"]
    admin = env.client.get("/api/index/status", headers=env.login(w)).json()
    assert admin["reason"] == "no_ai_key" and "Missing credentials" in admin["error"]
    # An admin viewing the app as a public user is a public user here too.
    as_public = env.client.get("/api/index/status",
                               headers={**env.login(w), "X-Role": "public"}).json()
    assert as_public["error"] is None


def test_a_retry_waiting_says_why_without_the_raw_text(env, jobs_paused):  # noqa: F811
    w = env.workspace()
    job_id = jobs.enqueue("index", w, {}, lane=indexing.lane(w))
    conn = db.connect()
    with conn:
        conn.execute("UPDATE jobs SET error = ? WHERE id = ?",
                     ("RateLimitError: Error code: 429 - insufficient_quota", job_id))
    conn.close()
    state = indexing.index_status(w)
    assert state["status"] == "indexing" and state["reason"] == "ai_quota"
    assert state["message"] == "Trying again soon"


# ---- The jobs view -------------------------------------------------------------------------
def test_admins_read_their_own_workspaces_jobs(env, monkeypatch, jobs_paused):  # noqa: F811
    w, other = env.workspace(), env.workspace()
    failed = fail_index(monkeypatch, w, "APIConnectionError: Connection error.")
    jobs.enqueue("export", other, {"export_id": "secret-export"}, lane=f"x:{other}")
    jobs.enqueue("sync", w, {"provider": "notion", "cursor": "abc"}, lane=f"sync:{w}:notion")

    r = env.client.get("/api/admin/jobs", headers=env.login(w))
    assert r.status_code == 200, r.text
    listed = r.json()["jobs"]
    assert [j["kind"] for j in listed] == ["sync", "index"]           # newest first, theirs only
    sync, index = listed
    assert sync["status"] == "queued" and sync["details"] == {"provider": "notion"}
    assert index["id"] == failed and index["status"] == "failed"
    assert index["reason"] == "ai_unreachable" and "Connection error" in index["error"]
    assert index["details"] == {"full": True}
    assert "secret-export" not in r.text

    # Admins only: a member, or an admin viewing the app as public, can't.
    assert env.client.get("/api/admin/jobs",
                          headers=env.login(w, "staff")).status_code == 403
    assert env.client.get("/api/admin/jobs",
                          headers={**env.login(w), "X-Role": "public"}).status_code == 403


# ---- Members SCIM manages ------------------------------------------------------------------
def scim_member(w, name="sam"):
    username = f"{name}@{w}.example"
    members_store.set_password(username, name.title(), None, w, "public")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE members SET scim_updated_at = ? WHERE username = ?",
                     (time.time(), username))
    conn.close()
    return username


def listed(env, w, username):  # noqa: F811
    members = env.client.get("/api/admin/members", headers=env.login(w)).json()["members"]
    return next(m for m in members if m["username"] == username)


def test_members_scim_manages_are_marked_and_its_changes_refused(env):  # noqa: F811
    w = env.workspace()
    sam = scim_member(w)
    admin = env.login(w)
    # SCIM provisioned them, but no token can push any more: nothing is SCIM's.
    assert listed(env, w, sam)["scim_managed"] is False
    scim_store.create_token(w, f"admin@{w}.example")
    assert listed(env, w, sam)["scim_managed"] is True
    assert listed(env, w, f"staff@{w}.example")["scim_managed"] is False

    # Off stays possible (an emergency); back on, and removing, are the provider's.
    assert env.client.patch(f"/api/admin/members/{sam}", headers=admin,
                            json={"disabled": True}).status_code == 200
    r = env.client.patch(f"/api/admin/members/{sam}", headers=admin, json={"disabled": False})
    assert r.status_code == 409 and "SCIM" in r.json()["detail"]
    assert env.client.delete(f"/api/admin/members/{sam}", headers=admin).status_code == 409
    assert members_store.get(sam)["disabled"] is True
    # A role picked here still wins over their groups.
    assert env.client.patch(f"/api/admin/members/{sam}", headers=admin,
                            json={"role": "hr"}).status_code == 200

    # A member SCIM never touched is the admin's.
    staff = f"staff@{w}.example"
    assert env.client.delete(f"/api/admin/members/{staff}", headers=admin).status_code == 200


# ---- Insights: Not found -------------------------------------------------------------------
def today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def log_question(w, question, outcome="not_found", access="public"):
    return log_store.add(w, "someone", "web", outcome, text_mode="full", question=question,
                         max_access=access)


def not_found(env, w, **params):  # noqa: F811
    r = env.client.get("/api/admin/insights/not-found", headers=env.login(w), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_not_found_tab_lists_open_questions_and_marks_them_resolved(env):  # noqa: F811
    w = env.workspace()
    first = log_question(w, "How long is parental leave?")
    log_question(w, "What is the refund policy?", outcome="answered")
    second = log_question(w, "Who approves overtime?", access="restricted")

    got = not_found(env, w)
    assert got["text_kept"] is True and got["hidden"] == 0
    assert [q["question"] for q in got["questions"]] == ["Who approves overtime?",
                                                        "How long is parental leave?"]
    assert "someone" not in json.dumps(got)                    # never who asked

    admin = env.login(w)
    url = f"/api/admin/insights/not-found/{first}/resolve"
    assert env.client.post(url, headers=admin).status_code == 200
    assert [q["id"] for q in not_found(env, w)["questions"]] == [second]
    resolved = not_found(env, w, resolved="true")["questions"]
    assert [q["id"] for q in resolved] == [first] and resolved[0]["resolved_at"]
    assert len(events_of(w, "insights.resolve")) == 1
    assert env.client.delete(url, headers=admin).status_code == 200      # open again
    assert {q["id"] for q in not_found(env, w)["questions"]} == {first, second}

    # Only HR, and only the workspace's own unanswered questions.
    staff = env.login(w, "staff")
    assert env.client.get("/api/admin/insights/not-found", headers=staff).status_code == 403
    other = env.workspace()
    theirs = log_question(other, "Elsewhere?")
    assert env.client.post(f"/api/admin/insights/not-found/{theirs}/resolve",
                           headers=admin).status_code == 404
    answered = log_question(w, "Answered?", outcome="answered")
    assert env.client.post(f"/api/admin/insights/not-found/{answered}/resolve",
                           headers=admin).status_code == 404


def test_the_not_found_tab_follows_the_retention_mode_and_role(env):  # noqa: F811
    w = env.workspace()
    log_question(w, "Who approves overtime?", access="restricted")
    log_question(w, "How long is parental leave?")
    # A role below what a question's search reached doesn't see it at all.
    seen = insights.not_found_for(w, "public", "full", today(), today())
    assert [q["question"] for q in seen["questions"]] == ["How long is parental leave?"]
    assert seen["hidden"] == 1
    # Metadata only: the rows stay, without their words.
    log_store.set_settings(w, "metadata", 90)
    got = not_found(env, w)
    assert got["text_kept"] is False and len(got["questions"]) == 2
    assert all(q["question"] is None for q in got["questions"])


def test_an_unanswered_question_is_drafted_as_a_document(setup):  # noqa: F811
    log_id = log_question(setup.ws, "How long is parental leave?")
    r = setup.client.post("/api/drafts/from-question", headers=setup.admin,
                          json={"question_id": log_id})
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == {"question_id": log_id, "label": None}
    d = ready(setup, r.json()["id"])
    assert d["status"] == "draft" and d["kind"] == "gap"
    assert d["title"] == "Draft - How long is parental leave?"
    assert "public/parental.txt" in {s["doc_key"] for s in d["sources"]}
    (prompt,) = [p for p in setup.prompts if p.startswith("You draft a new")]
    assert "How long is parental leave?" in prompt
    assert len(events_of(setup.ws, "draft.from_question")) == 1

    # Not one that was answered, nor one whose text isn't kept.
    answered = log_question(setup.ws, "Answered?", outcome="answered")
    assert setup.client.post("/api/drafts/from-question", headers=setup.admin,
                             json={"question_id": answered}).status_code == 404
    log_store.set_settings(setup.ws, "metadata", 90)
    assert setup.client.post("/api/drafts/from-question", headers=setup.admin,
                             json={"question_id": log_id}).status_code == 404


# ---- Export and "Continue in my chats" -----------------------------------------------------
def test_a_conversation_exports_as_markdown(client):  # noqa: F811
    cid = hr_chat_with_a_restricted_and_a_public_answer(client)
    r = client.get(f"/api/conversations/{cid}/export", headers=headers(client, "hana"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"].startswith('attachment; filename="')
    text = r.text
    assert text.index(REFUND_Q) < text.index("thirty days") < text.index(BONUS_Q)
    assert "March" in text
    # Each answer's footnotes are numbered apart from the others': [^2-1], not [^1].
    assert "[^2-1]" in text and "\n[^2-1]: " in text and "[^1]" not in text

    # Viewing the app as public, the restricted answer is left out, as on screen.
    public = client.get(f"/api/conversations/{cid}/export",
                        headers=headers(client, "hana", "public")).text
    assert "March" not in public and "This answer is hidden" in public
    # Only its owner can.
    assert client.get(f"/api/conversations/{cid}/export",
                      headers=headers(client, "ada")).status_code == 404


def test_continue_in_my_chats_copies_only_what_the_viewer_may_see(client):  # noqa: F811
    cid = hr_chat_with_a_restricted_and_a_public_answer(client)
    link = share(client, "hana", cid)

    r = client.post(f"/api/shared/{link['token']}/continue", headers=headers(client, "pat"))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["skipped"] == 1 and got["title"].endswith(read(client, "hana", cid)["title"])
    mine = read(client, "pat", got["id"])
    assert [m["author"] for m in mine["messages"]] == ["user", "assistant"]
    assert mine["messages"][0]["content"] == REFUND_Q
    assert "thirty days" in mine["messages"][1]["content"]
    assert "March" not in json.dumps(mine) and BONUS_Q not in json.dumps(mine)
    assert all(not m.get("trace") for m in mine["messages"])
    # It's theirs: listed among their chats, and they can ask on from it.
    listed = client.get("/api/conversations", headers=headers(client, "pat")).json()
    assert got["id"] in {c["id"] for c in listed["conversations"]}
    events = ask(client, "pat", "And for gifts?", conversation_id=got["id"])
    assert events[-1]["type"] == "done"

    # HR sees and copies all of it.
    whole = client.post(f"/api/shared/{link['token']}/continue",
                        headers=headers(client, "ada")).json()
    assert whole["skipped"] == 0
    assert len(read(client, "ada", whole["id"])["messages"]) == 4


def test_continue_needs_something_to_copy_and_a_working_link(client):  # noqa: F811
    cid = new_chat(client, "hana")
    ask(client, "hana", BONUS_Q, conversation_id=cid)              # restricted only
    link = share(client, "hana", cid)
    r = client.post(f"/api/shared/{link['token']}/continue", headers=headers(client, "pat"))
    assert r.status_code == 400
    assert client.post("/api/shared/" + "x" * 43 + "/continue",
                       headers=headers(client, "pat")).status_code == 404
    assert client.delete(f"/api/shares/{link['id']}",
                         headers=headers(client, "hana")).status_code == 200
    assert client.post(f"/api/shared/{link['token']}/continue",
                       headers=headers(client, "ada")).status_code == 410
    assert open_link(client, "ada", link["token"]).status_code == 410

