"""
The Teams bot (kb/routers/teams.py, kb/services/teams.py, teams_store.py):
requests carry the Bot Connector's JWT, checked as Microsoft documents it (key,
endorsement, issuer, audience, expiry, serviceUrl); activities are taken in once;
a Teams user is the member with their email or UPN, with their role as it is
now; answers in channels and group chats come from public documents only, and an
asker who could see more is answered again in their 1:1 chat; answers are
Adaptive Cards with numbered source links and feedback buttons that land in
message_feedback; setting it up is admin-only, audited, and never shows the
secret.

Through the real API, job runner, search index and agent (fake models, as in
test_api_keys.py); every call to Microsoft goes to the recorded-HTTP fake in
teams_fixtures.py, whose signing key is generated when the tests run.
"""

import io
import json
import time
import zipfile

import pytest

import db
import members_store
import teams_store
from kb import jobs, state
from kb.services import teams as teams_service
from teams_fixtures import (BOT, BOT_SECRET, ISSUER, SERVICE_URL, TENANT, FakeTeams, _jwk,
                            activity, headers, other_key, token)
from test_api_keys import kb  # noqa: F401  (a fixture)
from test_indexing import env  # noqa: F401  (a fixture kb needs)

QUESTION = "When does the Brightlearn vendor contract end?"
SECRET_WORDS = "March 2027"            # only in the restricted contract
PUBLIC_WORDS = "Guide"                 # the public guide (the fake model quotes its heading)


@pytest.fixture
def teams(kb, monkeypatch):  # noqa: F811
    """A workspace with documents (kb) whose own Azure Bot is connected (tenant
    TENANT), jobs run by hand, and Microsoft faked. aad-hr is its HR person,
    aad-staff a public member, aad-admin its admin."""
    monkeypatch.setenv("PUBLIC_URL", "https://kb.example.com")
    for cache in (state.teams_keys, state.teams_tokens, state.teams_people):
        cache.clear()
    fake = FakeTeams(monkeypatch)
    w = kb.workspace_with_docs()
    monkeypatch.setenv("JOBS_IN_PROCESS", "off")
    jobs.stop_in_process()
    teams_store.set_custom_app(w, BOT, TENANT, BOT_SECRET, f"admin@{w}.example")
    fake.people.update({"aad-hr": f"hr@{w}.example", "aad-staff": f"staff@{w}.example",
                        "aad-admin": f"admin@{w}.example", "aad-stranger": "nobody@else.example"})
    kb.ws, kb.fake = w, fake
    kb.path = teams_service.messaging_path(teams_store.get(w))

    def send(act, tok=None, path=None, **kw):
        return kb.client.post(path or kb.path, content=json.dumps(act).encode(),
                              headers=headers(tok, **kw))

    def run_jobs():
        runner = jobs.Runner(handlers={"teams": jobs.HANDLERS["teams"]}, threads=0)
        while runner.run_once():
            pass
        conn = db.connect()
        try:
            rows = conn.execute("SELECT status, result, error FROM jobs WHERE kind = 'teams' "
                                "ORDER BY id").fetchall()
            conn.execute("DELETE FROM jobs WHERE kind = 'teams'")
            conn.commit()
        finally:
            conn.close()
        return [{"status": r["status"], **json.loads(r["result"] or "{}"), "error": r["error"]}
                for r in rows]

    kb.send, kb.run_jobs = send, run_jobs
    yield kb
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM jobs WHERE kind = 'teams'")
        for table in ("teams_installs", "teams_users", "teams_threads"):
            conn.execute(f"DELETE FROM {table}")     # the next test connects TENANT again
    conn.close()


def queued():
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'teams' AND "
                            "status = 'queued'").fetchone()[0]
    finally:
        conn.close()


def everything(fake, where=None):
    return " ".join(json.dumps(c["json"]) for c in fake.calls
                    if c["method"] in ("POST", "PUT") and "/activities" in c["path"]
                    and (where is None or where in c["path"]))


def _events(ws, action=None):
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM audit_events WHERE workspace = ? ORDER BY id",
                            (ws,)).fetchall()
    finally:
        conn.close()
    out = [dict(r, details=json.loads(r["details"] or "{}")) for r in rows]
    return [e for e in out if action is None or e["action"] == action]


# ---- The Bot Connector's token --------------------------------------------------------------
def test_a_good_token_is_accepted_and_the_message_queued(teams):
    r = teams.send(activity("personal_message", "aad-staff", "What is the refund policy?"))
    assert r.status_code == 200, r.text
    assert queued() == 1
    assert teams_store.get(teams.ws)["status"] == "connected"


@pytest.mark.parametrize("bad", ["expired", "wrong_audience", "wrong_issuer", "other_key",
                                 "service_url", "no_token", "not_a_jwt", "hs256"])
def test_a_token_that_isnt_the_connectors_for_this_bot_is_refused(teams, bad):
    act = activity("personal_message", "aad-staff", "hi")
    tok = {
        "expired": token(exp_in=-6 * 60),                    # past the 5 minutes' skew
        "wrong_audience": token(aud="ffffffff-0000-0000-0000-000000000000"),
        "wrong_issuer": token(iss="https://sts.windows.net/someone-else/"),
        "other_key": token(key=other_key()),
        "service_url": token(service_url="https://evil.example/"),
        "no_token": None,
        "not_a_jwt": "abc.def",
        "hs256": __import__("jwt").encode({"iss": ISSUER, "aud": BOT, "exp": time.time() + 60,
                                          "serviceUrl": SERVICE_URL}, "k" * 32,
                                         algorithm="HS256"),
    }[bad]
    if bad == "no_token":
        r = teams.client.post(teams.path, json=act)
    else:
        r = teams.send(act, tok=tok)
    assert r.status_code == 401
    assert queued() == 0


def test_a_token_within_the_clock_skew_is_accepted(teams):
    r = teams.send(activity("personal_message", "aad-staff", "hi"), tok=token(exp_in=-4 * 60))
    assert r.status_code == 200


def test_a_key_not_endorsed_for_teams_is_refused_with_403(teams):
    teams.fake.keys = [_jwk(__import__("teams_fixtures")._KEY, "test-key-1", ["skype"])]
    r = teams.send(activity("personal_message", "aad-staff", "hi"))
    assert r.status_code == 403 and queued() == 0


def test_the_keys_are_fetched_once_and_again_for_an_unknown_key(teams):
    for _ in range(3):
        teams.send(activity("personal_message", "aad-staff", "hi"))
    fetched = [c for c in teams.fake.calls if c["url"].endswith("/.well-known/keys")]
    assert len(fetched) == 1
    rotated = other_key()
    teams.fake.keys.append(_jwk(rotated, "rotated", ["msteams"]))
    state.teams_keys["keys"] = (time.time() - 10 * 60, state.teams_keys["keys"][1])
    assert teams.send(activity("personal_message", "aad-staff", "hi"),
                      tok=token(key=rotated, kid="rotated")).status_code == 200


def test_another_bots_endpoint_and_an_unknown_tenant_are_not_answered(teams):
    # A token for this bot, sent to an endpoint no bot has: refused.
    r = teams.send(activity("personal_message", "aad-staff", "hi"),
                   path="/api/teams/apps/nope/messages")
    assert r.status_code == 401
    # The central endpoint isn't set up here (no TEAMS_APP_ID).
    assert teams.send(activity("personal_message", "aad-staff", "hi"),
                      path="/api/teams/messages").status_code == 401
    # The right bot, a tenant this workspace doesn't answer: acknowledged, nothing queued.
    other = activity("personal_message", "aad-staff", "hi",
                     tenant="99999999-0000-0000-0000-000000000000")
    assert teams.send(other).status_code == 200 and queued() == 0


def test_an_activity_teams_sends_again_is_answered_once(teams):
    act = activity("personal_message", "aad-staff", "What is the refund policy?",
                   activity_id="1700000000999999")
    for _ in range(3):
        assert teams.send(act).status_code == 200
    assert queued() == 1


# ---- Who is asking -----------------------------------------------------------------------------
def test_members_are_answered_with_their_role_and_the_lookup_is_cached(teams):
    for _ in range(2):
        teams.send(activity("personal_message", "aad-hr", QUESTION))
    results = teams.run_jobs()
    assert [r.get("answered") for r in results] == ["personal", "personal"]
    assert len(teams.fake.calls_to("GET", "/members/")) == 1          # cached
    assert SECRET_WORDS in everything(teams.fake)                     # HR, in their 1:1 chat
    # The token for the Connector came from the bot's own tenant.
    [got] = [c for c in teams.fake.calls if "login.microsoftonline.com" in c["url"]]
    assert f"/{TENANT}/oauth2/v2.0/token" in got["url"]
    assert got["data"]["scope"] == "https://api.botframework.com/.default"


def test_a_member_is_found_by_upn_when_teams_has_no_email(teams):
    teams.fake.people["aad-upn"] = ""
    teams.fake.upns["aad-upn"] = f"hr@{teams.ws}.example"
    teams.send(activity("personal_message", "aad-upn", QUESTION))
    [result] = teams.run_jobs()
    assert result["answered"] == "personal" and SECRET_WORDS in everything(teams.fake)


@pytest.mark.parametrize("who, expect", [("aad-stranger", teams_service.UNKNOWN),
                                         ("aad-disabled", teams_service.DISABLED),
                                         ("aad-elsewhere", teams_service.UNKNOWN)])
def test_people_who_arent_members_here_are_refused_kindly(teams, who, expect):
    w = teams.ws
    if who == "aad-disabled":
        members_store.set_disabled(f"staff@{w}.example", True, w)
        teams.fake.people[who] = f"staff@{w}.example"
    if who == "aad-elsewhere":                        # a member, of another workspace
        teams.fake.people[who] = f"admin@{teams.workspace()}.example"
    teams.send(activity("personal_message", who, QUESTION))
    [result] = teams.run_jobs()
    assert result.get("refused")
    [said] = teams.fake.posted()
    assert said["text"] == expect


def test_unknown_people_are_answered_from_public_documents_when_allowed(teams):
    h = teams.session(teams.ws, "admin")
    r = teams.client.put("/api/admin/teams/settings", headers=h, json={"unknown_users": "public"})
    assert r.status_code == 200 and r.json()["unknown_users"] == "public"
    teams.send(activity("personal_message", "aad-stranger", QUESTION))
    [result] = teams.run_jobs()
    assert result["answered"] == "personal"
    posted = everything(teams.fake)
    assert PUBLIC_WORDS in posted and SECRET_WORDS not in posted
    conn = db.connect()
    owner = conn.execute("SELECT owner, role_used FROM conversations WHERE workspace = ? "
                         "ORDER BY created_at DESC", (teams.ws,)).fetchone()
    conn.close()
    assert tuple(owner) == (f"teams:{TENANT}:aad-stranger", "public")


# ---- The audience rule ----------------------------------------------------------------------
@pytest.mark.parametrize("where", ["channel_mention", "group_chat_mention"])
def test_a_channel_answer_is_public_and_hr_gets_the_rest_in_their_1to1_chat(teams, where):
    teams.send(activity(where, "aad-hr", QUESTION))
    [result] = teams.run_jobs()
    assert result["answered"] == "channel+personal"
    group = "19:"
    in_group = everything(teams.fake, group)
    assert PUBLIC_WORDS in in_group and SECRET_WORDS not in in_group
    assert teams_service.PUBLIC_NOTE in in_group
    assert "restricted" not in in_group.lower()             # nothing said about what's hidden
    [made] = [c for c in teams.fake.calls_to("POST") if c["path"].endswith("/v3/conversations")]
    assert made["json"]["members"] == [{"id": "29:aad-hr"}]
    assert made["json"]["channelData"] == {"tenant": {"id": TENANT}}
    personal = everything(teams.fake, "a:1personal-new-")
    assert SECRET_WORDS in personal
    conn = db.connect()
    roles = [r[0] for r in conn.execute("SELECT role_used FROM conversations WHERE workspace = ? "
                                        "AND owner = ? ORDER BY created_at",
                                        (teams.ws, f"hr@{teams.ws}.example"))]
    conn.close()
    assert roles == ["public", "hr"]


def test_the_known_1to1_chat_is_used_for_the_private_answer(teams):
    teams.send(activity("bot_installed", "aad-hr"))                 # the app added personally
    teams.send(activity("channel_mention", "aad-hr", QUESTION))
    [result] = teams.run_jobs()
    assert result["answered"] == "channel+personal"
    assert not [c for c in teams.fake.calls_to("POST") if c["path"].endswith("/v3/conversations")]
    assert SECRET_WORDS in everything(teams.fake, "a:1personal-aad-hr")


def test_when_teams_wont_let_the_bot_write_to_them_the_channel_note_is_all(teams):
    teams.fake.fail["POST /v3/conversations"] = 403
    teams.send(activity("channel_mention", "aad-hr", QUESTION))
    [result] = teams.run_jobs()
    assert result == {**result, "answered": "channel", "personal": "unavailable"}
    assert SECRET_WORDS not in everything(teams.fake)


def test_a_public_member_in_a_channel_gets_nothing_private(teams):
    teams.send(activity("channel_mention", "aad-staff", QUESTION))
    [result] = teams.run_jobs()
    assert result["answered"] == "channel"
    assert not teams.fake.calls_to("POST", "/v3/conversations/a:1personal")
    assert SECRET_WORDS not in everything(teams.fake)


def test_the_bots_mention_is_removed_from_the_question():
    act = activity("channel_mention", "aad-x", "What is   the <b>refund</b> policy?")
    assert teams_service.question_of(act) == "What is the refund policy?"


# ---- The card ------------------------------------------------------------------------------------
def test_the_answer_is_an_adaptive_card_with_sources_and_feedback(teams):
    teams.send(activity("personal_message", "aad-hr", QUESTION))
    teams.run_jobs()
    working, *_ = teams.fake.posted()
    assert working["text"] == teams_service.WORKING and working["replyToId"]
    [update] = teams.fake.calls_to("PUT", "/activities/")
    card = FakeTeams.card(update["json"])
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.5"
    text = json.dumps(card)
    assert "[S1]" not in text and "[[1]](" in text                  # markers as numbered links
    assert "Sources" in text
    verbs = [a for a in card["actions"] if a["type"] == "Action.Execute"]
    assert [a["data"]["rating"] for a in verbs] == [1, -1]
    assert {a["verb"] for a in verbs} == {"kb_feedback"}
    assert any(a["type"] == "Action.OpenUrl" and a["url"].startswith("https://kb.example.com/chat/")
               for a in card["actions"])
    assert len(json.dumps(update["json"]).encode()) < 100_000


def test_card_markdown_links_sources_and_drops_unknown_markers():
    sources = [{"n": 1, "title": "Guide", "url": "https://docs.example/guide"},
               {"n": 2, "title": "Policy", "chunk_id": "c 9"}]
    md = teams_service.card_markdown("# Refunds\nWithin 30 days [S1] or ask [S2][S7].",
                                     sources, "https://kb.example.com")
    assert md == ("**Refunds**\nWithin 30 days [[1]](https://docs.example/guide) or ask "
                  "[[2]](https://kb.example.com/passages/c%209).")


def test_feedback_from_the_card_lands_in_message_feedback(teams):
    teams.send(activity("personal_message", "aad-staff", "What is the refund policy?"))
    teams.run_jobs()
    card = FakeTeams.card(teams.fake.calls_to("PUT", "/activities/")[-1]["json"])
    message_id = card["actions"][0]["data"]["message_id"]
    r = teams.send(activity("feedback_invoke", "aad-staff", message_id=message_id, rating=-1))
    assert r.status_code == 200
    body = r.json()
    assert body["statusCode"] == 200 and body["type"] == "application/vnd.microsoft.activity.message"
    assert body["value"] == teams_service.THANKS[-1]
    conn = db.connect()
    row = conn.execute("SELECT user, rating FROM message_feedback WHERE message_id = ?",
                       (message_id,)).fetchone()
    conn.close()
    assert tuple(row) == (f"staff@{teams.ws}.example", -1)
    assert _events(teams.ws, "feedback.set")[-1]["details"]["surface"] == "teams"
    # An older client's Action.Submit: a message with a value, taken the same way.
    teams.send(activity("feedback_submit", "aad-staff", message_id=message_id, rating=1))
    [result] = teams.run_jobs()
    assert result["rating"] == 1


def test_feedback_on_an_answer_the_clicker_cant_see_is_refused(teams):
    teams.send(activity("personal_message", "aad-hr", QUESTION))
    teams.run_jobs()
    card = FakeTeams.card(teams.fake.calls_to("PUT", "/activities/")[-1]["json"])
    message_id = card["actions"][0]["data"]["message_id"]
    r = teams.send(activity("feedback_invoke", "aad-staff", message_id=message_id))
    assert r.json()["value"] == "That answer isn't available to you any more."


def test_questions_are_logged_with_surface_teams_and_audited(teams):
    teams.send(activity("channel_mention", "aad-staff", "What is the refund policy?"))
    teams.run_jobs()
    conn = db.connect()
    logged = conn.execute("SELECT surface, user, role FROM question_log WHERE workspace = ?",
                          (teams.ws,)).fetchall()
    conn.close()
    assert [tuple(r) for r in logged] == [("teams", f"staff@{teams.ws}.example", "public")]
    [asked] = _events(teams.ws, "teams.ask")
    assert asked["details"]["surface"] == "teams" and asked["details"]["audience"] == "public"
    assert "refund" not in json.dumps(asked["details"])               # never the question


def test_a_follow_up_in_the_1to1_chat_continues_the_conversation(teams):
    teams.send(activity("personal_message", "aad-staff", "What is the refund policy?"))
    teams.send(activity("personal_message", "aad-staff", "And for annual plans?"))
    teams.run_jobs()
    conn = db.connect()
    count = conn.execute("SELECT COUNT(*) FROM conversations WHERE workspace = ? AND owner = ?",
                         (teams.ws, f"staff@{teams.ws}.example")).fetchone()[0]
    conn.close()
    assert count == 1


def test_teams_requests_are_rate_limited_per_person(teams, monkeypatch):
    monkeypatch.setenv("RATE_LIMITS", "on")
    monkeypatch.setenv("RATE_LIMIT_TEAMS_USER", "2")
    state.hits.clear()
    for i in range(4):
        teams.send(activity("personal_message", "aad-staff", f"question {i}"))
    assert queued() == 2
    state.hits.clear()


# ---- Setting it up ------------------------------------------------------------------------------
def test_setting_up_is_admin_only_audited_and_never_shows_the_secret(teams):
    h = teams.session(teams.ws, "admin")
    view = teams.client.get("/api/admin/teams", headers=h).json()
    assert view["status"] == "configured" and view["mode"] == "custom"
    assert view["messaging_endpoint"].startswith("https://kb.example.com/api/teams/apps/")
    assert view["bot_app_id"] == BOT and view["has_secret"] is True
    hr = teams.session(teams.ws, "hr")
    assert teams.client.get("/api/admin/teams", headers=hr).json()["can_manage"] is False
    for method, path, body in (("put", "/api/admin/teams/app", {"mode": "custom"}),
                               ("put", "/api/admin/teams/settings", {"unknown_users": "public"}),
                               ("delete", "/api/admin/teams", None),
                               ("get", "/api/admin/teams/package", None)):
        r = getattr(teams.client, method)(path, headers=hr, **({"json": body} if body else {}))
        assert r.status_code == 403, path
    new_secret = "another-secret-value~2345"
    r = teams.client.put("/api/admin/teams/app", headers=h, json={
        "mode": "custom", "app_id": BOT, "app_tenant": TENANT, "client_secret": new_secret})
    assert r.status_code == 200, r.text
    assert teams_store.app_secret(teams.ws) == new_secret
    conn = db.connect()
    stored = conn.execute("SELECT app_secret FROM teams_installs WHERE workspace = ?",
                          (teams.ws,)).fetchone()[0]
    conn.close()
    assert bytes(stored).startswith(b"kbw1:")         # under the workspace's own data key
    assert new_secret.encode() not in bytes(stored)
    # Left out, the secret of the same bot is kept.
    r = teams.client.put("/api/admin/teams/app", headers=h, json={
        "mode": "custom", "app_id": BOT, "app_tenant": TENANT, "client_secret": ""})
    assert r.status_code == 200 and teams_store.app_secret(teams.ws) == new_secret
    shown = json.dumps(teams.client.get("/api/admin/teams", headers=h).json())
    logged = json.dumps(_events(teams.ws))
    for secret in (BOT_SECRET, new_secret):
        assert secret not in shown and secret not in logged
    assert _events(teams.ws, "teams.app")[-1]["details"]["tenant"] == TENANT


@pytest.mark.parametrize("body, error", [
    ({"mode": "custom", "app_id": "nope", "app_tenant": TENANT, "client_secret": "x" * 20},
     "Microsoft App ID"),
    ({"mode": "custom", "app_id": BOT, "app_tenant": "nope", "client_secret": "x" * 20},
     "tenant ID"),
    ({"mode": "custom", "app_id": "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee", "app_tenant": TENANT,
      "client_secret": "short"}, "client secret"),
    ({"mode": "central", "tenant_id": TENANT}, "no Teams bot of its own"),
])
def test_bad_settings_are_refused_with_a_reason(teams, body, error):
    r = teams.client.put("/api/admin/teams/app", headers=teams.session(teams.ws, "admin"),
                         json=body)
    assert r.status_code == 400 and error in r.json()["detail"]


def test_a_tenant_is_answered_by_one_workspace(teams):
    other = teams.workspace()
    r = teams.client.put("/api/admin/teams/app", headers=teams.session(other, "admin"), json={
        "mode": "custom", "app_id": "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee", "app_tenant": TENANT,
        "client_secret": "x" * 20})
    assert r.status_code == 409


def test_the_central_bot_answers_the_tenant_an_admin_names(teams, monkeypatch):
    monkeypatch.setenv("TEAMS_APP_ID", BOT)
    monkeypatch.setenv("TEAMS_APP_PASSWORD", BOT_SECRET)
    monkeypatch.setenv("TEAMS_APP_TENANT", TENANT)
    h = teams.session(teams.ws, "admin")
    r = teams.client.put("/api/admin/teams/app", headers=h,
                         json={"mode": "central", "tenant_id": TENANT.upper()})
    assert r.status_code == 200, r.text
    assert r.json()["messaging_endpoint"] == "https://kb.example.com/api/teams/messages"
    assert teams_store.app_secret(teams.ws) is None
    act = activity("personal_message", "aad-staff", "What is the refund policy?")
    assert teams.send(act, path="/api/teams/messages").status_code == 200
    assert teams.send(act, path=teams.path).status_code == 401       # the old bot's endpoint
    [result] = teams.run_jobs()
    assert result["answered"] == "personal"


def test_the_app_package_is_the_manifest_with_this_bot_and_host(teams):
    r = teams.client.get("/api/admin/teams/package", headers=teams.session(teams.ws, "admin"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["color.png", "manifest.json", "outline.png"]
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["bots"][0]["botId"] == BOT and manifest["id"] == BOT
    assert set(manifest["bots"][0]["scopes"]) == {"personal", "team", "groupChat"}
    assert manifest["validDomains"] == ["kb.example.com"]
    assert manifest["developer"]["websiteUrl"] == "https://kb.example.com"
    assert z.read("color.png")[:8] == b"\x89PNG\r\n\x1a\n"


def test_disconnecting_forgets_the_bot_and_stops_answering(teams):
    h = teams.session(teams.ws, "admin")
    r = teams.client.delete("/api/admin/teams", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "not_configured"
    assert teams_store.get(teams.ws) is None
    assert teams.send(activity("personal_message", "aad-staff", "hi")).status_code == 401
    assert _events(teams.ws, "teams.disconnect")
