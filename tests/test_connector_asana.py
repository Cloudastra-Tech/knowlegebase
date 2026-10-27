"""
Asana against recorded HTTP (tests/fixtures/connectors/asana/, written from
Asana's API reference by make_work_tools.py; an unrecorded request fails the
test), through the real framework (kb/services/sync.py): the OAuth connect
flow and the one-hour token refreshed early and on a 401, the workspace /
project picker, offset paging, tasks with comments and project briefs as
Markdown, modified_since and the events API (deletes, an expired sync token),
deletes behind the mass-delete guard, project membership into acl (failing
closed), 429s and typed errors.
"""

import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

A = "https://app.asana.com/api/1.0"
TOKEN = "https://app.asana.com/-/oauth_token"
PICKS = [{"id": "project-111", "name": "Handbook", "kind": "project"}]


@pytest.fixture(autouse=True)
def app(monkeypatch):
    monkeypatch.setenv("ASANA_CLIENT_ID", "as-client")
    monkeypatch.setenv("ASANA_CLIENT_SECRET", "as-secret")


def connect(env, picks=PICKS, **secret):
    import integrations_store
    integrations_store.connect(env.ws, "asana", "ann@acme.example", {
        "access_token": "as-at-1", "refresh_token": "as-rt-1",
        "expires_at": time.time() + 3000, "renew_at": time.time() + 2000, **secret}, "admin")
    integrations_store.update(env.ws, "asana", settings={"user": "u1", "containers": list(picks)})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "asana", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "asana")


# ---- connecting ------------------------------------------------------------------------
def test_connect_signs_in_and_keeps_the_refresh_token(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/asana/connect", headers=env.headers()).json()["url"]
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.netloc, parts.path) == ("app.asana.com", "/-/oauth_authorize")
    assert (q["client_id"], q["response_type"], q["scope"]) == ("as-client", "code", "default")
    assert q["redirect_uri"].endswith("/api/integrations/asana/callback")
    rec.add("POST", TOKEN, json_body={"access_token": "as-at-1", "refresh_token": "as-rt-1",
                                      "expires_in": 3600, "token_type": "bearer",
                                      "data": {"gid": "u1", "name": "Ann",
                                               "email": "ann@acme.example"}})
    r = env.client.get("/api/integrations/asana/callback", follow_redirects=False,
                       params={"state": q["state"], "code": "abc"})
    assert r.headers["location"] == "/#integrations?connected=asana&choose=1"
    assert rec.calls[0]["data"]["grant_type"] == "authorization_code"
    sec = integrations_store.secret(env.ws, "asana")
    assert (sec["access_token"], sec["refresh_token"]) == ("as-at-1", "as-rt-1")
    assert integrations_store.get(env.ws, "asana")["account"] == "ann@acme.example"


def test_the_token_is_refreshed_early_and_on_a_401_and_a_revoked_grant_is_typed(env,
                                                                               monkeypatch):
    import integrations_store
    from connectors import asana
    rec = Recorded(monkeypatch)
    connect(env, renew_at=time.time() - 1)                 # past 80 % of its hour
    rec.add("POST", TOKEN, json_body={"access_token": "as-at-2", "expires_in": 3600})
    assert asana.CONNECTOR._access_token(env.ws) == "as-at-2"
    sec = integrations_store.secret(env.ws, "asana")
    assert sec["refresh_token"] == "as-rt-1"               # Asana sent none: the old one stays
    assert rec.calls[0]["data"] == {"grant_type": "refresh_token", "refresh_token": "as-rt-1",
                                    "client_id": "as-client", "client_secret": "as-secret"}
    rec.add("GET", f"{A}/workspaces", status=401, json_body={"errors": [{"message": "x"}]})
    rec.add("POST", TOKEN, json_body={"access_token": "as-at-3", "expires_in": 3600})
    rec.add("GET", f"{A}/workspaces", json_body={"data": [{"gid": "1", "name": "Acme"}],
                                                 "next_page": None})
    ctx = base.SyncContext(workspace=env.ws, provider="asana", settings={}, known={})
    assert asana.CONNECTOR.list_containers(ctx) == [
        {"id": "workspace-1", "name": "Acme", "kind": "category", "has_children": True}]
    assert rec.calls[-1]["headers"]["Authorization"] == "Bearer as-at-3"
    integrations_store.update(env.ws, "asana", secret={**integrations_store.secret(
        env.ws, "asana"), "renew_at": time.time() - 1})
    rec.add("POST", TOKEN, status=400, json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        asana.CONNECTOR._access_token(env.ws)


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_lists_workspaces_then_their_projects(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect(env, picks=[])
    rec.add("GET", f"{A}/projects", params={"workspace": "1", "archived": "false"},
            json_body={"data": [{"gid": "112", "name": "Zeta"}],
                       "next_page": {"offset": "o2"}})
    rec.add("GET", f"{A}/projects", params={"offset": "o2"},
            json_body={"data": [{"gid": "111", "name": "Handbook"}], "next_page": None})
    r = env.client.get("/api/integrations/asana/containers?parent=workspace-1",
                       headers=env.headers())
    assert [i["id"] for i in r.json()["items"]] == ["project-111", "project-112"]
    r = env.client.put("/api/integrations/asana/containers", headers=env.headers(),
                       json={"containers": [{"id": "folder-x", "name": "x"}]})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_renders_tasks_comments_and_the_brief_with_member_acls(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "asana/full")
    connect(env)
    summary = sync.sync(env.ws, "asana")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 3)
    task = item(env, "task-t1")
    assert task["title"] == "Rotate the API keys"
    assert task["url"] == "https://app.asana.com/0/111/t1"
    # Ann's email was read; Bob's couldn't be (left out), the team isn't expanded.
    assert task["acl"] == {"via": "asana", "project": "111", "users": ["ann@acme.example"]}
    text = env.text("asana", task["file_name"])
    assert "Section: Security" in text and "Assignee: Ann" in text
    assert "**every 90 days**" in text
    assert text.index("Prod next week.") < text.index("Done for staging.")
    assert "assigned" not in text                          # only comments, not activity
    assert "Status: Completed" in env.text("asana", item(env, "task-t2")["file_name"])
    brief = env.text("asana", item(env, "brief-111")["file_name"])
    assert "Where the team keeps its policies." in brief and "One place for every policy." in brief
    state = json.loads(__import__("sync_store").state(env.ws, "asana")["cursor"])
    assert state["events"] == {"111": "ev-1"}


def test_then_modified_since_and_the_events_api_delete_what_was_removed(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "asana/full")
    connect(env)
    sync.sync(env.ws, "asana")
    rec.load("asana/incremental")
    summary = sync.sync(env.ws, "asana")
    assert rec.unused() == []
    assert (summary["mode"], summary["deleted"]) == ("incremental", 1)
    assert item(env, "task-t2") is None                    # deleted in Asana (events)
    assert "every 30 days" in env.text("asana", item(env, "task-t1")["file_name"])


def test_an_expired_events_token_lists_everything_again(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "asana/full")
    connect(env)
    sync.sync(env.ws, "asana")
    rec.add("GET", f"{A}/events", status=412, params={"sync": "ev-1"},
            json_body={"sync": "ev-9", "errors": [{"message": "too old"}]})
    rec.load("asana/full")
    summary = sync.sync(env.ws, "asana")
    assert summary["mode"] == "full"
    assert json.loads(sync_store.state(env.ws, "asana")["cursor"])["events"] == {"111": "ev-1"}


def test_a_full_listing_that_lost_everything_is_held_for_an_admin(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "asana/full")
    connect(env)
    sync.sync(env.ws, "asana")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.add("GET", f"{A}/events", status=412, json_body={"sync": "ev-3"})
    rec.add("GET", f"{A}/projects/111", json_body={"data": dict(
        {"gid": "111", "name": "Handbook", "archived": False})})
    rec.add("GET", f"{A}/memberships", json_body={"data": [], "next_page": None})
    rec.add("GET", f"{A}/tasks", json_body={"data": [], "next_page": None})
    summary = sync.sync(env.ws, "asana", full=True)
    assert (summary["held_deletes"], summary["deleted"]) == (3, 0)
    assert card(env)["held_deletes"] == 3


def test_membership_maps_onto_members_and_fails_closed(env):
    import access
    import members_store
    import auth
    from kb.services import permissions
    members_store.set_password("ann@acme.example", "Ann", auth.hash_password("x" * 12),
                               env.ws, "public")
    r = permissions.Resolver(env.ws)
    assert r.resolve({"via": "asana", "project": "1", "users": ["ann@acme.example",
                                                                "stranger@x.example"]}) == (
        [access.user("ann@acme.example")], 1)
    assert r.resolve({"via": "asana", "project": "1", "partial": True}) == ([access.NOBODY], 1)
    assert r.resolve({"via": "asana", "users": ["ann@acme.example"]}) == ([access.NOBODY], 1)


def test_a_429_waits_for_retry_after_and_forbidden_is_typed(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", f"{A}/events", status=429, headers={"Retry-After": "11"})
    rec.load("asana/full")
    sync.sync(env.ws, "asana")
    assert 11 in rec.slept
    rec.add("GET", f"{A}/events", status=412, json_body={"sync": "ev-5"})
    rec.add("GET", f"{A}/projects/111", status=403, json_body={"errors": [{"message": "no"}]})
    with pytest.raises(base.Forbidden):
        sync.sync(env.ws, "asana", full=True)
    assert card(env)["error"]["code"] == "forbidden"


def test_nothing_is_synced_until_something_is_picked_and_disconnect_revokes(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env, picks=[])
    assert not sync.ready_to_sync(env.ws, "asana")
    sync.sync(env.ws, "asana")
    assert rec.calls == []
    rec.add("POST", "https://app.asana.com/-/oauth_revoke")
    assert env.client.delete("/api/integrations/asana", headers=env.headers()).status_code == 200
    assert rec.calls[0]["data"]["token"] == "as-rt-1"
    assert integrations_store.get(env.ws, "asana") is None


def test_asana_is_live_with_the_source_policy(env):
    c = card(env)
    assert (c["availability"], c["acl"], c["picker"], c["status"]) == (
        "live", True, "tree", "disconnected")
