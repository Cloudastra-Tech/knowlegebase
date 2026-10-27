"""
Dropbox against recorded HTTP (tests/fixtures/connectors/dropbox/, written
from Dropbox's API reference and its API spec; an unrecorded request fails
the test), through the real framework (kb/services/sync.py): the offline
OAuth flow and the team-space path root, the refresh token (reused, not
rotated) on expiry and on a 401, the folder picker, list_folder with its
cursor and /continue, deleted entries by path (a move isn't a delete), a
`reset` cursor, deletes behind the mass-delete guard, shared-folder members
into acl, 429s and typed errors.
"""

import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded, make_pdf
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

API = "https://api.dropboxapi.com/2"
C = "https://content.dropboxapi.com/2"
TOKEN = "https://api.dropboxapi.com/oauth2/token"
FOLDERS = [{"id": "id:fold-hb", "name": "Handbook", "kind": "folder", "path": "/handbook"}]


@pytest.fixture(autouse=True)
def app(monkeypatch):
    monkeypatch.setenv("DROPBOX_APP_KEY", "db-key")
    monkeypatch.setenv("DROPBOX_APP_SECRET", "db-secret")


def connect(env, folders=FOLDERS, **secret):
    import integrations_store
    integrations_store.connect(env.ws, "dropbox", "ann@acme.example", {
        "access_token": "db-at-1", "refresh_token": "db-rt", "expires_at": time.time() + 3600,
        "email": "ann@acme.example", **secret}, "admin")
    integrations_store.update(env.ws, "dropbox", settings={"account_id": "dbid:ann",
                                                           "folders": list(folders)})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "dropbox", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "dropbox")


def full(rec):
    rec.load("dropbox/full")
    rec.add("POST", f"{C}/files/download", body=make_pdf("Expenses need a receipt."),
            req_headers={"Dropbox-API-Arg": json.dumps({"path": "id:b"})})


# ---- connecting ------------------------------------------------------------------------
def test_connect_is_offline_and_reaches_the_team_space(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/dropbox/connect", headers=env.headers()).json()["url"]
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.netloc, parts.path) == ("www.dropbox.com", "/oauth2/authorize")
    assert (q["client_id"], q["token_access_type"], q["response_type"]) == (
        "db-key", "offline", "code")
    assert set(q["scope"].split()) == {"account_info.read", "files.metadata.read",
                                       "files.content.read", "sharing.read"}
    rec.add("POST", TOKEN, json_body={"access_token": "db-at-1", "expires_in": 14400,
                                      "token_type": "bearer", "refresh_token": "db-rt",
                                      "account_id": "dbid:ann", "scope": q["scope"]})
    rec.add("POST", f"{API}/users/get_current_account", json_body={
        "account_id": "dbid:ann", "email": "Ann@Acme.example",
        "name": {"display_name": "Ann Lee"}, "team": {"id": "dbtid:1", "name": "Acme"},
        "root_info": {".tag": "team", "root_namespace_id": "3235641",
                      "home_namespace_id": "3235642", "home_path": "/Ann Lee"}})
    r = env.client.get("/api/integrations/dropbox/callback", follow_redirects=False,
                       params={"state": q["state"], "code": "abc"})
    assert r.headers["location"] == "/#integrations?connected=dropbox&choose=1"
    assert rec.calls[0]["data"]["grant_type"] == "authorization_code"
    sec = integrations_store.secret(env.ws, "dropbox")
    assert (sec["refresh_token"], sec["root"], sec["email"]) == ("db-rt", "3235641",
                                                                 "ann@acme.example")
    assert integrations_store.get(env.ws, "dropbox")["account"] == "Acme (Ann@Acme.example)"
    # Every call after this is rooted at the team space.
    rec.add("POST", f"{API}/files/list_folder", json_body={"entries": [
        {".tag": "folder", "id": "id:f1", "name": "Team", "path_lower": "/team"},
        {".tag": "file", "id": "id:x", "name": "x.txt"}], "cursor": "c", "has_more": False})
    items = env.client.get("/api/integrations/dropbox/containers", headers=env.headers())
    assert items.json()["items"] == [{"id": "id:f1", "name": "Team", "kind": "folder",
                                      "path": "/team", "has_children": True}]
    assert json.loads(rec.calls[-1]["headers"]["Dropbox-API-Path-Root"]) == {
        ".tag": "root", "root": "3235641"}


def test_the_refresh_token_is_reused_on_expiry_and_after_a_401(env, monkeypatch):
    import integrations_store
    from connectors import dropbox
    rec = Recorded(monkeypatch)
    connect(env, expires_at=time.time() - 1)
    rec.add("POST", TOKEN, json_body={"access_token": "db-at-2", "expires_in": 14400,
                                      "token_type": "bearer"})
    rec.add("POST", f"{API}/files/list_folder", status=401,
            json_body={"error_summary": "expired_access_token/",
                       "error": {".tag": "expired_access_token"}})
    rec.add("POST", TOKEN, json_body={"access_token": "db-at-3", "expires_in": 14400})
    rec.add("POST", f"{API}/files/list_folder",
            json_body={"entries": [], "cursor": "c", "has_more": False})
    ctx = base.SyncContext(workspace=env.ws, provider="dropbox", settings={}, known={})
    assert dropbox.CONNECTOR.list_containers(ctx) == []
    assert [c["data"]["refresh_token"] for c in rec.requests_to("oauth2/token")] == [
        "db-rt", "db-rt"]
    sec = integrations_store.secret(env.ws, "dropbox")
    assert (sec["access_token"], sec["refresh_token"]) == ("db-at-3", "db-rt")
    rec.add("POST", f"{API}/files/list_folder", status=401, json_body={})
    rec.add("POST", TOKEN, status=400, json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        dropbox.CONNECTOR.list_containers(ctx)


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_pages_the_listing_and_captures_shared_folder_members(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    full(rec)
    summary = sync.sync(env.ws, "dropbox")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"], summary["skipped"]) == ("full", 3, 1)
    lst = rec.requests_to("files/list_folder")[0]["json"]
    assert (lst["path"], lst["recursive"]) == ("id:fold-hb", True)
    leave = item(env, "id:a")
    assert "25 days" in env.text("dropbox", leave["file_name"])
    assert leave["acl"] == {
        "via": "dropbox", "shared_folder": "sf-1",
        "users": ["ann@acme.example", "bob@acme.example", "dave@acme.example"],   # not carol
        "groups": ["dropbox:ext:eng@acme.example", "dropbox:group:g:2"],
        "invitees": ["new@acme.example"]}
    assert len(rec.requests_to("sharing/list_folder_members")) == 2   # once per run, paged
    assert item(env, "id:b")["acl"] == {"via": "dropbox", "users": ["ann@acme.example"],
                                        "owner_only": True}
    assert item(env, "id:big")["skipped_reason"] == "too_large"
    assert item(env, "id:c") is None
    assert leave["url"] == "https://www.dropbox.com/home/Handbook?preview=Leave.md"
    cursor = json.loads(sync_store.state(env.ws, "dropbox")["cursor"])
    assert cursor["cursors"] == {"id:fold-hb": "cur-2"}


def test_then_continue_from_the_cursor_with_deletes_by_path_and_moves_kept(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    full(rec)
    sync.sync(env.ws, "dropbox")
    rec.load("dropbox/incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "dropbox")
    assert rec.unused() == []
    assert not [c for c in rec.calls[before:] if c["url"].endswith("files/list_folder")]
    assert (summary["mode"], summary["upserted"], summary["unchanged"], summary["deleted"]) == (
        "incremental", 1, 1, 1)
    assert "28 days" in env.text("dropbox", item(env, "id:a")["file_name"])
    assert item(env, "id:d") is None                       # deleted
    moved = item(env, "id:b")                              # moved, not deleted
    assert moved["attrs"]["path"] == "/handbook/archive/expenses.pdf"
    assert json.loads(sync_store.state(env.ws, "dropbox")["cursor"])["cursors"] == {
        "id:fold-hb": "cur-3"}


def test_a_reset_cursor_lists_the_folder_again(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    full(rec)
    sync.sync(env.ws, "dropbox")
    rec.add("POST", f"{API}/files/list_folder/continue", status=409, json_body={
        "error_summary": "reset/..", "error": {".tag": "reset"}})
    full(rec)
    summary = sync.sync(env.ws, "dropbox")
    assert summary["mode"] == "full"
    assert json.loads(sync_store.state(env.ws, "dropbox")["cursor"])["cursors"] == {
        "id:fold-hb": "cur-2"}


def test_a_picked_folder_that_is_gone_needs_an_admin(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("POST", f"{API}/files/list_folder", status=409, json_body={
        "error_summary": "path/not_found/...", "error": {".tag": "path",
                                                         "path": {".tag": "not_found"}}})
    with pytest.raises(base.ConfigError):
        sync.sync(env.ws, "dropbox")
    assert card(env)["error"]["code"] == "config"


def test_a_listing_that_lost_everything_is_held_for_an_admin(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    full(rec)
    sync.sync(env.ws, "dropbox")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    for _ in range(2):
        rec.add("POST", f"{API}/files/list_folder",
                json_body={"entries": [], "cursor": "e", "has_more": False})
    summary = sync.sync(env.ws, "dropbox", full=True)
    assert (summary["held_deletes"], summary["deleted"]) == (3, 0)
    assert sync.sync(env.ws, "dropbox", full=True, confirm_deletes=True)["deleted"] == 3
    assert env.files("dropbox") == []


def test_a_429_waits_for_retry_after(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("POST", f"{API}/files/list_folder", status=429, headers={"Retry-After": "9"},
            json_body={"error_summary": "too_many_requests/",
                       "error": {"reason": {".tag": "too_many_requests"}, "retry_after": 9}})
    full(rec)
    sync.sync(env.ws, "dropbox")
    assert 9 in rec.slept and rec.unused() == []


def test_disconnecting_revokes_the_token(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("POST", f"{API}/auth/token/revoke", text="null")
    assert env.client.delete("/api/integrations/dropbox", headers=env.headers()).status_code == 200
    assert rec.calls[0]["headers"]["Authorization"] == "Bearer db-at-1"
    assert integrations_store.get(env.ws, "dropbox") is None


def test_dropbox_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["dropbox"] == "live"
    assert card(env)["status"] == "disconnected"
