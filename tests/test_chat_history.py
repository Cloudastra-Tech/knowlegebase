"""
Chat history and sharing (chat.md C9-C12, C16, C21, C25): searching your chats,
editing a question and regenerating an answer as branches, "Branch in new chat",
folders and pins, retention and its nightly purge, share links, Copy as markdown
with footnotes, titles and follow-ups. Through the real API, with the same fakes
as test_conversations.py (OpenAI and the search index).

The rule every reader here goes through: an answer is shown only to a role that
may read everything it was built from (visible_to). Search, shares and Copy must
never leak a withheld answer's text.
"""

import json
import time
import uuid

import pytest

import audit_store
import auth
import conversations_store
import db
import members_store
from kb import jobs
from kb.services import conversations, retention, suggestions
from test_conversations import PASSWORD, ask, ask_status, client, fakes, headers, new_chat, read  # noqa: F401

BONUS_Q = "When is the bonus announced?"
REFUND_Q = "What is the refund policy?"


def events_of(ws, action):
    return [e for e in audit_store.list_events(ws, limit=200)[0] if e["action"] == action]


def person(client, role="public", ws="default"):
    """Someone new, so what their searches find is only what the test made."""
    name = f"{role}-{uuid.uuid4().hex[:8]}"
    members_store.set_password(name, name.title(), auth.hash_password(PASSWORD), ws, role)
    client.tokens[name] = client.post("/api/auth/login", json={"username": name,
                                                                "password": PASSWORD}).json()["token"]
    return name


def search(client, who, q, role="hr"):
    r = client.get("/api/conversations/search", params={"q": q}, headers=headers(client, who, role))
    assert r.status_code == 200, r.text
    return r.json()["results"]


# ---- C9: search ---------------------------------------------------------------------------
def test_search_finds_titles_questions_and_answers_of_your_own_chats(client):
    me, other = person(client), person(client)
    cid = new_chat(client, me)
    ask(client, me, REFUND_Q, conversation_id=cid)

    by_question = search(client, me, "refund polic")             # prefixes match
    assert [r["conversation_id"] for r in by_question] == [cid]
    assert "\x02" in by_question[0]["snippet"] and "\x03" in by_question[0]["snippet"]
    by_answer = search(client, me, "thirty days")
    assert [r["conversation_id"] for r in by_answer] == [cid]
    assert by_answer[0]["title"] == REFUND_Q

    assert search(client, other, "thirty days") == []             # someone else's chat
    # FTS syntax is only ever words: no error, no match-all.
    for q in ('"', "refund OR *", "NEAR(", "thirty) AND (", ""):
        assert isinstance(search(client, me, q), list)
    assert search(client, me, "*") == []


def test_search_never_matches_an_answer_the_searchers_role_cant_read(client):
    hr = person(client, "hr")
    cid = new_chat(client, hr)
    ask(client, hr, BONUS_Q, conversation_id=cid)
    assert [r["conversation_id"] for r in search(client, hr, "March")] == [cid]

    # Viewing the app as public: the restricted answer's words don't match ...
    assert search(client, hr, "March", role="public") == []
    # ... and once demoted, they don't either, though the question still does.
    members_store.set_role(hr, "public", "default")
    assert search(client, hr, "March") == []
    assert search(client, hr, "pool") == []
    found = search(client, hr, "bonus announced")
    assert [r["conversation_id"] for r in found] == [cid]
    assert "March" not in json.dumps(found)


def test_a_renamed_chat_is_found_by_its_new_name(client):
    me = person(client)
    cid = new_chat(client, me)
    ask(client, me, REFUND_Q, conversation_id=cid)
    client.patch(f"/api/conversations/{cid}", json={"title": "Money back rules"},
                 headers=headers(client, me))
    assert [r["conversation_id"] for r in search(client, me, "money back")] == [cid]


# ---- C9: pin, folders ---------------------------------------------------------------------
def test_chats_can_be_pinned_and_moved_into_folders(client):
    cid = new_chat(client, "pat")
    ask(client, "pat", REFUND_Q, conversation_id=cid)
    h = headers(client, "pat")

    r = client.patch(f"/api/conversations/{cid}", json={"pinned": True}, headers=h)
    assert r.json()["pinned"] is True
    folder = client.post("/api/conversation-folders", json={"name": "  Policies  "}, headers=h).json()
    assert folder["name"] == "Policies"
    assert client.patch(f"/api/conversation-folders/{folder['id']}", json={"name": "HR policies"},
                        headers=h).json()["name"] == "HR policies"
    r = client.patch(f"/api/conversations/{cid}", json={"folder_id": folder["id"]}, headers=h)
    assert r.json()["folder_id"] == folder["id"] and r.json()["pinned"]

    listed = client.get("/api/conversations", headers=h).json()
    assert [f["name"] for f in listed["folders"]] == ["HR policies"]
    assert next(c for c in listed["conversations"] if c["id"] == cid)["folder_id"] == folder["id"]

    # Someone else's folder can't be used, nor theirs renamed or deleted.
    other = client.post("/api/conversation-folders", json={"name": "Mine"},
                        headers=headers(client, "sam")).json()
    assert client.patch(f"/api/conversations/{cid}", json={"folder_id": other["id"]},
                        headers=h).status_code == 404
    assert client.delete(f"/api/conversation-folders/{folder['id']}",
                         headers=headers(client, "sam")).status_code == 404
    assert client.post("/api/conversation-folders", json={"name": "   "}, headers=h).status_code == 400

    # Deleting the folder keeps its chats, in no folder; unpinning works.
    assert client.delete(f"/api/conversation-folders/{folder['id']}", headers=h).json() == {"ok": True}
    r = client.patch(f"/api/conversations/{cid}", json={"pinned": False}, headers=h)
    assert r.json()["folder_id"] is None and r.json()["pinned"] is False


# ---- C10: edit, regenerate, branches ------------------------------------------------------
def test_editing_a_question_branches_the_chat_and_both_branches_stay(client):
    cid = new_chat(client, "pat")
    ask(client, "pat", "What is the leave policy?", conversation_id=cid)
    ask(client, "pat", REFUND_Q, conversation_id=cid)
    first_q, first_a, old_q, old_a = read(client, "pat", cid)["messages"]

    events = ask(client, "pat", "How many leave days do employees get?", conversation_id=cid,
                 edit_of=old_q["id"])
    assert events[-1]["state"] == "answered"
    chat = read(client, "pat", cid)
    q1, a1, new_q, new_a = chat["messages"]
    assert (q1["id"], a1["id"]) == (first_q["id"], first_a["id"])
    assert new_q["content"] == "How many leave days do employees get?"
    assert new_q["parent_id"] == old_q["parent_id"] == first_a["id"]
    assert new_q["siblings"] == [old_q["id"], new_q["id"]]
    assert new_a["siblings"] == [new_a["id"]] and "twenty" in new_a["content"]

    # Back to the first version: its answer comes back with it.
    r = client.patch(f"/api/conversations/{cid}", json={"active_leaf_id": old_q["id"]},
                     headers=headers(client, "pat"))
    assert r.status_code == 200
    assert [m["id"] for m in read(client, "pat", cid)["messages"]] == [
        first_q["id"], first_a["id"], old_q["id"], old_a["id"]]
    # Switching branches isn't an audit event; it changes only what its owner looks at.
    assert not [e for e in events_of("default", "conversation.rename") if e["target_id"] == cid]


def test_an_edit_is_answered_with_only_the_conversation_before_it(client, fakes):
    cid = new_chat(client, "pat")
    ask(client, "pat", REFUND_Q, conversation_id=cid)
    first_q = read(client, "pat", cid)["messages"][0]
    events = ask(client, "pat", "What is the leave policy?", conversation_id=cid,
                 edit_of=first_q["id"])
    # The replaced first question is no history: nothing is rewritten.
    assert events[-1]["standalone_question"] is None
    chat = read(client, "pat", cid)
    assert [m["content"] for m in chat["messages"] if m["author"] == "user"] == [
        "What is the leave policy?"]


def test_regenerating_adds_a_new_answer_beside_the_old_one(client):
    cid = new_chat(client, "pat")
    ask(client, "pat", REFUND_Q, conversation_id=cid)
    question, first = read(client, "pat", cid)["messages"]

    done = ask(client, "pat", conversation_id=cid, regenerate_of=first["id"])[-1]
    assert done["state"] == "answered"
    question2, second = read(client, "pat", cid)["messages"]
    assert question2["id"] == question["id"] and second["id"] == done["message_id"]
    assert second["siblings"] == [first["id"], second["id"]]

    client.patch(f"/api/conversations/{cid}", json={"active_leaf_id": first["id"]},
                 headers=headers(client, "pat"))
    assert read(client, "pat", cid)["messages"][1]["id"] == first["id"]


def test_edits_and_regenerations_are_checked(client):
    cid = new_chat(client, "pat")
    ask(client, "pat", REFUND_Q, conversation_id=cid)
    question, answer = read(client, "pat", cid)["messages"]
    other = new_chat(client, "pat")
    ask(client, "pat", REFUND_Q, conversation_id=other)
    other_q, other_a = read(client, "pat", other)["messages"]

    assert ask_status(client, "pat", "New words", conversation_id=cid, edit_of=answer["id"]) == 400
    assert ask_status(client, "pat", "New words", conversation_id=cid, edit_of=other_q["id"]) == 400
    assert ask_status(client, "pat", "", conversation_id=cid, regenerate_of=question["id"]) == 400
    assert ask_status(client, "pat", "", conversation_id=cid, regenerate_of=other_a["id"]) == 400
    assert ask_status(client, "sam", "", conversation_id=cid, regenerate_of=answer["id"]) == 404
    assert ask_status(client, "pat", "", edit_of=question["id"]) == 400       # no conversation
    assert client.patch(f"/api/conversations/{cid}", json={"active_leaf_id": other_a["id"]},
                        headers=headers(client, "pat")).status_code == 404


def test_a_pending_answer_cant_be_regenerated():
    conv = conversations_store.create("default", "pat", "public")
    q = conversations_store.add_message(conv["id"], "user", "Anything?")
    a = conversations_store.add_message(conv["id"], "assistant", "", parent_id=q)
    with pytest.raises(Exception) as e:
        conversations.start_turn("default", "pat", "public", conv["id"], "", regenerate_of=a)
    assert getattr(e.value, "status_code", None) == 400


# ---- C11: fork ----------------------------------------------------------------------------
def test_branch_in_new_chat_copies_the_path_into_a_chat_of_its_own(client):
    me = person(client)
    cid = new_chat(client, me)
    ask(client, me, REFUND_Q, conversation_id=cid)
    ask(client, me, "What is the leave policy?", conversation_id=cid)
    q1, a1, q2, a2 = read(client, me, cid)["messages"]

    r = client.post(f"/api/conversations/{cid}/fork", json={"message_id": a1["id"]},
                    headers=headers(client, me))
    assert r.status_code == 200, r.text
    fork = r.json()
    assert fork["id"] != cid and fork["title"] == "↳ " + REFUND_Q
    chat = read(client, me, fork["id"])
    assert chat["forked_from"] == {"conversation_id": cid, "title": REFUND_Q}
    assert [(m["author"], m["content"]) for m in chat["messages"]] == [
        ("user", q1["content"]), ("assistant", a1["content"])]
    assert chat["messages"][1]["sources"] == a1["sources"]
    assert {m["id"] for m in chat["messages"]}.isdisjoint({q1["id"], a1["id"]})   # copies

    # It carries on on its own; deleting the original leaves it whole.
    ask(client, me, "And the leave policy?", conversation_id=fork["id"])
    assert len(read(client, me, cid)["messages"]) == 4
    client.delete(f"/api/conversations/{cid}", headers=headers(client, me))
    chat = read(client, me, fork["id"])
    assert len(chat["messages"]) == 4 and chat["forked_from"] is None
    assert [r["conversation_id"] for r in search(client, me, "thirty days")] == [fork["id"]]

    assert events_of("default", "conversation.fork")[0]["target_id"] == fork["id"]


def test_only_a_finished_answer_of_your_own_can_be_forked(client):
    cid = new_chat(client, "pat")
    ask(client, "pat", REFUND_Q, conversation_id=cid)
    question, answer = read(client, "pat", cid)["messages"]
    for who, mid in (("pat", question["id"]), ("pat", "nope"), ("sam", answer["id"])):
        r = client.post(f"/api/conversations/{cid}/fork", json={"message_id": mid},
                        headers=headers(client, who))
        assert r.status_code == 400, (who, mid)


def test_a_forked_restricted_answer_is_still_withheld_from_a_lower_role(client):
    cid = new_chat(client, "hana")
    ask(client, "hana", BONUS_Q, conversation_id=cid)
    answer = read(client, "hana", cid)["messages"][1]
    fork = client.post(f"/api/conversations/{cid}/fork", json={"message_id": answer["id"]},
                       headers=headers(client, "hana")).json()
    copied = read(client, "hana", fork["id"], role="public")["messages"][1]
    assert copied["withheld"] and copied["content"] == ""


# ---- C25: copy as markdown ----------------------------------------------------------------
def test_copy_turns_citations_into_footnotes():
    message = {"content": "Refunds take thirty days [S2]. Leave is twenty days [S1][S2]. "
                          "Bogus [S9].",
               "sources": [{"n": 1, "title": "Leave policy", "provider": "Company documents",
                            "url": None, "heading_path": "Handbook › Leave", "page": 3},
                           {"n": 2, "title": "Refunds", "provider": "Google Drive",
                            "url": "https://docs.example/refunds"},
                           {"title": "Pune", "provider": "Wikipedia", "url": "https://w/Pune",
                            "kind": "general_knowledge"}]}
    assert conversations.markdown_of(message) == (
        "Refunds take thirty days[^1]. Leave is twenty days[^2][^1]. Bogus.\n\n"
        "[^1]: [Refunds](https://docs.example/refunds) — Google Drive\n"
        "[^2]: Leave policy, Handbook › Leave, page 3 — Company documents\n"
        "- [Pune](https://w/Pune) — General knowledge · Wikipedia")


def test_copy_is_only_for_an_answer_you_may_read(client):
    cid = new_chat(client, "hana")
    ask(client, "hana", BONUS_Q, conversation_id=cid)
    question, answer = read(client, "hana", cid)["messages"]
    r = client.get(f"/api/messages/{answer['id']}/markdown", headers=headers(client, "hana"))
    assert r.status_code == 200 and r.json()["markdown"].startswith(
        "The annual bonus pool is announced in March.[^1]")
    assert "[^1]: bonus.md" in r.json()["markdown"]
    assert client.get(f"/api/messages/{answer['id']}/markdown",
                      headers=headers(client, "hana", "public")).status_code == 403
    assert client.get(f"/api/messages/{answer['id']}/markdown",
                      headers=headers(client, "sam")).status_code == 404
    assert client.get(f"/api/messages/{question['id']}/markdown",
                      headers=headers(client, "hana")).status_code == 404


# ---- C12: retention -----------------------------------------------------------------------
@pytest.fixture
def workspace(client):
    """A workspace of its own: an admin, an HR person and a member."""
    ws = f"ret-{uuid.uuid4().hex[:8]}"
    names = {}
    for name, role in (("admin", "admin"), ("hr", "hr"), ("staff", "public")):
        username = f"{name}@{ws}.example"
        members_store.set_password(username, name.title(), auth.hash_password(PASSWORD), ws, role)
        client.tokens[username] = client.post(
            "/api/auth/login", json={"username": username, "password": PASSWORD}).json()["token"]
        names[name] = username
    return ws, names


def age(cid, days):
    conn = db.connect()
    with conn:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                     (time.time() - days * 86400, cid))
    conn.close()


def test_chat_settings_default_to_a_year_and_only_admins_change_them(client, workspace):
    ws, who = workspace
    for person in who.values():
        r = client.get("/api/chat-settings", headers=headers(client, person))
        assert r.json()["retention_days"] == 365 and r.json()["sharing_enabled"] is True
    for person in (who["hr"], who["staff"]):
        assert client.put("/api/admin/chat-settings", json={"retention_days": 30},
                          headers=headers(client, person)).status_code == 403
    assert client.put("/api/admin/chat-settings", json={"retention_days": 30},
                      headers=headers(client, who["admin"], "public")).status_code == 403
    assert client.put("/api/admin/chat-settings", json={"retention_days": 45},
                      headers=headers(client, who["admin"])).status_code == 400

    r = client.put("/api/admin/chat-settings", json={"retention_days": None, "sharing_enabled": False},
                   headers=headers(client, who["admin"]))
    assert r.status_code == 200
    seen = client.get("/api/chat-settings", headers=headers(client, who["staff"])).json()
    assert seen["retention_days"] is None and seen["sharing_enabled"] is False
    assert seen["updated_by"] == who["admin"]
    event = events_of(ws, "chat_settings.update")[0]
    assert event["details"] == {"retention_days": [365, None], "sharing_enabled": [True, False]}
    # Another workspace keeps its own.
    assert client.get("/api/chat-settings", headers=headers(client, "pat")).json()[
        "retention_days"] == 365


def test_the_nightly_purge_deletes_chats_past_retention_with_everything_on_them(client, workspace):
    ws, who = workspace
    staff = who["staff"]
    old, recent = new_chat(client, staff), new_chat(client, staff)
    for cid in (old, recent):
        ask(client, staff, REFUND_Q, conversation_id=cid)
    old_answer = read(client, staff, old)["messages"][1]["id"]
    client.put(f"/api/messages/{old_answer}/feedback", json={"rating": -1},
               headers=headers(client, staff))
    link = client.post("/api/shares", json={"conversation_id": old},
                       headers=headers(client, staff)).json()
    age(old, 40)
    age(recent, 20)

    assert retention.purge() == {}                  # a year: nothing is that old
    client.put("/api/admin/chat-settings", json={"retention_days": 30},
               headers=headers(client, who["admin"]))
    assert retention.purge()[ws] == 1

    assert client.get(f"/api/conversations/{old}", headers=headers(client, staff)).status_code == 404
    assert read(client, staff, recent)["messages"]
    assert conversations_store.feedback_of(staff, [old_answer]) == {}
    assert conversations_store.share_by_token(link["token"]) is None
    # Deleted the normal way, so the question log forgets their words too (its trigger).
    conn = db.connect()
    logged = conn.execute("SELECT COUNT(*) FROM question_log WHERE workspace = ? AND "
                          "question IS NOT NULL", (ws,)).fetchone()[0]
    leftover = conn.execute("SELECT COUNT(*) FROM question_log WHERE message_id = ?",
                            (old_answer,)).fetchone()[0]
    conn.close()
    assert logged == 1 and leftover == 0              # only the recent chat's question
    assert [r["conversation_id"] for r in search(client, staff, "thirty")] == [recent]
    event = events_of(ws, "chat.retention_purge")[0]
    assert (event["actor"], event["actor_type"]) == ("system", "system")
    assert event["details"] == {"deleted": 1, "retention_days": 30}

    # "Forever" purges nothing.
    client.put("/api/admin/chat-settings", json={"retention_days": None},
               headers=headers(client, who["admin"]))
    age(recent, 4000)
    assert ws not in retention.purge()


def test_the_purge_is_a_nightly_job():
    jobs.load_handlers()
    assert "chat_retention" in jobs.HANDLERS
    assert jobs.PERIODIC["chat_retention"]() == 24 * 60 * 60


# ---- C16: share links ---------------------------------------------------------------------
def share(client, who, cid, role="hr", **body):
    r = client.post("/api/shares", json={"conversation_id": cid, **body},
                    headers=headers(client, who, role))
    assert r.status_code == 200, r.text
    return r.json()


def open_link(client, who, token, role="hr"):
    return client.get(f"/api/shared/{token}", headers=headers(client, who, role))


def hr_chat_with_a_restricted_and_a_public_answer(client):
    cid = new_chat(client, "hana")
    ask(client, "hana", REFUND_Q, conversation_id=cid)
    ask(client, "hana", BONUS_Q, conversation_id=cid)
    return cid


def test_a_shared_chat_withholds_answers_a_lower_role_cant_read(client):
    cid = hr_chat_with_a_restricted_and_a_public_answer(client)
    link = share(client, "hana", cid)
    assert link["url"] == f"/s/{link['token']}" and link["kind"] == "chat"
    assert link["restricted"] is True

    # Only the token's hash is stored.
    conn = db.connect()
    stored = conn.execute("SELECT * FROM shares WHERE id = ?", (link["id"],)).fetchone()
    conn.close()
    assert link["token"] not in json.dumps(dict(stored))
    assert stored["token_hash"] == conversations_store.token_hash(link["token"])

    seen = open_link(client, "ada", link["token"]).json()          # HR / admin: all of it
    assert seen["shared_by"] == "hana" and seen["withheld"] == 0
    assert "March" in json.dumps(seen)

    r = open_link(client, "pat", link["token"])                     # a public member
    assert r.status_code == 200
    seen = r.json()
    q1, refund, q2, bonus = seen["messages"]
    assert "thirty days" in refund["content"] and not refund["withheld"]
    assert refund["copy"] and "[^1]" in refund["copy"]
    assert q2["content"] == BONUS_Q                                  # the question stays
    assert bonus["withheld"] and bonus["content"] == "" and bonus["sources"] == []
    assert bonus["question"] == BONUS_Q and bonus["copy"] is None
    assert seen["withheld"] == 1
    assert "March" not in r.text and "bonus.md" not in r.text
    # An HR member viewing the app as public is treated as public.
    assert "March" not in open_link(client, "ada", link["token"], role="public").text


def test_a_shared_answer_shows_only_it_and_its_question(client):
    cid = hr_chat_with_a_restricted_and_a_public_answer(client)
    refund = read(client, "hana", cid)["messages"][1]
    link = share(client, "hana", cid, message_id=refund["id"])
    assert link["kind"] == "answer" and link["restricted"] is False
    seen = open_link(client, "pat", link["token"]).json()
    assert [m["author"] for m in seen["messages"]] == ["user", "assistant"]
    assert seen["messages"][1]["id"] == refund["id"] and "Bonus" not in json.dumps(seen)


def test_a_share_never_shows_more_than_its_sharer_may_see_now(client):
    cid = hr_chat_with_a_restricted_and_a_public_answer(client)
    link = share(client, "hana", cid)
    members_store.set_role("hana", "public", "default")             # demoted after sharing
    seen = open_link(client, "ada", link["token"]).json()
    assert seen["withheld"] == 1 and "March" not in json.dumps(seen)
    members_store.set_role("hana", "hr", "default")
    assert "March" in open_link(client, "ada", link["token"]).text

    # Shared while viewing the app as public: shared as public.
    link = share(client, "hana", cid, role="public")
    assert "March" not in open_link(client, "ada", link["token"]).text

    # A sharer who has left the workspace: the link is dead.
    members_store.set_disabled("hana", True, "default")
    try:
        assert open_link(client, "ada", link["token"]).status_code == 410
    finally:
        members_store.set_disabled("hana", False, "default")


def test_links_expire_can_be_revoked_and_stop_with_sharing_off(client, workspace):
    ws, who = workspace
    staff, admin = who["staff"], who["admin"]
    cid = new_chat(client, staff)
    ask(client, staff, REFUND_Q, conversation_id=cid)

    day = share(client, staff, cid, expires_in_days=1)
    assert abs(day["expires_at"] - (time.time() + 86400)) < 60
    assert open_link(client, who["hr"], day["token"]).status_code == 200
    conn = db.connect()
    with conn:                                      # a day later
        conn.execute("UPDATE shares SET expires_at = ? WHERE id = ?", (time.time() - 1, day["id"]))
    conn.close()
    assert open_link(client, who["hr"], day["token"]).status_code == 410

    # Never longer than the chat is kept.
    forever = share(client, staff, cid, expires_in_days=None)
    assert abs(forever["expires_at"] - (time.time() + 365 * 86400)) < 60
    assert client.post("/api/shares", json={"conversation_id": cid, "expires_in_days": 12},
                       headers=headers(client, staff)).status_code == 400

    listed = client.get("/api/shares", params={"conversation_id": cid},
                        headers=headers(client, staff)).json()["shares"]
    assert [s["id"] for s in listed] == [forever["id"]]       # the expired one is gone
    assert all("token" not in s for s in listed)

    assert client.delete(f"/api/shares/{forever['id']}",
                         headers=headers(client, who["hr"])).status_code == 404   # not theirs
    assert client.delete(f"/api/shares/{forever['id']}",
                         headers=headers(client, staff)).json() == {"ok": True}
    assert open_link(client, who["hr"], forever["token"]).status_code == 410
    assert [e["target_id"] for e in events_of(ws, "share.revoke")] == [forever["id"]]
    assert len(events_of(ws, "share.create")) == 2

    live = share(client, staff, cid)
    client.put("/api/admin/chat-settings", json={"retention_days": 365, "sharing_enabled": False},
               headers=headers(client, admin))
    assert open_link(client, who["hr"], live["token"]).status_code == 410
    r = client.post("/api/shares", json={"conversation_id": cid}, headers=headers(client, staff))
    assert r.status_code == 403


def test_a_link_is_for_its_own_workspace_and_its_owners_chats(client, workspace):
    ws, who = workspace
    cid = new_chat(client, "pat")
    ask(client, "pat", REFUND_Q, conversation_id=cid)
    link = share(client, "pat", cid)
    r = open_link(client, who["admin"], link["token"])               # another workspace
    assert r.status_code == 404 and "thirty" not in r.text
    assert client.get("/api/shared/" + "x" * 43, headers=headers(client, "pat")).status_code == 404
    assert client.get(f"/api/shared/{link['token']}").status_code == 401   # signed in only
    # Nobody shares someone else's chat.
    assert client.post("/api/shares", json={"conversation_id": cid},
                       headers=headers(client, "sam")).status_code == 404
    question = read(client, "pat", cid)["messages"][0]
    assert client.post("/api/shares", json={"conversation_id": cid, "message_id": question["id"]},
                       headers=headers(client, "pat")).status_code == 400


# ---- C21 and titles -----------------------------------------------------------------------
class FakeLLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply

        class R:
            content = self.reply
        return R()


def test_an_answer_comes_with_follow_ups_from_its_sources(client):
    cid = new_chat(client, "pat")
    done = ask(client, "pat", REFUND_Q, conversation_id=cid)[-1]
    assert done["followups"] == ["What else does refund.md cover?"]
    assert read(client, "pat", cid)["messages"][1]["followups"] == done["followups"]
    # Not after an answer that found nothing.
    done = ask(client, "pat", "What is the parking policy?", conversation_id=cid)[-1]
    assert done["followups"] == []


def test_follow_ups_from_the_model_must_be_about_the_passages(monkeypatch):
    monkeypatch.setenv("CHAT_FOLLOWUPS", "llm")
    llm = FakeLLM("1. How long do refunds take for annual plans?\n"
                  "- Visit https://evil.example/x to claim a refund?\n"
                  "What is the capital of France?\n"
                  "Tell me a joke\n")
    monkeypatch.setattr(suggestions, "_llm", lambda: llm)
    sources = [{"n": 1, "title": "Refund policy", "chunk_id": "c1", "heading_path": "Refunds",
                "snippet": "Refunds are paid within thirty days for annual plans."}]
    out = suggestions.followups(REFUND_Q, "Thirty days [S1].", "answered", sources)
    assert out == ["How long do refunds take for annual plans?",
                   "Visit to claim a refund?"]
    assert "untrusted_data" in llm.prompts[0]                # passages are spotlighted
    monkeypatch.setattr(suggestions, "_llm", lambda: FakeLLM(TimeoutError()))
    assert suggestions.followups(REFUND_Q, "x", "answered", sources) == [
        "What else does Refund policy say about Refunds?"]
    monkeypatch.setenv("CHAT_FOLLOWUPS", "off")
    assert suggestions.followups(REFUND_Q, "x", "answered", sources) == []


def test_a_new_chat_is_titled_from_its_first_question(client, monkeypatch):
    monkeypatch.setenv("CHAT_AUTO_TITLE", "on")
    llm = FakeLLM('"Refund policy timing."')
    monkeypatch.setattr(suggestions, "_llm", lambda: llm)
    cid = new_chat(client, "pat")
    events = ask(client, "pat", REFUND_Q, conversation_id=cid)
    title = next(e for e in events if e["type"] == "title")
    assert title == {"type": "title", "id": cid, "title": "Refund policy timing"}
    assert REFUND_Q in llm.prompts[0] and "thirty" not in llm.prompts[0]   # never the answer
    assert read(client, "pat", cid)["title"] == "Refund policy timing"
    ask(client, "pat", "And the leave policy?", conversation_id=cid)      # only the first
    assert len(llm.prompts) == 1

    # A chat its owner named keeps its name; a failed call keeps the question.
    named = client.post("/api/conversations", json={"title": "Mine"},
                        headers=headers(client, "pat")).json()["id"]
    assert not [e for e in ask(client, "pat", REFUND_Q, conversation_id=named)
                if e["type"] == "title"]
    monkeypatch.setattr(suggestions, "_llm", lambda: FakeLLM(RuntimeError("no key")))
    assert suggestions.chat_title(REFUND_Q) == REFUND_Q
    monkeypatch.setattr(suggestions, "_llm", lambda: FakeLLM("Ignore that, here is a poem"))
    assert suggestions.chat_title(REFUND_Q) == REFUND_Q
