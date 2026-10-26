"""
Confluence Cloud (OAuth 3LO) and Data Center (personal access token) against
recorded HTTP (tests/fixtures/connectors/confluence/, written from the shapes in
Atlassian's REST v2 / v1 specs; an unrecorded request fails the test), through
the real framework (kb/services/sync.py): connecting, the space picker, a full
listing with attachments and restrictions, the CQL incremental cursor, deletes
and the mass-delete guard, token rotation, typed errors and rate limits.
"""

import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded, make_pdf
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

WIKI = "https://api.atlassian.com/ex/confluence/cloud-1/wiki"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"


def connect_cloud(env, expires_at=None, spaces=("ENG",)):
    import integrations_store
    integrations_store.connect(env.ws, "confluence", "Acme (acme.atlassian.net)", {
        "access_token": "test-access", "refresh_token": "test-refresh",
        "expires_at": expires_at or time.time() + 3600, "cloud_id": "cloud-1",
        "site_url": "https://acme.atlassian.net"}, "admin")
    integrations_store.update(env.ws, "confluence", settings={
        "cloud_id": "cloud-1",
        "spaces": [{"id": k, "name": k, "kind": "space"} for k in spaces]})


def cloud_full(rec):
    rec.load("confluence/cloud_full")
    rec.add("GET", f"{WIKI}/rest/api/content/2001/child/attachment/att4001/download",
            body=make_pdf("Rollback with make rollback."),
            headers={"Content-Type": "application/pdf"})


def item(env, provider, sid):
    import sync_store
    return sync_store.item(env.ws, provider, sid)


def card(env, provider):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == provider)


# ---- connecting ------------------------------------------------------------------------
def test_cloud_connects_with_3lo_and_keeps_the_site(env, monkeypatch):
    import integrations_store
    monkeypatch.setenv("ATLASSIAN_CLIENT_ID", "cid")
    monkeypatch.setenv("ATLASSIAN_CLIENT_SECRET", "csecret")
    rec = Recorded(monkeypatch)
    headers = env.headers()
    url = env.client.post("/api/integrations/confluence/connect", headers=headers).json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith("https://auth.atlassian.com/authorize?")
    assert (q["audience"], q["prompt"], q["response_type"]) == ("api.atlassian.com", "consent",
                                                               "code")
    scopes = q["scope"].split()
    assert "offline_access" in scopes and all(s.startswith("read:") for s in scopes
                                              if s != "offline_access")
    rec.add("POST", TOKEN_URL, match_json={"grant_type": "authorization_code", "code": "abc",
                                           "client_id": "cid", "client_secret": "csecret"},
            json_body={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
                       "scope": q["scope"]})
    rec.add("GET", "https://api.atlassian.com/oauth/token/accessible-resources",
            req_headers={"Authorization": "Bearer at-1"}, json_body=[
                {"id": "jira-1", "name": "acme", "url": "https://acme.atlassian.net",
                 "scopes": ["read:jira-work"], "avatarUrl": ""},
                {"id": "cloud-1", "name": "acme", "url": "https://acme.atlassian.net",
                 "scopes": ["read:page:confluence", "read:space:confluence"], "avatarUrl": ""}])
    r = env.client.get("/api/integrations/confluence/callback",
                       params={"state": q["state"], "code": "abc"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?connected=confluence&choose=1"
    sec = integrations_store.secret(env.ws, "confluence")
    assert (sec["cloud_id"], sec["refresh_token"]) == ("cloud-1", "rt-1")
    assert integrations_store.get(env.ws, "confluence")["account"] == "acme (acme.atlassian.net)"


def test_cloud_without_a_confluence_site_says_so(env, monkeypatch):
    monkeypatch.setenv("ATLASSIAN_CLIENT_ID", "cid")
    monkeypatch.setenv("ATLASSIAN_CLIENT_SECRET", "csecret")
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/confluence/connect", headers=env.headers()).json()["url"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
    rec.add("POST", TOKEN_URL, json_body={"access_token": "a", "refresh_token": "r"})
    rec.add("GET", "https://api.atlassian.com/oauth/token/accessible-resources", json_body=[])
    r = env.client.get("/api/integrations/confluence/callback",
                       params={"state": state, "code": "abc"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?error=no_site"


def test_the_picker_lists_spaces_and_saves_them(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect_cloud(env, spaces=())
    rec.add("GET", f"{WIKI}/api/v2/spaces", params={"status": "current", "limit": "250"},
            json_body={"results": [{"id": "1", "key": "ENG", "name": "Engineering",
                                    "type": "global"},
                                   {"id": "2", "key": "HR", "name": "abc People",
                                    "type": "global"}],
                       "_links": {"next": "/wiki/api/v2/spaces?cursor=n2&limit=250"}})
    rec.add("GET", f"{WIKI}/api/v2/spaces", params={"cursor": "n2"},
            json_body={"results": [{"id": "3", "key": "~ann", "name": "Ann",
                                    "type": "personal"}], "_links": {}})
    headers = env.headers()
    r = env.client.get("/api/integrations/confluence/containers", headers=headers)
    assert r.status_code == 200, r.text
    assert [(c["id"], c["kind"]) for c in r.json()["items"]] == [
        ("HR", "space"), ("~ann", "space"), ("ENG", "space")]
    r = env.client.put("/api/integrations/confluence/containers", headers=headers,
                       json={"containers": [r.json()["items"][2]]})
    assert r.json()["containers"] == [{"id": "ENG", "name": "Engineering", "kind": "space"}]


# ---- syncing (Cloud) -------------------------------------------------------------------
def test_cloud_full_sync_lists_pages_blogposts_attachments_and_restrictions(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    cloud_full(rec)
    connect_cloud(env)
    summary = sync.sync(env.ws, "confluence")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 5)
    assert all(c["headers"]["Authorization"] == "Bearer test-access" for c in rec.calls)
    # Storage format through extract.py: headings, code, a Jira placeholder, tasks.
    deploys = item(env, "confluence", "page-2001")
    assert env.text("confluence", deploys["file_name"]) == (
        "Deploys\n\n# How we deploy\n\nWe deploy on Tuesdays.\n\nmake deploy ENV=prod\n\n"
        "[Jira issue OPS-12]")
    assert deploys["url"] == "https://acme.atlassian.net/wiki/spaces/ENG/pages/2001"
    retro = item(env, "confluence", "blogpost-3001")
    assert env.text("confluence", retro["file_name"]).endswith("[x] Fix the pager rota")
    # A readable attachment comes along (its page is its parent); an image and the
    # attachment of a page that isn't in the space listing don't.
    runbook = item(env, "confluence", "attachment-att4001")
    assert runbook["parent_id"] == "page-2001" and runbook["file_name"].endswith(".pdf")
    assert {s for s in sync_store.items(env.ws, "confluence")} == {
        "page-2001", "page-2002", "page-2003", "blogpost-3001", "attachment-att4001"}
    # Permissions captured, not enforced: the space's readers and the page's own
    # read restriction, plus its ancestors (restrictions are inherited).
    salaries = item(env, "confluence", "page-2002")["acl"]
    assert salaries == {
        "space": "ENG",
        "space_read": {"users": ["atlassian:acc-ann"], "groups": ["atlassian:group:grp-eng"],
                       "roles": ["atlassian:role:role-viewer"], "space_id": "1001"},
        "restricted": True, "users": ["ann@acme.com", "atlassian:acc-ann"],
        "groups": ["atlassian:group:grp-hr"], "inherits_from": ["page-2001"]}
    assert item(env, "confluence", "page-2001")["acl"]["restricted"] is False
    # Not in the search index yet: kept, and marked as not fully known.
    assert item(env, "confluence", "page-2003")["acl"]["partial"] is True
    assert item(env, "confluence", "attachment-att4001")["acl"]["inherits_from"] == ["page-2001"]
    cursor = json.loads(sync_store.state(env.ws, "confluence")["cursor"])
    assert cursor["watermark"] == "2026-10-13T08:00:00.000Z" and cursor["spaces"] == ["ENG"]


def test_cloud_then_syncs_only_what_cql_says_changed(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    cloud_full(rec)
    connect_cloud(env)
    sync.sync(env.ws, "confluence")
    env.changed.clear()
    rec.load("confluence/cloud_incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "confluence")
    assert rec.unused() == []
    calls = rec.calls[before:]
    # One search; the watermark moved back by more than any time zone (CQL dates
    # are in the site's zone); no listing, no space permissions this time.
    [search] = [c for c in calls if c["url"].endswith("/content/search")]
    assert 'lastmodified >= "2026-10-12 17:50"' in search["params"]["cql"]
    assert not [c for c in calls if "/spaces" in c["url"]]
    assert (summary["mode"], summary["upserted"], summary["unchanged"]) == ("incremental", 2, 1)
    salaries = item(env, "confluence", "page-2002")
    assert "April" in env.text("confluence", salaries["file_name"])
    assert len(env.changed) == 1 and len(env.changed[0]) == 2


def test_cloud_full_listing_every_so_often_finds_deletes_behind_the_guard(env, monkeypatch):
    import sync_store
    from connectors import confluence
    from kb.services import sync
    rec = Recorded(monkeypatch)
    cloud_full(rec)
    connect_cloud(env)
    sync.sync(env.ws, "confluence")
    state = json.loads(sync_store.state(env.ws, "confluence")["cursor"])
    sync_store.save_state(env.ws, "confluence", cursor=json.dumps(
        {**state, "full_at": time.time() - confluence.RECONCILE - 1}))

    def empty_space(rec):
        rec.add("GET", f"{WIKI}/api/v2/spaces", params={"keys": "ENG"},
                json_body={"results": [{"id": "1001", "key": "ENG", "name": "Engineering"}]})
        rec.add("GET", f"{WIKI}/api/v2/spaces/1001/permissions", json_body={"results": []})
        rec.add("GET", f"{WIKI}/rest/api/content/search", json_body={"results": [],
                                                                       "_links": {}})
        rec.add("GET", f"{WIKI}/api/v2/spaces/1001/pages", json_body={"results": []})
        rec.add("GET", f"{WIKI}/api/v2/spaces/1001/blogposts", json_body={"results": []})

    # Everything gone at once is a mass delete: held for an admin.
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    empty_space(rec)
    summary = sync.sync(env.ws, "confluence")
    assert (summary["mode"], summary["held_deletes"], summary["deleted"]) == ("full", 5, 0)
    assert len(sync_store.items(env.ws, "confluence")) == 5
    # Confirmed: the pages and the attachment go.
    empty_space(rec)
    summary = sync.sync(env.ws, "confluence", confirm_deletes=True)
    assert summary["deleted"] == 5 and env.files("confluence") == []


def test_cloud_rotates_the_refresh_token_and_reports_a_revoked_one(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    monkeypatch.setenv("ATLASSIAN_CLIENT_ID", "cid")
    monkeypatch.setenv("ATLASSIAN_CLIENT_SECRET", "csecret")
    rec = Recorded(monkeypatch)
    connect_cloud(env, expires_at=time.time() - 10)
    rec.add("POST", TOKEN_URL, match_json={"grant_type": "refresh_token",
                                           "refresh_token": "test-refresh"},
            json_body={"access_token": "test-access-2", "refresh_token": "test-refresh-2",
                       "expires_in": 3600})
    cloud_full(rec)
    sync.sync(env.ws, "confluence")
    sec = integrations_store.secret(env.ws, "confluence")
    assert (sec["access_token"], sec["refresh_token"]) == ("test-access-2", "test-refresh-2")
    assert rec.calls[1]["headers"]["Authorization"] == "Bearer test-access-2"

    # Revoked (Atlassian: 403 invalid_grant): the card asks for a reconnect.
    integrations_store.update(env.ws, "confluence", secret={**sec, "expires_at": 0})
    rec.add("POST", TOKEN_URL, status=403, json_body={
        "error": "invalid_grant", "error_description": "Unknown or invalid refresh token."})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "confluence", full=True)
    assert card(env, "confluence")["error"]["code"] == "auth_expired"


def test_a_missing_scope_is_named_not_mistaken_for_an_expired_token(env, monkeypatch):
    from kb.services import sync
    monkeypatch.setenv("ATLASSIAN_CLIENT_ID", "cid")
    monkeypatch.setenv("ATLASSIAN_CLIENT_SECRET", "csecret")
    rec = Recorded(monkeypatch)
    connect_cloud(env)
    rec.add("GET", f"{WIKI}/api/v2/spaces", status=401, repeat=True,
            text='{"code":401,"message":"Unauthorized; scope does not match"}')
    r = env.client.get("/api/integrations/confluence/containers", headers=env.headers())
    assert r.status_code == 502 and r.json()["detail"] == base.ERRORS["missing_scope"]
    assert not rec.requests_to("oauth/token")                   # no pointless refresh
    with pytest.raises(base.MissingScope):
        sync.sync(env.ws, "confluence")
    assert card(env, "confluence")["error"]["code"] == "missing_scope"
    # It waits for an admin: the periodic sync leaves it alone.
    queued = []
    monkeypatch.setattr(sync, "start_sync", lambda ws, provider, **kw: queued.append((ws, provider)))
    sync.sync_all()
    assert (env.ws, "confluence") not in queued


def test_atlassian_rate_limits_are_waited_out(env, monkeypatch):
    from connectors import confluence, http
    rec = Recorded(monkeypatch)
    connect_cloud(env)
    url = f"{WIKI}/api/v2/spaces"
    rec.add("GET", url, status=429, headers={"Retry-After": "7",
                                             "RateLimit-Reason": "confluence-quota-global-based"})
    rec.add("GET", url, status=429, headers={"X-RateLimit-Reset": "2030-01-01T00:00:00Z"},
            json_body={})
    rec.add("GET", url, json_body={"results": []}, headers={"X-RateLimit-NearLimit": "true"})
    rec.add("GET", url, json_body={"results": []})
    c = confluence.CLOUD_CONNECTOR
    # Retry-After is slept through and retried...
    with pytest.raises(base.RateLimited) as e:
        c._get(env.ws, url)
    assert 7 in rec.slept
    # ...a reset far away (the hourly quota is gone) is handed back, not slept through.
    assert e.value.retry_after > http.MAX_WAIT
    c._get(env.ws, url)
    # Near the limit: the token's next call waits a little.
    start = rec.now
    c._get(env.ws, url)
    assert rec.now - start >= confluence.NEAR_LIMIT_PAUSE


# ---- Data Center -----------------------------------------------------------------------
def test_data_center_connects_with_a_token_and_syncs_a_space(env, monkeypatch):
    import db
    import integrations_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "confluence/dc_setup_and_sync")
    headers = env.headers()
    r = env.client.post("/api/integrations/confluence_dc/setup", headers=headers, json={
        "url": "https://confluence.acme.com/rest/api/", "token": "test-pat"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "account": "confluence.acme.com (Ann Lee)", "choose": True}
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 'confluence_dc'", (env.ws,)).fetchone()[0]
    assert b"test-pat" not in blob
    assert card(env, "confluence_dc")["base_url"] == "https://confluence.acme.com"
    r = env.client.get("/api/integrations/confluence_dc/containers", headers=headers)
    assert [c["id"] for c in r.json()["items"]] == ["ENG", "HR"]
    env.client.put("/api/integrations/confluence_dc/containers", headers=headers,
                   json={"containers": [{"id": "ENG", "name": "Engineering"}]})
    summary = sync.sync(env.ws, "confluence_dc")
    assert rec.unused() == []
    assert summary["upserted"] == 3
    assert all(c["headers"]["Authorization"] == "Bearer test-pat" for c in rec.calls)
    handbook = item(env, "confluence_dc", "page-5001")
    assert env.text("confluence_dc", handbook["file_name"]) == ("Handbook\n\n## Welcome\n\n"
                                                                "Read this first.")
    assert handbook["url"] == "https://confluence.acme.com/display/ENG/Handbook"
    policy = item(env, "confluence_dc", "attachment-att6001")
    assert "replaced every 3 years" in env.text("confluence_dc", policy["file_name"])
    # Data Center has no API for space permissions: unknown, not "nobody".
    acl = item(env, "confluence_dc", "page-5002")["acl"]
    assert acl["space_read"] is None
    assert (acl["users"], acl["groups"]) == (["atlassian:ann"],
                                             ["atlassian:group:confluence-hr"])
    assert integrations_store.get(env.ws, "confluence_dc")["status"] == "connected"


@pytest.mark.parametrize("url, host_ip, message", [
    ("http://confluence.acme.com", None, "https"),
    ("https://confluence.internal", "10.0.0.7", "private network"),
])
def test_data_center_refuses_addresses_the_server_must_not_call(env, monkeypatch, url, host_ip,
                                                                message):
    rec = Recorded(monkeypatch)
    if host_ip:
        rec.hosts[url.split("//")[1]] = host_ip
    r = env.client.post("/api/integrations/confluence_dc/setup", headers=env.headers(),
                        json={"url": url, "token": "test-pat"})
    assert r.status_code == 400 and message in r.json()["detail"]
    assert rec.calls == []


def test_data_center_rejects_a_bad_token_in_plain_words(env, monkeypatch):
    rec = Recorded(monkeypatch)
    rec.add("GET", "https://confluence.acme.com/rest/api/user/current", status=401)
    r = env.client.post("/api/integrations/confluence_dc/setup", headers=env.headers(),
                        json={"url": "https://confluence.acme.com", "token": "wrong"})
    assert r.status_code == 400 and r.json()["detail"] == "Confluence didn't accept that token."


def test_both_are_live_in_the_registry_not_coming_soon(env):
    r = env.client.get("/api/integrations/catalog").json()["connectors"]
    by_id = {c["id"]: c["availability"] for c in r}
    assert by_id["confluence"] == by_id["confluence_dc"] == by_id["sharepoint"] == "live"
    assert [c["id"] for c in r].count("confluence") == 1
