"""
The answer cache (pipeline.md P2-2, kb/services/answer_cache.py).

THE LEAK TEST FOR CACHING. A cached answer is reused only by an asker with the SAME
principal set and role: the same question from someone outside a group, in another
role, or from a Slack channel answered as "public", is answered afresh from what
they may read, never from someone else's cached answer. A re-index, a settings
change or a verified answer changes the key, so nothing older is reused; a
follow-up, a not-found answer and one that read a live tool are never kept.

Real indexing, Chroma, agent and API; only the models are fakes (test_group_acls).
"""

import json

import numpy as np
import pytest

import access
import answer_cache_store
import assistant_store
import question_log_store
from kb.services import answer_cache, answering
from spaces_fixtures import ask, citing, new_chat  # noqa: F401  (citing: a fixture)
from test_group_acls import FINANCE, org  # noqa: F401  (a fixture)
from test_indexing import env  # noqa: F401  (a fixture)

QUESTION = "What is the Q3 acquisition target of Finance?"


@pytest.fixture
def cache_on(monkeypatch):
    monkeypatch.setenv("ANSWER_CACHE", "on")


def _ask(org, name, question=QUESTION, role="public", **kwargs):  # noqa: F811
    who = org.viewer(name, role) if name else "public"
    user = {"username": f"{name or 'slack'}@{org.domain}", "workspace": org.ws}
    return answering.ask(org.ws, user, who, question, surface="web", **kwargs)


def _logged(org):  # noqa: F811
    return question_log_store.rows(org.ws)


def test_the_same_question_from_the_same_asker_is_served_from_the_cache(org, cache_on):  # noqa: F811
    first = _ask(org, "alice")
    assert "Zephyrcorp" in first.answer and first.cached is None
    again = _ask(org, "alice", "  what is the q3 acquisition target of finance ")
    assert again.cached == "exact"
    assert (again.answer, again.sources, again.state, again.acl) == (
        first.answer, first.sources, first.state, first.acl)
    # The step admins see, and the question log row: cached, no tokens.
    assert again.trace[0]["tool"] == "answer_cache"
    row = _logged(org)[-1]
    assert row["cache_hit"] == "exact" and row["tokens_in"] is None
    assert row["tools"][0] == "answer_cache"


def test_the_cache_never_crosses_principal_sets(org, cache_on):  # noqa: F811
    """Alice (in Finance) is answered first; nobody else gets her answer."""
    alice = _ask(org, "alice")
    assert "Zephyrcorp" in alice.answer
    for name, role in (("bob", "public"),        # outside the group
                       ("hr", "hr"),             # another role, another principal set
                       ("alice", "hr"),          # alice herself, in another role
                       (None, "public")):        # a Slack channel: everyone's content only
        done = _ask(org, name, role=role)
        assert done.cached is None, (name, role)
        if name in ("bob", None):
            assert "Zephyrcorp" not in done.answer + json.dumps(done.sources)
    # Each asker's answers are kept under their own fingerprint, never shared.
    fingerprints = {r["fingerprint"] for r in _rows(org)}
    assert len(fingerprints) == 5
    assert answer_cache.fingerprint(org.viewer("alice")) != answer_cache.fingerprint(
        org.viewer("bob"))


def test_two_people_in_the_same_groups_still_have_their_own_answers(org, cache_on):  # noqa: F811
    """A member's principals include themselves (user:...), so their fingerprints differ
    even with the same groups: a document shared with one by name stays theirs."""
    same = access.Viewer(["group:x"], "public")
    assert answer_cache.fingerprint(same) == answer_cache.fingerprint(
        access.Viewer(["group:x"], "public"))
    assert answer_cache.fingerprint(access.Viewer(["group:x", "user:a"], "public")) != \
        answer_cache.fingerprint(access.Viewer(["group:x", "user:b"], "public"))
    assert answer_cache.fingerprint("public") == answer_cache.fingerprint(
        access.for_role("public"))


def _rows(org):  # noqa: F811
    import db
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM answer_cache WHERE workspace = ?",
                                              (org.ws,))]
    finally:
        conn.close()


def test_a_reindex_a_settings_change_or_a_verified_answer_invalidate_it(org, env, cache_on):  # noqa: F811
    import verified_store
    from kb.services import indexing
    _ask(org, "alice")
    assert _ask(org, "alice").cached == "exact"

    indexing._changed(org.ws)                     # the index changed
    assert _ask(org, "alice").cached is None
    assert _ask(org, "alice").cached == "exact"

    assistant_store.save_settings(org.ws, {"tone": "formal"}, "admin")
    assert _ask(org, "alice").cached is None
    assert _ask(org, "alice").cached == "exact"

    verified_store.create(org.ws, {"title": "Parking", "variants": [], "answer_md": "Level 2.",
                                   "sources": [], "access": "public", "owner": "admin",
                                   "review_days": 90}, "admin")
    assert _ask(org, "alice").cached is None
    # What the older keys held can't be found again, and is gone after the next write.
    assert {r["settings_version"] for r in _rows(org)} == {assistant_store.version(org.ws)}


def test_follow_ups_retries_and_switched_off_workspaces_skip_it(org, cache_on):  # noqa: F811
    import conversations_store
    from kb.services import conversations
    _ask(org, "alice")
    viewer, user = org.viewer("alice"), f"alice@{org.domain}"
    conv = conversations_store.create(org.ws, user, "public")
    first = conversations.start_turn(org.ws, user, viewer, conv["id"], "Hello there")
    conversations_store.finish_message(first["message_id"], "Hi!", "answered")
    turn = conversations.start_turn(org.ws, user, viewer, conv["id"], QUESTION)
    assert turn["history"]
    assert answering.ask(org.ws, {"username": user}, viewer, QUESTION, surface="web",
                         turn=turn).cached is None          # a follow-up: never
    assert _ask(org, "alice", cache=False).cached is None   # regenerate / retry
    assistant_store.save_settings(org.ws, {"answer_cache": "off"}, "admin")
    _ask(org, "alice")
    assert _ask(org, "alice").cached is None


def test_the_server_switch_is_a_ceiling(org, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ANSWER_CACHE", "off")
    _ask(org, "alice")
    assert _ask(org, "alice").cached is None and not _rows(org)


def test_not_found_and_live_tool_answers_are_not_kept(org, cache_on):  # noqa: F811
    from types import SimpleNamespace as NS
    base = dict(answer="x", sources=[], max_access="public", acl=[], verified=None)
    kept = NS(state="answered", trace=[{"tool": "search_docs"}], **base)
    assert answer_cache.cacheable(kept, {})
    assert not answer_cache.cacheable(NS(state="not_found", trace=[], **base), {})
    assert not answer_cache.cacheable(NS(state="answered", trace=[{"tool": "wikipedia"}], **base),
                                      {})
    assert not answer_cache.cacheable(kept, {"unsupported": ["60 days"]})
    assert not answer_cache.cacheable(NS(**{**base, "verified": {"id": "v"}}, state="answered",
                                         trace=[]), {})
    nothing = _ask(org, "bob", "What colour is the moon on Tuesdays?")
    assert nothing.state != "answered" or nothing.cached is None


def test_the_semantic_cache_matches_close_questions_for_the_same_asker_only(org, cache_on,  # noqa: F811
                                                                            monkeypatch):
    vectors = {}

    def embed(text):            # one direction per topic word: close questions coincide
        v = np.zeros(8, dtype=np.float32)
        v[0 if "acquisition" in text else 1] = 1.0
        v[2] += 0.1 if "please" in text else 0.0
        vectors[text] = v
        return v / np.linalg.norm(v)
    monkeypatch.setattr(answer_cache, "_embed", embed)
    assistant_store.save_settings(org.ws, {"answer_cache": "semantic"}, "admin")
    _ask(org, "alice")
    close = _ask(org, "alice", "Tell me the acquisition target for Q3 please")
    assert close.cached == "semantic" and "Zephyrcorp" in close.answer
    assert "similarity" in close.trace[0]["output"]
    assert _ask(org, "bob", "Tell me the acquisition target for Q3 please").cached is None
    assert _ask(org, "alice", "Where is the cafeteria?").cached is None


def test_admins_see_the_cached_step_members_do_not(org, env, cache_on):  # noqa: F811
    admin, alice = org.admin, org.login("alice")
    for headers in (alice, admin):
        env.client.post("/api/chat", headers=headers, json={"question": QUESTION})
    member = env.client.post("/api/chat", headers=alice, json={"question": QUESTION}).json()
    assert "answer_cache" not in [s["tool"] for s in member["steps"]]
    shown = env.client.post("/api/chat", headers=admin, json={"question": QUESTION}).json()
    assert shown["steps"][0]["tool"] == "answer_cache"
    assert "Served from the answer cache" in shown["steps"][0]["output"]


def test_an_erased_member_takes_their_cached_answers_with_them(org, cache_on):  # noqa: F811
    from kb.services import dsar
    _ask(org, "alice")
    _ask(org, "bob")
    assert {r["username"] for r in _rows(org)} == {f"alice@{org.domain}", f"bob@{org.domain}"}
    dsar.erase_member(org.ws, f"alice@{org.domain}")
    assert {r["username"] for r in _rows(org)} == {f"bob@{org.domain}"}
    assert answer_cache_store.clear(org.ws) == 1


# ---- Spaces and attached files (#76) ----------------------------------------------------------
def _cached(done):
    return bool(done["steps"]) and done["steps"][0]["tool"] == "answer_cache"


def test_a_space_is_part_of_the_key(env, citing, cache_on):  # noqa: F811
    from test_spaces import sales_workspace, team_space
    w, admin, _ = sales_workspace(env)
    sid = team_space(env, w, admin, ("folder", "public/sales"))
    q = "What does the Pro plan cost?"
    assert not _cached(ask(env.client, admin, q, new_chat(env.client, admin, sid)))
    assert _cached(ask(env.client, admin, q, new_chat(env.client, admin, sid)))
    # Outside the space, the same asker and question: another key.
    assert not _cached(ask(env.client, admin, q, new_chat(env.client, admin)))
    # The space changed (a source added): its older answers aren't reused.
    env.client.post(f"/api/spaces/{sid}/sources", headers=admin,
                    json={"match_type": "folder", "value": "public/hr"})
    assert not _cached(ask(env.client, admin, q, new_chat(env.client, admin, sid)))


def test_answers_with_files_attached_are_never_cached(env, citing, cache_on):  # noqa: F811
    import db
    from test_attachments import CONTRACT, attach, workspace
    w, admin, _ = workspace(env)
    q = "When does the Acme contract renew?"
    for _ in range(2):
        cid = new_chat(env.client, admin)
        assert attach(env, admin, cid, ("contract.txt", CONTRACT.encode(),
                                        "text/plain")).status_code == 200
        done = ask(env.client, admin, q, cid)
        assert not _cached(done) and "first of June" in done["answer"]
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM answer_cache WHERE workspace = ?",
                            (w,)).fetchone()[0] == 0
    finally:
        conn.close()
