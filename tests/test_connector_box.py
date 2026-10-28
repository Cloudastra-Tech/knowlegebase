"""
Box against recorded HTTP (tests/fixtures/connectors/box/, written from Box's
API reference; an unrecorded request fails the test), through the real
framework (kb/services/sync.py): the OAuth connect flow, the single-use
refresh token, the folder picker, marker paging, the user event stream
position (taken before a full walk, deduplicated, a folder event listing
everything again), deletes behind the mass-delete guard, collaborations and
shared links into acl, 429s and typed errors.
"""

import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded, make_pdf
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

API = "https://api.box.com/2.0"
TOKEN = "https://api.box.com/oauth2/token"
FOLDERS = [{"id": "111", "name": "Handbook", "kind": "folder"}]


@pytest.fixture(autouse=True)
def app(monkeypatch):
    monkeypatch.setenv("BOX_CLIENT_ID", "box-client")
    monkeypatch.setenv("BOX_CLIENT_SECRET", "box-secret")


def connect(env, folders=FOLDERS, **secret):
    import integrations_store
    integrations_store.connect(env.ws, "box", "Acme (ann@acme.example)", {
        "access_token": "box-at-1", "refresh_token": "box-rt-1",
        "expires_at": time.time() + 3000, "login": "ann@acme.example", **secret}, "admin")
    integrations_store.update(env.ws, "box", settings={"user_id": "11", "folders": list(folders)})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "box", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "box")


def full(rec):
    rec.load("box/full")
    rec.add("GET", f"{API}/files/504/content", body=make_pdf("Expenses need a receipt."))


# ---- connecting ------------------------------------------------------------------------
def test_connect_saves_both_tokens_and_the_users_login(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/box/connect", headers=env.headers()).json()["url"]
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.netloc, parts.path) == ("account.box.com", "/api/oauth2/authorize")
    assert (q["client_id"], q["response_type"]) == ("box-client", "code")
    rec.add("POST", TOKEN, json_body={"access_token": "box-at-1", "expires_in": 3600,
                                      "token_type": "bearer", "refresh_token": "box-rt-1",
                                      "restricted_to": []})
    rec.add("GET", f"{API}/users/me", params={"fields": "id,name,login,enterprise"},
            json_body={"type": "user", "id": "11", "name": "Ann", "login": "Ann@Acme.example",
                       "enterprise": {"type": "enterprise", "id": "5", "name": "Acme"}})
    r = env.client.get("/api/integrations/box/callback", follow_redirects=False,
                       params={"state": q["state"], "code": "abc"})
    assert r.headers["location"] == "/#integrations?connected=box&choose=1"
    sec = integrations_store.secret(env.ws, "box")
    assert (sec["access_token"], sec["refresh_token"], sec["login"]) == (
        "box-at-1", "box-rt-1", "ann@acme.example")
    assert integrations_store.get(env.ws, "box")["account"] == "Acme (Ann@Acme.example)"
    assert rec.calls[0]["data"]["client_secret"] == "box-secret"


def test_the_single_use_refresh_token_is_replaced_and_saved(env, monkeypatch):
    import integrations_store
    from connectors import box
    rec = Recorded(monkeypatch)
    connect(env, expires_at=time.time() - 1)
    rec.add("POST", TOKEN, json_body={"access_token": "box-at-2", "refresh_token": "box-rt-2",
                                      "expires_in": 3600})
    rec.add("GET", f"{API}/folders/0/items", status=401,
            json_body={"type": "error", "status": 401, "code": "invalid_token"})
    rec.add("POST", TOKEN, json_body={"access_token": "box-at-3", "refresh_token": "box-rt-3",
                                      "expires_in": 3600})
    rec.add("GET", f"{API}/folders/0/items", json_body={"entries": [
        {"type": "folder", "id": "111", "name": "Handbook"},
        {"type": "file", "id": "9", "name": "x.txt"}], "next_marker": None})
    ctx = base.SyncContext(workspace=env.ws, provider="box", settings={}, known={})
    assert box.CONNECTOR.list_containers(ctx) == [
        {"id": "111", "name": "Handbook", "kind": "folder", "has_children": True}]
    assert [c["data"]["refresh_token"] for c in rec.requests_to("oauth2/token")] == [
        "box-rt-1", "box-rt-2"]
    assert integrations_store.secret(env.ws, "box")["refresh_token"] == "box-rt-3"
    integrations_store.update(env.ws, "box", secret={
        **integrations_store.secret(env.ws, "box"), "expires_at": time.time() - 1})
    rec.add("POST", TOKEN, status=400, json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        box.CONNECTOR.list_containers(ctx)


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_walks_the_folders_and_captures_collaborations(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    full(rec)
    summary = sync.sync(env.ws, "box")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"], summary["skipped"]) == ("full", 2, 1)
    events = rec.requests_to("/events")[0]
    assert rec.calls.index(events) == 0                  # the position comes before the walk
    leave = item(env, "501")
    assert "25 days" in env.text("box", leave["file_name"])
    assert leave["url"] == "https://app.box.com/file/501"
    assert leave["acl"] == {"via": "box", "users": ["ann@acme.example", "bob@acme.example",
                                                    "carol@acme.example"],
                            "groups": ["box:group:77"], "shared_link": None}
    expenses = item(env, "504")                          # in a sub-folder with a company link
    assert expenses["acl"]["users"] == ["ann@acme.example", "bob@acme.example",
                                        "dave@acme.example"]
    assert expenses["acl"]["shared_link"] == "company"
    assert item(env, "502")["skipped_reason"] == "too_large"
    assert item(env, "503") is None
    assert len(rec.requests_to("/folders/111/collaborations")) == 1   # cached per run
    assert json.loads(sync_store.state(env.ws, "box")["cursor"])["position"] == "1000"


def test_then_the_event_stream_says_what_changed(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    full(rec)
    sync.sync(env.ws, "box")
    rec.load("box/incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "box")
    assert rec.unused() == []
    assert not [c for c in rec.calls[before:] if "/items" in c["url"]]   # no walk
    assert (summary["mode"], summary["upserted"], summary["deleted"]) == ("incremental", 1, 1)
    assert len([c for c in rec.calls[before:] if c["url"] == f"{API}/files/501"]) == 1  # deduped
    leave = item(env, "501")
    assert "28 days" in env.text("box", leave["file_name"])
    assert leave["acl"]["shared_link"] == "open"
    assert item(env, "504") is None                      # trashed
    assert json.loads(sync_store.state(env.ws, "box")["cursor"])["position"] == "1100"


def test_a_folder_event_lists_everything_again(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    full(rec)
    sync.sync(env.ws, "box")
    rec.add("GET", f"{API}/events", params={"stream_position": "1000"}, json_body={
        "chunk_size": 1, "next_stream_position": "1200", "entries": [
            {"type": "event", "event_id": "e9", "event_type": "COLLAB_ADD_COLLABORATOR",
             "source": {"type": "folder", "id": "222"}}]})
    full(rec)
    summary = sync.sync(env.ws, "box")
    assert summary["mode"] == "full"


def test_a_listing_that_lost_everything_is_held_for_an_admin(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    full(rec)
    sync.sync(env.ws, "box")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    for _ in range(2):
        rec.add("GET", f"{API}/events", params={"stream_position": "now"},
                json_body={"chunk_size": 0, "next_stream_position": "2000", "entries": []})
        rec.add("GET", f"{API}/folders/111/items", json_body={"entries": [], "next_marker": None})
    summary = sync.sync(env.ws, "box", full=True)
    assert (summary["held_deletes"], summary["deleted"]) == (2, 0)
    assert sync.sync(env.ws, "box", full=True, confirm_deletes=True)["deleted"] == 2


def test_a_429_waits_and_a_picked_folder_that_is_gone_needs_an_admin(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", f"{API}/events", status=429, headers={"Retry-After": "13"},
            json_body={"type": "error", "status": 429, "code": "rate_limit_exceeded"})
    full(rec)
    sync.sync(env.ws, "box")
    assert 13 in rec.slept
    rec.add("GET", f"{API}/events", json_body={"next_stream_position": "1", "entries": []})
    rec.add("GET", f"{API}/folders/111/items", status=404,
            json_body={"type": "error", "status": 404, "code": "not_found"})
    with pytest.raises(base.ConfigError):
        sync.sync(env.ws, "box", full=True)
    assert card(env)["error"]["code"] == "config"


def test_disconnecting_revokes_the_token(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("POST", "https://api.box.com/oauth2/revoke")
    assert env.client.delete("/api/integrations/box", headers=env.headers()).status_code == 200
    assert rec.calls[0]["data"] == {"client_id": "box-client", "client_secret": "box-secret",
                                    "token": "box-rt-1"}
    assert integrations_store.get(env.ws, "box") is None


def test_box_is_live_in_the_registry(env):
    c = card(env)
    assert (c["availability"], c["acl"], c["picker"]) == ("live", True, "tree")
    assert "Platform Apps Manager" in c["note"]
