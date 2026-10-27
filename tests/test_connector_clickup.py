"""
ClickUp against recorded HTTP (tests/fixtures/connectors/clickup/, written from
ClickUp's API reference by make_work_tools.py; an unrecorded request fails the
test), through the real framework (kb/services/sync.py): the OAuth connect flow
(no refresh: ClickUp's tokens don't expire), the Workspace / Space / Folder /
List picker, page-numbered tasks and cursor-paged Docs, comments oldest first,
date_updated increments, a deleted Doc, deletes behind the mass-delete guard,
List membership into acl (failing closed where ClickUp can't say), 429s and
typed errors.
"""

import json
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

C = "https://api.clickup.com/api"
PICKS = [{"id": "folder-f1", "name": "Runbooks", "kind": "folder"}]


@pytest.fixture(autouse=True)
def app(monkeypatch):
    monkeypatch.setenv("CLICKUP_CLIENT_ID", "cu-client")
    monkeypatch.setenv("CLICKUP_CLIENT_SECRET", "cu-secret")


def connect(env, picks=PICKS):
    import integrations_store
    integrations_store.connect(env.ws, "clickup", "Acme (ann@acme.example)",
                               {"access_token": "cu-token"}, "admin")
    integrations_store.update(env.ws, "clickup", settings={"teams": ["900"],
                                                           "containers": list(picks)})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "clickup", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "clickup")


def test_connect_exchanges_the_code_and_names_the_workspaces(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/clickup/connect",
                          headers=env.headers()).json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith("https://app.clickup.com/api?") and q["client_id"] == "cu-client"
    rec.add("POST", f"{C}/v2/oauth/token", json_body={"access_token": "cu-token"},
            match_json={"client_id": "cu-client", "client_secret": "cu-secret", "code": "abc"})
    rec.add("GET", f"{C}/v2/user", json_body={"user": {"id": 1, "username": "Ann",
                                                       "email": "ann@acme.example"}})
    rec.add("GET", f"{C}/v2/team", json_body={"teams": [{"id": "900", "name": "Acme"}]})
    r = env.client.get("/api/integrations/clickup/callback", follow_redirects=False,
                       params={"state": q["state"], "code": "abc"})
    assert r.headers["location"] == "/#integrations?connected=clickup&choose=1"
    row = integrations_store.get(env.ws, "clickup")
    assert (row["account"], row["settings"]["teams"]) == ("Acme (ann@acme.example)", ["900"])
    assert rec.calls[1]["headers"]["Authorization"] == "Bearer cu-token"


def test_a_refused_code_and_a_revoked_token_are_typed(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/clickup/connect",
                          headers=env.headers()).json()["url"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
    rec.add("POST", f"{C}/v2/oauth/token", status=400, json_body={"err": "Code invalid",
                                                                  "ECODE": "OAUTH_014"})
    r = env.client.get("/api/integrations/clickup/callback", follow_redirects=False,
                       params={"state": state, "code": "bad"})
    assert r.headers["location"] == "/#integrations?error=connect_failed"
    connect(env)
    rec.add("GET", f"{C}/v2/team", status=401, json_body={"err": "Token invalid",
                                                          "ECODE": "OAUTH_025"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "clickup")
    assert card(env)["error"]["code"] == "auth_expired"


def test_the_picker_walks_spaces_folders_and_lists(env, monkeypatch):
    rec = Recorded(monkeypatch, "clickup/full")
    connect(env, picks=[])
    get = lambda parent: [(i["id"], i["kind"]) for i in env.client.get(  # noqa: E731
        f"/api/integrations/clickup/containers?parent={parent}",
        headers=env.headers()).json()["items"]]
    assert get("workspace-900") == [("space-s1", "space"), ("space-s2", "space")]
    assert get("space-s1") == [("folder-f1", "folder"), ("list-l2", "project")]
    assert rec.calls[-1]["headers"]["Authorization"] == "Bearer cu-token"
    r = env.client.put("/api/integrations/clickup/containers", headers=env.headers(),
                       json={"containers": [{"id": "page-1", "name": "x"}]})
    assert r.status_code == 400


def test_a_full_sync_renders_tasks_and_docs_with_list_acls(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "clickup/full")
    connect(env)
    summary = sync.sync(env.ws, "clickup")
    assert [u for u in rec.unused()] == []
    assert (summary["mode"], summary["upserted"]) == ("full", 4)
    query = rec.requests_to("/team/900/task")[0]["params"]
    assert (query["list_ids[]"], query["include_closed"], query["order_by"]) == (
        "l1", "true", "updated")
    task = item(env, "task-86a1")
    assert task["acl"] == {"via": "clickup", "list": "l1",
                           "users": ["ann@acme.example", "bob@acme.example"]}
    text = env.text("clickup", task["file_name"])
    assert "List: On-call" in text and "Run `make restart`." in text
    assert text.index("Needs sudo.") < text.index("Done twice this week.")   # oldest first
    doc = env.text("clickup", item(env, "doc-d1")["file_name"])
    assert "**secondary**" in doc and "Then the manager." in doc
    assert item(env, "doc-d1")["acl"]["list"] == "l1"     # a Doc on a List: its members
    assert item(env, "doc-d2") is None                    # not under the picks
    assert item(env, "doc-d3")["acl"] == {"via": "clickup", "list": None, "partial": True}


def test_then_date_updated_and_a_deleted_doc(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "clickup/full")
    connect(env)
    sync.sync(env.ws, "clickup")
    since = json.loads(sync_store.state(env.ws, "clickup")["cursor"])["since"]
    rec.load("clickup/incremental")
    summary = sync.sync(env.ws, "clickup")
    assert (summary["mode"], summary["deleted"]) == ("incremental", 1)
    query = [c for c in rec.requests_to("/team/900/task")][-1]["params"]
    assert int(query["date_updated_gt"]) == since - 120_000
    assert item(env, "doc-d3") is None
    assert "make restart-all" in env.text("clickup", item(env, "task-86a1")["file_name"])
    assert item(env, "task-86a2") is not None             # incremental: nothing else removed


def test_a_full_listing_that_lost_everything_is_held_for_an_admin(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "clickup/full")
    connect(env)
    sync.sync(env.ws, "clickup")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.add("GET", f"{C}/v2/team/900/task", json_body={"tasks": []})
    rec.add("GET", f"{C}/v3/workspaces/900/docs", json_body={"docs": [], "next_cursor": None})
    summary = sync.sync(env.ws, "clickup", full=True)
    assert (summary["held_deletes"], summary["deleted"]) == (4, 0)


def test_membership_maps_onto_members_and_fails_closed(env):
    import access
    import auth
    import members_store
    from kb.services import permissions
    members_store.set_password(f"ann@{env.ws}.example", "Ann", auth.hash_password("x" * 12),
                               env.ws, "public")
    r = permissions.Resolver(env.ws)
    assert r.resolve({"via": "clickup", "list": "l1", "users": [f"ann@{env.ws}.example"]})[0] == [
        access.user(f"ann@{env.ws}.example")]
    assert r.resolve({"via": "clickup", "list": None, "partial": True}) == ([access.NOBODY], 1)
    assert r.resolve({"via": "clickup", "list": "l1", "partial": True}) == ([access.NOBODY], 1)


def test_unreadable_list_members_make_the_tasks_for_no_one(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{C}/v2/list/l1/member", status=403, json_body={"err": "no",
                                                                    "ECODE": "ACCESS_083"})
    rec.load("clickup/full")
    connect(env)
    sync.sync(env.ws, "clickup")
    assert item(env, "task-86a1")["acl"] == {"via": "clickup", "list": "l1", "partial": True}


def test_a_429_waits_until_the_reset(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", f"{C}/v2/team", status=429, headers={"Retry-After": "13"},
            json_body={"err": "Rate limit reached", "ECODE": "APP_002"})
    rec.load("clickup/full")
    sync.sync(env.ws, "clickup")
    assert 13 in rec.slept


def test_clickup_is_live(env):
    c = card(env)
    assert (c["availability"], c["acl"], c["picker"]) == ("live", True, "tree")
