"""
Jira Cloud (the Atlassian 3LO app Confluence uses) and Jira Data Center
(personal access token) against recorded HTTP (tests/fixtures/connectors/jira/,
written from the shapes in Atlassian's REST v3 spec and the Data Center REST
reference; an unrecorded request fails the test), through the real framework
(kb/services/sync.py): connecting through Confluence's callback URL, the
project picker, the JQL listing and its paging, the incremental cursor, ADF to
Markdown, restricted comments, deletes behind the mass-delete guard, acl
capture, token rotation, 429s and typed errors.
"""

import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

API = "https://api.atlassian.com/ex/jira/cloud-j1/rest/api/3"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"


def connect_cloud(env, expires_at=None, projects=("ENG",)):
    import integrations_store
    integrations_store.connect(env.ws, "jira", "acme (acme.atlassian.net)", {
        "access_token": "test-access", "refresh_token": "test-refresh",
        "expires_at": expires_at or time.time() + 3600, "cloud_id": "cloud-j1",
        "site_url": "https://acme.atlassian.net"}, "admin")
    integrations_store.update(env.ws, "jira", settings={
        "cloud_id": "cloud-j1",
        "projects": [{"id": k, "name": k, "kind": "project"} for k in projects]})


def item(env, provider, sid):
    import sync_store
    return sync_store.item(env.ws, provider, sid)


def card(env, provider):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == provider)


def keep_old_issues_out(monkeypatch):
    """A backfill window ending between ENG-3 (2024-01-01) and the others (2026-10)."""
    cutoff = time.mktime((2025, 1, 1, 0, 0, 0, 0, 0, 0))
    monkeypatch.setenv("JIRA_BACKFILL_DAYS", str(int((time.time() - cutoff) / 86400)))


# ---- connecting ------------------------------------------------------------------------
def test_cloud_connects_through_the_atlassian_apps_one_callback(env, monkeypatch):
    import integrations_store
    monkeypatch.setenv("ATLASSIAN_CLIENT_ID", "cid")
    monkeypatch.setenv("ATLASSIAN_CLIENT_SECRET", "csecret")
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/jira/connect", headers=env.headers()).json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith("https://auth.atlassian.com/authorize?")
    # Only Jira's read scopes, and the redirect is Confluence's callback URL (an
    # Atlassian app has one).
    assert q["scope"] == "read:jira-work read:jira-user offline_access"
    assert q["redirect_uri"].endswith("/api/integrations/confluence/callback")
    rec.add("POST", TOKEN_URL, match_json={"grant_type": "authorization_code", "code": "abc",
                                           "redirect_uri": q["redirect_uri"]},
            json_body={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})
    rec.add("GET", "https://api.atlassian.com/oauth/token/accessible-resources", json_body=[
        {"id": "conf-1", "name": "acme", "url": "https://acme.atlassian.net",
         "scopes": ["read:page:confluence"], "avatarUrl": ""},
        {"id": "cloud-j1", "name": "acme", "url": "https://acme.atlassian.net",
         "scopes": ["read:jira-work", "read:jira-user"], "avatarUrl": ""}])
    r = env.client.get("/api/integrations/confluence/callback",
                       params={"state": q["state"], "code": "abc"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?connected=jira&choose=1"
    assert integrations_store.get(env.ws, "confluence") is None
    sec = integrations_store.secret(env.ws, "jira")
    assert (sec["cloud_id"], sec["refresh_token"]) == ("cloud-j1", "rt-1")
    # Jira's own path isn't a callback (its state is only valid on Confluence's).
    r = env.client.get("/api/integrations/jira/callback", params={"state": q["state"],
                                                                  "code": "abc"},
                       follow_redirects=False)
    assert r.headers["location"] == "/#integrations?error=expired"


def test_the_picker_lists_projects_and_saves_them(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect_cloud(env, projects=())
    rec.add("GET", f"{API}/project/search", params={"startAt": "0"}, json_body={
        "startAt": 0, "maxResults": 50, "total": 3, "isLast": False,
        "values": [{"id": "10000", "key": "ENG", "name": "Engineering"},
                   {"id": "10001", "key": "HR", "name": "People"}]})
    rec.add("GET", f"{API}/project/search", params={"startAt": "2"}, json_body={
        "startAt": 2, "maxResults": 50, "total": 3, "isLast": True,
        "values": [{"id": "10002", "key": "ADM", "name": "Admin"}]})
    headers = env.headers()
    r = env.client.get("/api/integrations/jira/containers", headers=headers)
    assert r.status_code == 200, r.text
    assert [(c["id"], c["kind"]) for c in r.json()["items"]] == [
        ("ADM", "project"), ("ENG", "project"), ("HR", "project")]
    r = env.client.put("/api/integrations/jira/containers", headers=headers,
                       json={"containers": [r.json()["items"][1]]})
    assert r.json()["containers"] == [{"id": "ENG", "name": "Engineering (ENG)",
                                       "kind": "project"}]


# ---- syncing (Cloud) -------------------------------------------------------------------
def test_full_sync_pages_through_jql_and_writes_issues_as_markdown(env, monkeypatch):
    import sync_store
    from kb.services import sync
    keep_old_issues_out(monkeypatch)
    rec = Recorded(monkeypatch, "jira/cloud_full")
    connect_cloud(env)
    summary = sync.sync(env.ws, "jira")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 2)
    assert all(c["headers"]["Authorization"] == "Bearer test-access" for c in rec.calls)
    deploys = item(env, "jira", "issue-10001")
    assert deploys["title"] == "ENG-1: Deploys fail on Fridays"
    assert deploys["url"] == "https://acme.atlassian.net/browse/ENG-1"
    assert deploys["attrs"]["status"] == "Done" and deploys["attrs"]["labels"] == ["deploys"]
    text = env.text("jira", deploys["file_name"])
    assert text.startswith("ENG-1: Deploys fail on Fridays\n\nProject: Engineering\nType: Bug\n"
                           "Status: Done\nPriority: High\nAssignee: Ann Lee\nReporter: Bob Ray\n"
                           "Labels: deploys\n")
    # ADF -> Markdown the chunker understands: heading, fenced code, table rows.
    assert ("## What happens\n\nThe `deploy` job times out; see the runbook "
            "(https://wiki.acme.com/run). Ping @Bob\n\n```bash\nmake deploy ENV=prod\n"
            "make verify\n```\n\nDay | Result\nFriday | fails\n\n- retry once\n"
            "- then page on-call") in text
    assert "### Bob Ray, 2026-10-09\n\nFixed by raising the timeout to 20 minutes." in text
    # A comment restricted to a role is narrower than the issue: left out.
    assert "hunter2" not in text
    # Outside the backfill window and never synced: not listed as an item.
    assert set(sync_store.items(env.ws, "jira")) == {"issue-10001", "issue-10002"}
    cursor = json.loads(sync_store.state(env.ws, "jira")["cursor"])
    assert cursor["watermark"] == "2026-10-12T09:00:00.000+0000"


def test_acl_captures_project_browse_grants_and_the_security_level(env, monkeypatch):
    from kb.services import sync
    keep_old_issues_out(monkeypatch)
    Recorded(monkeypatch, "jira/cloud_full")
    connect_cloud(env)
    sync.sync(env.ws, "jira")
    read = {"public": False, "users": ["atlassian:acc-cy"], "groups": ["atlassian:group:grp-eng"],
            "roles": ["atlassian:role:10002"], "dynamic": ["reporter"]}
    assert item(env, "jira", "issue-10001")["acl"] == {
        "project": "ENG", "project_read": read, "security_level": None, "restricted": False}
    assert item(env, "jira", "issue-10002")["acl"] == {
        "project": "ENG", "project_read": read,
        "security_level": {"id": "10100", "name": "HR only"}, "restricted": True}


def test_then_only_what_jql_says_changed_is_fetched(env, monkeypatch):
    from kb.services import sync
    keep_old_issues_out(monkeypatch)
    rec = Recorded(monkeypatch, "jira/cloud_full")
    connect_cloud(env)
    sync.sync(env.ws, "jira")
    env.changed.clear()
    rec.load("jira/cloud_incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "jira")
    assert rec.unused() == []
    calls = rec.calls[before:]
    # One search from the watermark moved back by more than any time zone; no
    # permission scheme this time; only the changed issue's comments.
    [search] = [c for c in calls if c["url"].endswith("/search/jql")]
    assert 'updated >= "2026-10-11 18:50"' in search["params"]["jql"]
    assert not [c for c in calls if "permissionscheme" in c["url"]]
    assert (summary["mode"], summary["upserted"], summary["unchanged"]) == ("incremental", 1, 1)
    payroll = env.text("jira", item(env, "jira", "issue-10002")["file_name"])
    assert "on the 25th" in payroll and "Status: Done" in payroll
    assert env.changed == [[f"connectors/jira/{item(env, 'jira', 'issue-10002')['file_name']}"]]


def test_a_full_listing_finds_deletes_and_holds_a_mass_delete(env, monkeypatch):
    import sync_store
    from connectors import jira
    from kb.services import sync
    keep_old_issues_out(monkeypatch)
    rec = Recorded(monkeypatch, "jira/cloud_full")
    connect_cloud(env)
    sync.sync(env.ws, "jira")
    state = json.loads(sync_store.state(env.ws, "jira")["cursor"])
    sync_store.save_state(env.ws, "jira", cursor=json.dumps(
        {**state, "full_at": time.time() - jira.RECONCILE - 1}))

    def only_eng_2(rec):
        rec.add("GET", f"{API}/project/ENG/permissionscheme", json_body={"permissions": []})
        rec.add("GET", f"{API}/search/jql", json_body={"issues": [
            json.loads(json.dumps(rec_issue_2))], "isLast": True})

    rec_issue_2 = next(ex["response"]["json"]["issues"][1] for ex in rec.exchanges
                       if ex["request"]["url"].endswith("/search/jql")
                       and len(ex["response"]["json"]["issues"]) == 2)
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "1")
    only_eng_2(rec)
    summary = sync.sync(env.ws, "jira")
    assert (summary["mode"], summary["held_deletes"], summary["deleted"]) == ("full", 1, 0)
    assert item(env, "jira", "issue-10001") is not None
    only_eng_2(rec)
    summary = sync.sync(env.ws, "jira", confirm_deletes=True)
    assert summary["deleted"] == 1 and item(env, "jira", "issue-10001") is None
    # The scheme now grants nobody browse: captured as such, not as unknown.
    assert item(env, "jira", "issue-10002")["acl"]["project_read"]["groups"] == []


def test_without_permission_to_read_the_scheme_project_read_is_unknown(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_cloud(env)
    rec.add("GET", f"{API}/project/ENG/permissionscheme", status=403,
            json_body={"errorMessages": ["You don't have permission"]})
    rec.add("GET", f"{API}/search/jql", json_body={"issues": [{
        "id": "10009", "key": "ENG-9", "fields": {"summary": "x", "updated":
                                                  "2026-10-26T09:00:00.000+0000"}}],
        "isLast": True})
    rec.add("GET", f"{API}/issue/10009/comment", json_body={"comments": [], "total": 0})
    sync.sync(env.ws, "jira")
    acl = item(env, "jira", "issue-10009")["acl"]
    assert acl["project_read"] is None and acl["partial"] is True


def test_jira_rotates_its_refresh_token_and_reports_a_revoked_one(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    keep_old_issues_out(monkeypatch)
    monkeypatch.setenv("ATLASSIAN_CLIENT_ID", "cid")
    monkeypatch.setenv("ATLASSIAN_CLIENT_SECRET", "csecret")
    rec = Recorded(monkeypatch)
    connect_cloud(env, expires_at=time.time() - 10)
    rec.add("POST", TOKEN_URL, match_json={"grant_type": "refresh_token",
                                           "refresh_token": "test-refresh"},
            json_body={"access_token": "test-access-2", "refresh_token": "test-refresh-2",
                       "expires_in": 3600})
    rec.load("jira/cloud_full")
    sync.sync(env.ws, "jira")
    sec = integrations_store.secret(env.ws, "jira")
    assert (sec["access_token"], sec["refresh_token"]) == ("test-access-2", "test-refresh-2")
    assert rec.calls[1]["headers"]["Authorization"] == "Bearer test-access-2"
    integrations_store.update(env.ws, "jira", secret={**sec, "expires_at": 0})
    rec.add("POST", TOKEN_URL, status=403, json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "jira", full=True)
    assert card(env, "jira")["error"]["code"] == "auth_expired"


def test_a_429_is_waited_out_and_a_missing_scope_is_named(env, monkeypatch):
    from kb.services import sync
    keep_old_issues_out(monkeypatch)
    rec = Recorded(monkeypatch)
    connect_cloud(env)
    rec.add("GET", f"{API}/project/ENG/permissionscheme", status=429,
            headers={"Retry-After": "3"})
    rec.load("jira/cloud_full")
    sync.sync(env.ws, "jira")
    assert 3 in rec.slept
    rec.add("GET", f"{API}/project/ENG/permissionscheme", status=401, repeat=True,
            text='{"code":401,"message":"Unauthorized; scope does not match"}')
    with pytest.raises(base.MissingScope):
        sync.sync(env.ws, "jira", full=True)
    assert card(env, "jira")["error"]["code"] == "missing_scope"


# ---- Data Center -----------------------------------------------------------------------
def test_data_center_connects_with_a_token_and_pages_by_start_at(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "jira/dc_setup_and_sync")
    headers = env.headers()
    r = env.client.post("/api/integrations/jira_dc/setup", headers=headers, json={
        "url": "https://jira.acme.com/rest/api/2/", "token": "test-pat"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "account": "jira.acme.com (Ann Lee)", "choose": True}
    assert card(env, "jira_dc")["base_url"] == "https://jira.acme.com"
    r = env.client.get("/api/integrations/jira_dc/containers", headers=headers)
    assert [c["id"] for c in r.json()["items"]] == ["OPS", "HR"]
    env.client.put("/api/integrations/jira_dc/containers", headers=headers,
                   json={"containers": [{"id": "OPS", "name": "Operations"}]})
    summary = sync.sync(env.ws, "jira_dc")
    assert rec.unused() == []
    assert summary["upserted"] == 2
    assert all(c["headers"]["Authorization"] == "Bearer test-pat" for c in rec.calls)
    tls = item(env, "jira_dc", "issue-20001")
    assert tls["url"] == "https://jira.acme.com/browse/OPS-1"
    text = env.text("jira_dc", tls["file_name"])
    assert "## Description\n\nCertificates expire every 90 days.\nRun certbot renew." in text
    assert "### Bob Ray, 2026-10-01\n\nDone for October." in text
    # No right to read the permission scheme: unknown, not "nobody".
    assert tls["acl"]["project_read"] is None and tls["acl"]["partial"] is True
    assert integrations_store.get(env.ws, "jira_dc")["status"] == "connected"


def test_data_center_rejects_bad_tokens_and_private_addresses(env, monkeypatch):
    rec = Recorded(monkeypatch)
    rec.add("GET", "https://jira.acme.com/rest/api/2/myself", status=401)
    r = env.client.post("/api/integrations/jira_dc/setup", headers=env.headers(),
                        json={"url": "https://jira.acme.com", "token": "wrong"})
    assert r.status_code == 400 and r.json()["detail"] == "Jira didn't accept that token."
    rec.hosts["jira.internal"] = "10.0.0.7"
    r = env.client.post("/api/integrations/jira_dc/setup", headers=env.headers(),
                        json={"url": "https://jira.internal", "token": "test-pat"})
    assert r.status_code == 400 and "private network" in r.json()["detail"]


# ---- ADF -------------------------------------------------------------------------------
def test_adf_reaches_the_chunker_with_its_structure():
    import chunking
    from connectors import jira
    rows = [{"type": "tableRow", "content": [
        {"type": "tableCell", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": f"Plan {i} with a longer description of its features"}]}]},
        {"type": "tableCell", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": str(i * 100)}]}]}]} for i in range(60)]
    header = {"type": "tableRow", "content": [
        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "Plan"}]}]},
        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "Price"}]}]}]}
    doc = {"type": "doc", "version": 1, "content": [
        {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Prices"}]},
        {"type": "codeBlock", "content": [{"type": "text", "text": "make build\nmake deploy"}]},
        {"type": "table", "content": [header, *rows]},
        {"type": "taskList", "content": [
            {"type": "taskItem", "attrs": {"state": "DONE"},
             "content": [{"type": "text", "text": "ship it"}]}]}]}
    text = jira.adf_to_markdown(doc)
    assert text.startswith("# Prices\n\n```\nmake build\nmake deploy\n```\n\nPlan | Price\n"
                           "Plan 0 with a longer description of its features | 0\n")
    assert text.endswith("\n\n[x] ship it")
    tables = [p for p in chunking.split(text) if p.kind == "table"]
    assert len(tables) > 1 and all(p.prefix == "Plan | Price" for p in tables[1:])


def test_jira_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["jira"] == by_id["jira_dc"] == "live"
