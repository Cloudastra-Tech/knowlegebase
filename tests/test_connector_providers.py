"""
Each live connector against recorded HTTP (tests/fixtures/connectors/<provider>/,
replayed by tests/connector_fixtures.py; an unrecorded request fails the test,
so nothing reaches the network), run through the real framework
(kb/services/sync.py): what is listed, fetched, written, deleted and re-indexed,
cursors and incremental runs, token refresh, and for the website crawler
robots.txt, conditional requests, limits and the SSRF guard.
"""

import json
import os
import time
import uuid

import pytest

from connector_fixtures import Recorded, make_docx, make_pdf
from connectors import base


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A workspace, its admin's API headers, and re-index requests recorded."""
    from fastapi.testclient import TestClient
    import api
    import auth
    import members_store
    from kb.services import sync
    monkeypatch.chdir(tmp_path)
    reindexed = []
    monkeypatch.setattr(sync.indexing, "schedule_index",
                        lambda ws, docs=None, full=False: reindexed.append(sorted(docs or [])))
    monkeypatch.setattr(sync, "start_sync", lambda *a, **kw: None)   # tests run syncs themselves

    class Env:
        ws = f"w-{uuid.uuid4().hex[:8]}"
        client = TestClient(api.app)
        changed = reindexed

        def headers(self):
            members_store.set_password(f"admin@{self.ws}.example", "Admin",
                                       auth.hash_password("correct-horse"), self.ws, "admin")
            r = self.client.post("/api/auth/login", json={"username": f"admin@{self.ws}.example",
                                                          "password": "correct-horse"})
            return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}

        def text(self, provider, name):
            with open(f"data/workspaces/{self.ws}/docs/connectors/{provider}/{name}",
                      encoding="utf-8") as f:
                return f.read()

        def files(self, provider):
            folder = f"data/workspaces/{self.ws}/docs/connectors/{provider}"
            return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    return Env()


# ---- Google Drive ----------------------------------------------------------------------
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def connect_drive(env, expires_at=None):
    import integrations_store
    integrations_store.connect(env.ws, "google_drive", "ann@acme.com", {
        "refresh_token": "test-refresh", "access_token": "test-access",
        "expires_at": expires_at or time.time() + 3600}, "admin")
    integrations_store.update(env.ws, "google_drive", settings={
        "folders": [{"id": "fold-root", "name": "Handbook", "kind": "folder"}]})


def drive_full(rec):
    rec.load("google_drive/full_sync")
    rec.add("GET", "https://www.googleapis.com/drive/v3/files/pdf1", params={"alt": "media"},
            body=make_pdf("Expenses over 100 need a receipt."),
            headers={"Content-Type": "application/pdf"})
    rec.add("GET", "https://www.googleapis.com/drive/v3/files/docx1", params={"alt": "media"},
            body=make_docx(["Day one: meet your buddy."], heading="Onboarding"))


def test_drive_first_sync_lists_the_picked_tree_and_reads_every_type(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    drive_full(rec)
    connect_drive(env)
    summary = sync.sync(env.ws, "google_drive")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"], summary["skipped"]) == ("full", 4, 1)
    assert env.files("google_drive") == ["doc1.txt", "docx1.txt", "pdf1.pdf", "sheet1.txt"]
    assert env.text("google_drive", "doc1.txt") == "Travel policy\n\nTrips need approval."
    assert env.text("google_drive", "docx1.txt") == ("Onboarding.docx\n\n# Onboarding\n"
                                                     "Day one: meet your buddy.")
    assert "EU,150" in env.text("google_drive", "sheet1.txt")
    # Too big to download: listed as skipped, not quietly left out.
    assert sync_store.item(env.ws, "google_drive", "big1")["skipped_reason"] == "too_large"
    assert not rec.requests_to("files/big1")
    # Every call carried the token, and asked for Shared Drive items too.
    assert all(c["headers"]["Authorization"] == "Bearer test-access" for c in rec.calls)
    # Permissions captured (not enforced): users, groups, domains.
    acl = sync_store.item(env.ws, "google_drive", "doc1")["acl"]
    assert acl == {"users": ["ann@acme.com"], "groups": ["google:eng@acme.com"],
                   "domains": ["acme.com"], "anyone": False}
    cursor = json.loads(sync_store.state(env.ws, "google_drive")["cursor"])
    assert cursor["token"] == "100" and set(cursor["folders"]) == {"fold-root", "fold-sub"}
    assert env.changed == [[f"connectors/google_drive/{n}" for n in
                            ("doc1.txt", "docx1.txt", "pdf1.pdf", "sheet1.txt")]]


def test_drive_then_syncs_only_the_change_feed(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    drive_full(rec)
    connect_drive(env)
    sync.sync(env.ws, "google_drive")
    env.changed.clear()
    rec.load("google_drive/changes")
    summary = sync.sync(env.ws, "google_drive")
    assert rec.unused() == []
    assert summary["mode"] == "incremental"
    # doc1 edited (and renamed); pdf1 removed; docx1 moved out of the picks;
    # txt1 new in a picked sub-folder; other1 elsewhere: ignored.
    assert env.files("google_drive") == ["doc1.txt", "sheet1.txt", "txt1.txt"]
    assert env.text("google_drive", "doc1.txt").startswith("Travel policy (2026)\n\n")
    assert not rec.requests_to("files/other1")
    assert (summary["upserted"], summary["deleted"]) == (2, 2)
    assert env.changed == [[f"connectors/google_drive/{n}" for n in
                            ("doc1.txt", "docx1.txt", "pdf1.pdf", "txt1.txt")]]
    assert json.loads(sync_store.state(env.ws, "google_drive")["cursor"])["token"] == "105"

    # A picked-under folder moved away: the tree is listed again, its files go.
    env.changed.clear()
    rec.load("google_drive/folder_moved")
    summary = sync.sync(env.ws, "google_drive")
    assert rec.unused() == []
    assert summary["mode"] == "full" and summary["deleted"] == 2
    assert env.files("google_drive") == ["doc1.txt"]
    assert json.loads(sync_store.state(env.ws, "google_drive")["cursor"])["token"] == "107"


def test_drive_changed_picks_list_everything_again(env, monkeypatch):
    import integrations_store
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    drive_full(rec)
    connect_drive(env)
    sync.sync(env.ws, "google_drive")
    integrations_store.update(env.ws, "google_drive", settings={"folders": []})
    summary = sync.sync(env.ws, "google_drive", confirm_deletes=True)
    assert summary["deleted"] == 4 and env.files("google_drive") == []
    assert sync_store.state(env.ws, "google_drive")["cursor"] is None


def test_drive_refreshes_an_expired_token_and_reports_a_revoked_one(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_drive(env, expires_at=time.time() - 10)
    rec.add("POST", "https://oauth2.googleapis.com/token",
            json_body={"access_token": "test-access-2", "expires_in": 3599})
    drive_full(rec)
    sync.sync(env.ws, "google_drive")
    assert integrations_store.secret(env.ws, "google_drive")["access_token"] == "test-access-2"
    assert rec.calls[1]["headers"]["Authorization"] == "Bearer test-access-2"

    # Revoked: Google answers invalid_grant. The card says to reconnect.
    integrations_store.update(env.ws, "google_drive", secret={
        "refresh_token": "test-refresh", "access_token": "x", "expires_at": 0})
    rec.add("POST", "https://oauth2.googleapis.com/token", status=400,
            json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "google_drive", full=True)
    item = next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "google_drive")
    assert item["error"] == {"code": "auth_expired", "message": base.ERRORS["auth_expired"]}


def test_drive_picker_lists_shared_drives_folders_and_files(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect_drive(env)
    rec.add("GET", "https://www.googleapis.com/drive/v3/drives",
            json_body={"drives": [{"id": "sd1", "name": "Company"}]})
    rec.add("GET", "https://www.googleapis.com/drive/v3/files", params={"q~": "'root' in parents"},
            json_body={"files": [{"id": "f1", "name": "Handbook",
                                  "mimeType": "application/vnd.google-apps.folder"},
                                 {"id": "d1", "name": "Plan", "mimeType": DOCX_MIME}]})
    r = env.client.get("/api/integrations/google_drive/containers", headers=env.headers())
    assert r.status_code == 200, r.text
    assert [(i["id"], i["kind"]) for i in r.json()["items"]] == [
        ("sd1", "drive"), ("f1", "folder"), ("d1", "file")]


def test_a_failed_picker_listing_is_explained_not_dumped(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect_drive(env, expires_at=time.time() - 10)
    rec.add("POST", "https://oauth2.googleapis.com/token", status=400,
            json_body={"error": "invalid_grant", "error_description": "Token secret-xyz"})
    r = env.client.get("/api/integrations/google_drive/containers", headers=env.headers())
    assert r.status_code == 502
    assert r.json()["detail"] == base.ERRORS["auth_expired"]


# ---- Notion ------------------------------------------------------------------------------
def connect_notion(env, settings=None):
    import integrations_store
    integrations_store.connect(env.ws, "notion", "Acme", {
        "access_token": "test-notion-access", "refresh_token": "test-notion-refresh"}, "admin")
    if settings:
        integrations_store.update(env.ws, "notion", settings=settings)


def test_notion_full_sync_uses_the_current_api_version(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "notion/full_sync")
    connect_notion(env)
    summary = sync.sync(env.ws, "notion")
    assert rec.unused() == []
    assert summary["upserted"] == 3
    assert all(c["headers"]["Notion-Version"] == "2025-09-03" for c in rec.calls)
    assert env.text("notion", "page-a.txt") == ("Handbook\n\n# Welcome\nRead this first.\n"
                                                "- Details\nHidden detail")
    # A database row (parent: a data source) keeps its properties.
    assert env.text("notion", "row-1.txt") == ("Laptop request\n\nStatus: Approved\nOwner: Ann")
    assert sync_store.item(env.ws, "notion", "row-1")["parent_id"] == "ds-1"
    watermark = json.loads(sync_store.state(env.ws, "notion")["cursor"])["watermark"]
    assert watermark == "2026-10-20T10:00:00.000Z"


def test_notion_incremental_stops_at_the_watermark(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "notion/full_sync")
    connect_notion(env)
    sync.sync(env.ws, "notion")
    env.changed.clear()
    rec.load("notion/incremental")
    summary = sync.sync(env.ws, "notion")
    assert rec.unused() == []
    assert summary["mode"] == "incremental" and summary["upserted"] == 1
    assert len([c for c in rec.calls if c["url"].endswith("/search")]) == 2   # no 2nd page
    assert "25 days" in env.text("notion", "page-b.txt")
    assert env.changed == [["connectors/notion/page-b.txt"]]


def test_notion_full_listing_every_so_often_finds_deletes(env, monkeypatch):
    import sync_store
    from connectors import notion
    from kb.services import sync
    rec = Recorded(monkeypatch, "notion/full_sync")
    connect_notion(env)
    sync.sync(env.ws, "notion")
    state = json.loads(sync_store.state(env.ws, "notion")["cursor"])
    sync_store.save_state(env.ws, "notion", cursor=json.dumps(
        {**state, "full_at": time.time() - notion.RECONCILE - 1}))
    rec.add("POST", "https://api.notion.com/v1/search", json_body={
        "results": [], "has_more": False})
    summary = sync.sync(env.ws, "notion", confirm_deletes=True)
    assert summary["mode"] == "full" and summary["deleted"] == 3


def test_notion_picked_pages_narrow_what_is_synced(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "notion/full_sync")
    connect_notion(env, {"pages": [{"id": "page-a", "name": "Handbook", "kind": "page"}]})
    rec.add("GET", "https://api.notion.com/v1/data_sources/ds-1", json_body={
        "object": "data_source", "id": "ds-1",
        "parent": {"type": "database_id", "database_id": "db-1"}})
    rec.add("GET", "https://api.notion.com/v1/databases/db-1", json_body={
        "object": "database", "id": "db-1", "parent": {"type": "workspace", "workspace": True}})
    sync.sync(env.ws, "notion")
    # page-b is under page-a; the database row isn't.
    assert env.files("notion") == ["page-a.txt", "page-b.txt"]


def test_notion_refreshes_a_rejected_token_and_keeps_the_new_one(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    monkeypatch.setenv("NOTION_CLIENT_ID", "cid")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "csecret")
    rec = Recorded(monkeypatch, "notion/refresh")
    connect_notion(env)
    sync.sync(env.ws, "notion")
    assert rec.unused() == []
    assert integrations_store.secret(env.ws, "notion") == {
        "access_token": "test-notion-access-2", "refresh_token": "test-notion-refresh-2"}
    token_call = rec.requests_to("oauth/token")[0]
    assert token_call["headers"]["Authorization"].startswith("Basic ")


def test_notion_calls_are_spaced_per_connection(env, monkeypatch):
    """Two connections don't share one spacing: each has its own 3-a-second bucket."""
    from connectors import notion
    rec = Recorded(monkeypatch)
    connect_notion(env)
    for _ in range(6):
        rec.add("POST", "https://api.notion.com/v1/search", json_body={"results": []},
                repeat=True)
    start = rec.now
    for _ in range(6):
        notion.CONNECTOR._call(env.ws, "POST", "/search", json={})
    first = rec.now - start
    assert first == pytest.approx(1.0)                 # 3 at once, then 3 more at 3/s
    import integrations_store
    integrations_store.connect("other-ws", "notion", "B", {"access_token": "b"}, "admin")
    start = rec.now
    notion.CONNECTOR._call("other-ws", "POST", "/search", json={})
    assert rec.now == start
    integrations_store.remove("other-ws", "notion")


# ---- Outline ----------------------------------------------------------------------------
def test_outline_is_connected_per_workspace_with_a_token_kept_encrypted(env, monkeypatch):
    import db
    import integrations_store
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "outline/setup_and_sync")
    headers = env.headers()
    r = env.client.post("/api/integrations/outline/setup", headers=headers, json={
        "url": "https://wiki.example.com/", "token": "test-outline-token"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "account": "Acme wiki (ann@acme.com)", "choose": True}
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 'outline'", (env.ws,)).fetchone()[0]
    assert b"test-outline-token" not in blob                     # encrypted (secret_box)
    assert integrations_store.secret(env.ws, "outline") == {"token": "test-outline-token"}
    import audit_store
    [event] = audit_store.list_events(env.ws, action="integration.connect")[0]
    assert event["details"] == {"account": "Acme wiki (ann@acme.com)",
                                "url": "https://wiki.example.com/"}
    assert "test-outline-token" not in json.dumps(event)        # the address, never the token

    r = env.client.get("/api/integrations/outline/containers", headers=headers)
    assert [c["name"] for c in r.json()["items"]] == ["Engineering", "HR"]
    r = env.client.put("/api/integrations/outline/containers", headers=headers, json={
        "containers": [{"id": "col-eng", "name": "Engineering", "kind": "collection"}]})
    assert r.status_code == 200
    summary = sync.sync(env.ws, "outline")
    assert rec.unused() == []
    assert summary["upserted"] == 2
    assert env.text("outline", "doc-1.txt") == "Deploys\n\n# Deploys\n\nWe deploy on **Tuesdays**."
    item = sync_store.item(env.ws, "outline", "doc-1")
    assert item["url"] == "https://wiki.example.com/doc/deploys-AbC123"
    assert item["acl"] == {"collection": "col-eng", "collection_permission": None}

    # Next time: only documents updated since (the list is newest first).
    env.changed.clear()
    rec.load("outline/incremental")
    summary = sync.sync(env.ws, "outline")
    # doc-3 is new, doc-1 is at the watermark (unchanged), doc-2 older: not looked at.
    assert (summary["mode"], summary["upserted"], summary["unchanged"]) == ("incremental", 1, 1)
    assert env.changed == [["connectors/outline/doc-3.txt"]]


@pytest.mark.parametrize("url, host_ip, message", [
    ("http://wiki.example.com", None, "https"),
    ("https://wiki.internal", "10.0.0.7", "private network"),
    ("https://metadata.example.com", "169.254.169.254", "private network"),
])
def test_outline_refuses_addresses_the_server_must_not_call(env, monkeypatch, url, host_ip,
                                                            message):
    rec = Recorded(monkeypatch)
    if host_ip:
        rec.hosts[url.split("//")[1]] = host_ip
    r = env.client.post("/api/integrations/outline/setup", headers=env.headers(),
                        json={"url": url, "token": "test-outline-token"})
    assert r.status_code == 400 and message in r.json()["detail"]
    assert rec.calls == []


def test_outline_rejects_a_bad_token_in_plain_words(env, monkeypatch):
    rec = Recorded(monkeypatch)
    rec.add("POST", "https://wiki.example.com/api/auth.info", status=401)
    r = env.client.post("/api/integrations/outline/setup", headers=env.headers(),
                        json={"url": "https://wiki.example.com", "token": "wrong"})
    assert r.status_code == 400 and r.json()["detail"] == "Outline didn't accept that token."


# ---- Website crawler ---------------------------------------------------------------------
def connect_site(env, urls=("https://docs.example.com/",), **limits):
    import integrations_store
    integrations_store.connect(env.ws, "website", "docs.example.com", {}, "admin")
    integrations_store.update(env.ws, "website", settings={
        "urls": [{"id": u, "name": u, "kind": "url"} for u in urls], **limits})


def test_the_crawler_follows_the_site_obeys_robots_and_skips_noindex(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "website/site")
    connect_site(env)
    summary = sync.sync(env.ws, "website")
    assert rec.unused() == []
    pages = {i["url"]: i for i in sync_store.items(env.ws, "website").values()}
    assert sorted(pages) == ["https://docs.example.com/", "https://docs.example.com/guide/",
                             "https://docs.example.com/pricing"]
    assert summary["upserted"] == 3
    assert not rec.requests_to("/private")                 # robots.txt: Disallow
    assert not rec.requests_to("other.example.org")        # another site
    assert not rec.requests_to("logo.png")
    home = pages["https://docs.example.com/"]
    assert home["title"] == "Acme Docs"
    text = env.text("website", home["file_name"])
    assert text.startswith("Acme Docs\n\n# Welcome") and "Pricing" not in text   # nav dropped
    assert all(c["headers"]["User-Agent"].startswith("KnowledgeAssistantBot") for c in rec.calls)
    # Crawl-delay: 2 -> the site's bucket allows one request every 2 seconds.
    from connectors import http
    assert {b.rate for b in http._buckets.values()} >= {0.5}


def test_a_recrawl_asks_conditionally_and_deletes_gone_pages(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "website/site")
    connect_site(env)
    sync.sync(env.ws, "website")
    env.changed.clear()
    rec.load("website/recrawl")
    summary = sync.sync(env.ws, "website")
    assert rec.unused() == []
    assert (summary["unchanged"], summary["deleted"], summary["upserted"]) == (2, 1, 0)
    assert sorted(i["url"] for i in sync_store.items(env.ws, "website").values()) == [
        "https://docs.example.com/", "https://docs.example.com/pricing"]
    assert len(env.changed) == 1 and len(env.changed[0]) == 1      # just the deleted page


def test_a_sitemap_lists_the_pages_to_take(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_site(env, urls=("https://docs.example.com/sitemap.xml",))
    rec.add("GET", "https://docs.example.com/robots.txt", status=404)
    rec.add("GET", "https://docs.example.com/sitemap.xml", text=(
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://docs.example.com/a</loc><lastmod>2026-10-01</lastmod></url>"
        "<url><loc>https://other.example.org/b</loc></url>"
        "<url><loc>/c</loc></url></urlset>"))
    for path in ("a", "c"):
        rec.add("GET", f"https://docs.example.com/{path}",
                text=f"<html><head><title>{path.upper()}</title></head><body><p>Page {path}"
                     f" <a href='/never'>x</a></p></body></html>")
    sync.sync(env.ws, "website")
    assert rec.unused() == []
    assert sorted(i["title"] for i in sync_store.items(env.ws, "website").values()) == ["A", "C"]
    assert not rec.requests_to("/never")                   # sitemap pages aren't followed


def test_the_page_limit_stops_the_crawl_and_deletes_nothing(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "website/site")
    connect_site(env)
    sync.sync(env.ws, "website")
    rec2 = Recorded(monkeypatch, "website/site")
    connect_site(env, max_pages=1)
    summary = sync.sync(env.ws, "website")
    assert summary["limit_reached"] == 1 and summary["deleted"] == 0
    assert len(sync_store.items(env.ws, "website")) == 3
    assert rec2.requests_to("/pricing") == []


def test_depth_limits_how_far_links_are_followed(env, monkeypatch):
    import sync_store
    from kb.services import sync
    Recorded(monkeypatch, "website/site")
    connect_site(env, max_depth=1)
    sync.sync(env.ws, "website")
    assert len(sync_store.items(env.ws, "website")) == 3       # / and its links, not deeper


def test_robots_txt_that_errors_stops_the_run_without_deleting(env, monkeypatch):
    import sync_store
    from kb.services import sync
    Recorded(monkeypatch, "website/site")
    connect_site(env)
    sync.sync(env.ws, "website")
    rec = Recorded(monkeypatch)
    for _ in range(5):
        rec.add("GET", "https://docs.example.com/robots.txt", status=503)
    with pytest.raises(base.Transient):
        sync.sync(env.ws, "website")
    assert len(sync_store.items(env.ws, "website")) == 3


def test_the_crawler_never_leaves_the_site_or_reaches_private_addresses(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    rec.hosts["evil.example.com"] = "10.0.0.9"
    connect_site(env)
    rec.add("GET", "https://docs.example.com/robots.txt", status=404)
    rec.add("GET", "https://docs.example.com/", status=302,
            headers={"Location": "https://evil.example.com/admin"})
    summary = sync.sync(env.ws, "website")
    assert summary["upserted"] == 0
    assert not rec.requests_to("evil.example.com")

    # A start address that resolves to a private network is refused when it's saved...
    rec.hosts["intranet.example.com"] = "192.168.1.10"
    r = env.client.post("/api/integrations/website/setup", headers=env.headers(),
                        json={"url": "https://intranet.example.com/"})
    assert r.status_code == 400 and "private network" in r.json()["detail"]
    # ...and if its DNS changes later, before every request.
    rec.hosts["docs.example.com"] = "127.0.0.1"
    calls = len(rec.calls)
    with pytest.raises(base.ConfigError):
        sync.sync(env.ws, "website")
    assert len(rec.calls) == calls


def test_the_website_setup_saves_limits_and_lists_its_urls(env, monkeypatch):
    import integrations_store
    Recorded(monkeypatch)
    headers = env.headers()
    r = env.client.post("/api/integrations/website/setup", headers=headers,
                        json={"url": "docs.example.com/start", "max_pages": 50, "max_depth": 2})
    assert r.status_code == 200, r.text
    settings = integrations_store.get(env.ws, "website")["settings"]
    assert settings["urls"] == [{"id": "https://docs.example.com/start",
                                 "name": "https://docs.example.com/start", "kind": "url"}]
    assert (settings["max_pages"], settings["max_depth"]) == (50, 2)
    r = env.client.put("/api/integrations/website/containers", headers=headers, json={
        "containers": [{"id": "https://docs.example.com/start"},
                       {"id": "http://insecure.example.com/"}]})
    assert r.status_code == 400 and "https" in r.json()["detail"]
    card = next(i for i in env.client.get("/api/integrations", headers=headers)
                .json()["integrations"] if i["id"] == "website")
    assert card["limits"] == {"max_pages": 50, "max_depth": 2}
    assert card["containers"][0]["id"] == "https://docs.example.com/start"
