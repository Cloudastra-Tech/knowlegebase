"""
Linear against recorded HTTP (tests/fixtures/connectors/linear/, written from
Linear's GraphQL schema and developer docs; an unrecorded request fails the
test), through the real framework (kb/services/sync.py): the OAuth connect
flow, the rotating refresh token (refreshed early, and on a 401), the team
picker, Relay paging, issues / projects / documents / project updates as
Markdown, the
updatedAt watermark, archived and trashed items deleted, deletes behind the
mass-delete guard, team membership into acl, 429s and typed errors.
"""

import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

G = "https://api.linear.app/graphql"
TOKEN = "https://api.linear.app/oauth/token"
TEAMS = [{"id": "team-eng", "name": "Engineering (ENG)", "kind": "team"}]


@pytest.fixture(autouse=True)
def app(monkeypatch):
    monkeypatch.setenv("LINEAR_CLIENT_ID", "lin-client")
    monkeypatch.setenv("LINEAR_CLIENT_SECRET", "lin-secret")


def connect(env, teams=TEAMS, **secret):
    import integrations_store
    integrations_store.connect(env.ws, "linear", "Acme (ann@acme.example)", {
        "access_token": "lin-at-1", "refresh_token": "lin-rt-1",
        "expires_at": time.time() + 86000, "renew_at": time.time() + 60000, **secret}, "admin")
    integrations_store.update(env.ws, "linear", settings={"organization": "acme",
                                                          "teams": list(teams)})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "linear", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "linear")


def gql(rec, data=None, status=200, headers=None, errors=None, query="", repeat=False):
    body = {"data": data} if errors is None else {"errors": errors}
    rec.add("POST", G, status=status, json_body=body, headers=headers,
            match_json={"query~": query} if query else None, repeat=repeat)


# ---- connecting ------------------------------------------------------------------------
def test_connect_asks_for_read_and_keeps_the_refresh_token(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    headers = env.headers()
    url = env.client.post("/api/integrations/linear/connect", headers=headers).json()["url"]
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.netloc, parts.path) == ("linear.app", "/oauth/authorize")
    assert (q["client_id"], q["response_type"], q["scope"], q["actor"]) == (
        "lin-client", "code", "read", "user")
    assert q["redirect_uri"].endswith("/api/integrations/linear/callback")
    rec.add("POST", TOKEN, json_body={"access_token": "lin-at-1", "token_type": "Bearer",
                                      "expires_in": 86399, "scope": "read",
                                      "refresh_token": "lin-rt-1"})
    gql(rec, {"viewer": {"name": "Ann", "email": "ann@acme.example",
                         "organization": {"name": "Acme", "urlKey": "acme"}}}, query="viewer")
    r = env.client.get("/api/integrations/linear/callback", follow_redirects=False,
                       params={"state": q["state"], "code": "abc"})
    assert r.headers["location"] == "/#integrations?connected=linear&choose=1"
    token_call = rec.calls[0]
    assert token_call["data"]["grant_type"] == "authorization_code"
    assert token_call["data"]["client_secret"] == "lin-secret"
    sec = integrations_store.secret(env.ws, "linear")
    assert (sec["access_token"], sec["refresh_token"]) == ("lin-at-1", "lin-rt-1")
    assert integrations_store.get(env.ws, "linear")["account"] == "Acme (ann@acme.example)"


def test_no_refresh_token_is_refused(env, monkeypatch):
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/linear/connect", headers=env.headers()).json()["url"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
    rec.add("POST", TOKEN, json_body={"access_token": "a", "expires_in": 86399})
    r = env.client.get("/api/integrations/linear/callback", follow_redirects=False,
                       params={"state": state, "code": "abc"})
    assert r.headers["location"] == "/#integrations?error=no_refresh_token"


def test_the_rotating_refresh_token_is_saved_before_use(env, monkeypatch):
    import integrations_store
    from connectors import linear
    rec = Recorded(monkeypatch)
    connect(env, renew_at=time.time() - 1)                  # past 80 % of its 24 hours
    rec.add("POST", TOKEN, json_body={"access_token": "lin-at-2", "refresh_token": "lin-rt-2",
                                      "expires_in": 86399})
    assert linear.CONNECTOR._access_token(env.ws) == "lin-at-2"
    sec = integrations_store.secret(env.ws, "linear")
    assert (sec["refresh_token"], rec.calls[0]["data"]["refresh_token"]) == ("lin-rt-2",
                                                                             "lin-rt-1")
    # A 401 on a query: refreshed once (with the NEW refresh token), then retried.
    gql(rec, status=401, errors=[{"message": "Authentication required",
                                  "extensions": {"code": "AUTHENTICATION_ERROR"}}])
    rec.add("POST", TOKEN, json_body={"access_token": "lin-at-3", "refresh_token": "lin-rt-3",
                                      "expires_in": 86399})
    gql(rec, {"teams": {"nodes": [], "pageInfo": {"hasNextPage": False}}})
    ctx = base.SyncContext(workspace=env.ws, provider="linear", settings={}, known={})
    assert linear.CONNECTOR.list_containers(ctx) == []
    assert rec.calls[2]["data"]["refresh_token"] == "lin-rt-2"
    assert rec.calls[3]["headers"]["Authorization"] == "Bearer lin-at-3"
    # A revoked grant: reconnect.
    integrations_store.update(env.ws, "linear", secret={**integrations_store.secret(
        env.ws, "linear"), "renew_at": time.time() - 1})
    rec.add("POST", TOKEN, status=400, json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        linear.CONNECTOR._access_token(env.ws)


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_lists_every_page_of_teams(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect(env)
    gql(rec, {"teams": {"nodes": [{"id": "t2", "key": "SAL", "name": "Sales"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "tc"}}})
    gql(rec, {"teams": {"nodes": [{"id": "team-eng", "key": "ENG", "name": "Engineering"}],
                        "pageInfo": {"hasNextPage": False}}})
    r = env.client.get("/api/integrations/linear/containers", headers=env.headers())
    assert r.json()["items"] == [{"id": "team-eng", "name": "Engineering (ENG)", "kind": "team"},
                                 {"id": "t2", "name": "Sales (SAL)", "kind": "team"}]
    assert rec.calls[1]["json"]["variables"] == {"after": "tc"}
    r = env.client.put("/api/integrations/linear/containers", headers=env.headers(),
                       json={"containers": [{"id": "x", "name": "x", "kind": "folder"}]})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_renders_issues_projects_documents_and_updates_with_team_acls(
        env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "linear/full")
    connect(env)
    summary = sync.sync(env.ws, "linear")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 5)
    first = [c for c in rec.calls if "issues(" in c["json"]["query"]][0]
    assert first["json"]["variables"]["filter"] == {"team": {"id": {"in": ["team-eng"]}}}
    issue = item(env, "issue:iss-1")
    assert issue["title"] == "ENG-1: Rotate the API keys"
    assert issue["url"] == "https://linear.app/acme/issue/ENG-1"
    assert issue["acl"] == {"via": "linear", "team": "team-eng", "teams": ["team-eng"],
                            "users": ["ann@acme.example", "bob@acme.example"]}
    text = env.text("linear", issue["file_name"])
    assert "Status: In Progress" in text and "**every 90 days**" in text
    assert text.index("Prod next week.") < text.index("Done for staging.")   # oldest first
    guide = env.text("linear", item(env, "issue:iss-2")["file_name"])
    assert "Reviewed." in guide                           # the second page of comments
    assert item(env, "issue:iss-3") is None               # in the trash
    project = item(env, "project:proj-1")
    assert "One place for policies." in env.text("linear", project["file_name"])
    assert project["acl"]["teams"] == ["team-eng"]        # the picked team of its two
    assert item(env, "project:proj-2") is None            # another team's only
    assert "Deploy" in env.text("linear", item(env, "document:doc-1")["file_name"])
    assert item(env, "document:doc-2") is None
    update = item(env, "update:upd-1")
    assert update["title"] == "Handbook: update of 2026-10-08"
    assert update["acl"]["teams"] == ["team-eng"]         # its project's picked team
    text = env.text("linear", update["file_name"])
    assert "Health: At risk" in text and "**legal review**" in text
    assert item(env, "update:upd-2") is None              # another team's project


def test_then_only_what_changed_since_the_watermark_and_archives_are_deleted(env,
                                                                            monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "linear/full")
    connect(env)
    sync.sync(env.ws, "linear")
    assert json.loads(sync_store.state(env.ws, "linear")["cursor"])["watermark"] == (
        "2026-10-10T10:00:00.000Z")
    rec.load("linear/incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "linear")
    assert rec.unused() == []
    issues = [c for c in rec.calls[before:] if "issues(" in c["json"]["query"]][0]
    assert issues["json"]["variables"]["filter"] == {
        "team": {"id": {"in": ["team-eng"]}}, "updatedAt": {"gt": "2026-10-10T09:58:00Z"}}
    assert (summary["mode"], summary["upserted"], summary["deleted"]) == ("incremental", 2, 2)
    assert "every 30 days" in env.text("linear", item(env, "issue:iss-1")["file_name"])
    assert item(env, "issue:iss-2") is None               # archived
    assert item(env, "update:upd-1") is None              # archived
    assert "Test" in env.text("linear", item(env, "document:doc-1")["file_name"])


def test_a_reconcile_that_lists_nothing_is_held_for_an_admin(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "linear/full")
    connect(env)
    sync.sync(env.ws, "linear")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    empty = {"nodes": [], "pageInfo": {"hasNextPage": False}}
    for name in ("issues", "projects", "documents", "projectUpdates"):
        gql(rec, {name: empty}, query=f"{name}(", repeat=True)
    summary = sync.sync(env.ws, "linear", full=True)
    assert (summary["held_deletes"], summary["deleted"]) == (5, 0)
    assert card(env)["held_deletes"] == 5
    assert sync.sync(env.ws, "linear", full=True, confirm_deletes=True)["deleted"] == 5


def test_a_429_waits_for_retry_after_and_forbidden_is_typed(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    gql(rec, status=429, headers={"Retry-After": "17"},
        errors=[{"message": "Rate limit exceeded", "extensions": {"code": "RATELIMITED"}}])
    rec.load("linear/full")
    sync.sync(env.ws, "linear")
    assert 17 in rec.slept
    gql(rec, errors=[{"message": "no", "extensions": {"code": "FORBIDDEN"}}])
    with pytest.raises(base.Forbidden):
        sync.sync(env.ws, "linear", full=True)
    gql(rec, status=401, errors=[{"message": "x", "extensions": {"code": "AUTHENTICATION_ERROR"}}])
    rec.add("POST", TOKEN, status=400, json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "linear", full=True)
    assert card(env)["error"]["code"] == "auth_expired"


def test_nothing_is_synced_until_a_team_is_picked(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env, teams=[])
    assert not sync.ready_to_sync(env.ws, "linear")
    sync.sync(env.ws, "linear")
    assert rec.calls == []


def test_disconnecting_revokes_the_refresh_token(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("POST", "https://api.linear.app/oauth/revoke")
    assert env.client.delete("/api/integrations/linear", headers=env.headers()).status_code == 200
    assert rec.calls[0]["data"] == {"token": "lin-rt-1", "token_type_hint": "refresh_token"}
    assert integrations_store.get(env.ws, "linear") is None


def test_linear_is_live_in_the_registry(env):
    c = card(env)
    assert (c["availability"], c["acl"], c["picker"], c["status"]) == (
        "live", True, "list", "disconnected")
    assert "source" in c["policies"]
