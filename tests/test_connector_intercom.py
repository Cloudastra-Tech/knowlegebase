"""
Intercom against recorded HTTP (tests/fixtures/connectors/intercom/, written
from Intercom's OpenAPI description (2.14) and API reference; an unrecorded
request fails the test), through the real framework (kb/services/sync.py):
connecting with a private app's token or OAuth on the workspace's region, the
Help Center / collection picker, the newest-first article listing and where an
increment stops, deletes behind the mass-delete guard, the captured acl (and
why the source policy isn't offered), closed conversations (off by default,
locked to HR and admins), 429s with X-RateLimit-Reset and typed errors.
"""

import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

I = "https://api.eu.intercom.io"
ME = {"type": "admin", "id": "991", "name": "Ann Lee", "email": "ann@acme.com",
      "app": {"type": "app", "id_code": "abc123", "name": "Acme", "region": "EU"}}


def connect_token(env, **settings):
    import integrations_store
    integrations_store.connect(env.ws, "intercom", "Acme (ann@acme.com)",
                               {"access_token": "ic-token", "oauth": False}, "admin")
    integrations_store.update(env.ws, "intercom", settings={"region": "EU", "app": "abc123",
                                                            **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "intercom", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "intercom")


# ---- connecting ------------------------------------------------------------------------
def test_a_private_apps_token_connects_and_finds_its_region(env, monkeypatch):
    import db
    rec = Recorded(monkeypatch)
    rec.add("GET", "https://api.intercom.io/me", json_body=ME,
            req_headers={"Authorization": "Bearer ic-token", "Intercom-Version": "2.14"})
    r = env.client.post("/api/integrations/intercom/setup", headers=env.headers(),
                        json={"url": "US", "token": "ic-token"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "account": "Acme (ann@acme.com)", "choose": False}
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 'intercom'", (env.ws,)).fetchone()[0]
    assert b"ic-token" not in blob
    c = card(env)
    # Hosted in the EU: every later call goes there. Audience targeting isn't in the
    # API, so "as shared at the source" isn't offered.
    assert (c["base_url"], c["acl"], c["policies"], c["policy"]) == (
        "EU", False, ["workspace", "restricted"], "restricted")
    assert "audience" in c["source_policy_note"]
    r = env.client.put("/api/integrations/intercom/policy", headers=env.headers(),
                       json={"policy": "source"})
    assert r.status_code == 400


def test_a_bad_token_is_refused_in_plain_words(env, monkeypatch):
    rec = Recorded(monkeypatch)
    rec.add("GET", "https://api.intercom.io/me", status=401,
            json_body={"type": "error.list", "errors": [{"code": "unauthorized"}]})
    r = env.client.post("/api/integrations/intercom/setup", headers=env.headers(),
                        json={"url": "US", "token": "nope"})
    assert r.status_code == 400 and r.json()["detail"] == "Intercom didn't accept that access token."


def test_oauth_signs_in_on_the_regions_host(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    headers = env.headers()
    assert env.client.post("/api/integrations/intercom/connect", headers=headers,
                           json={"url": "EU"}).status_code == 400   # no server app: a form
    monkeypatch.setenv("INTERCOM_CLIENT_ID", "ic-client")
    monkeypatch.setenv("INTERCOM_CLIENT_SECRET", "ic-secret")
    assert env.client.post("/api/integrations/intercom/connect", headers=headers,
                           json={"url": "Mars"}).status_code == 400
    url = env.client.post("/api/integrations/intercom/connect", headers=headers,
                          json={"url": "EU"}).json()["url"]
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.netloc, parts.path, q["client_id"]) == ("app.eu.intercom.com", "/oauth",
                                                          "ic-client")
    rec.add("POST", "https://api.intercom.io/auth/eagle/token",
            match_json={"code": "c0de=", "client_id": "ic-client", "client_secret": "ic-secret"},
            json_body={"token_type": "Bearer", "token": "ic-oauth", "access_token": "ic-oauth"})
    rec.add("GET", "https://api.intercom.io/me", json_body=ME,
            req_headers={"Authorization": "Bearer ic-oauth"})
    r = env.client.get("/api/integrations/intercom/callback",
                       params={"state": q["state"], "code": "c0de="}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?connected=intercom"
    assert integrations_store.secret(env.ws, "intercom") == {"access_token": "ic-oauth",
                                                             "oauth": True}


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_is_help_centers_then_collections(env, monkeypatch):
    Recorded(monkeypatch, "intercom/full")
    connect_token(env)
    headers = env.headers()
    top = env.client.get("/api/integrations/intercom/containers", headers=headers).json()["items"]
    assert [(c["id"], c["name"]) for c in top] == [("hc-1", "Acme Help"),
                                                   ("hc-2", "Partner portal")]
    Recorded(monkeypatch, "intercom/full")
    inside = env.client.get("/api/integrations/intercom/containers?parent=hc-1",
                            headers=headers).json()["items"]
    assert [(c["id"], c["has_children"]) for c in inside] == [("collection-31", True)]
    r = env.client.put("/api/integrations/intercom/containers", headers=headers,
                       json={"containers": [{"id": "section-1", "name": "x"}]})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_lists_every_published_article(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "intercom/full")
    connect_token(env)
    summary = sync.sync(env.ws, "intercom")
    assert rec.unused() == []
    assert all(c["url"].startswith(I) for c in rec.calls)          # the EU region's host
    assert all(c["headers"]["Intercom-Version"] == "2.14" for c in rec.calls)
    assert (summary["mode"], summary["upserted"]) == ("full", 3)
    assert set(sync_store.items(env.ws, "intercom")) == {
        "article-700", "article-701", "article-703"}             # not the draft
    transfer = item(env, "article-703")
    assert env.text("intercom", transfer["file_name"]) == (
        "Pay by bank transfer\n\n## Bank transfer\n\nUse the IBAN on your invoice.")
    assert transfer["acl"] == {"via": "intercom", "kind": "article", "state": "published",
                               "help_center": "1", "help_center_live": True,
                               "collections": ["31", "32"], "audience": "not_exposed"}
    assert item(env, "article-700")["acl"]["help_center_live"] is False


def test_a_picked_collection_takes_its_subcollections(env, monkeypatch):
    import sync_store
    from kb.services import sync
    Recorded(monkeypatch, "intercom/full")
    connect_token(env, containers=[{"id": "collection-31", "name": "Getting started",
                                    "kind": "section"}])
    sync.sync(env.ws, "intercom")
    assert set(sync_store.items(env.ws, "intercom")) == {"article-701", "article-703"}


def test_an_increment_stops_at_the_first_article_older_than_the_last_run(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "intercom/full")
    connect_token(env)
    sync.sync(env.ws, "intercom")
    assert json.loads(sync_store.state(env.ws, "intercom")["cursor"])["watermark"] == 1790003000
    rec.load("intercom/incremental")
    summary = sync.sync(env.ws, "intercom")
    assert rec.unused() == []                                    # no second page asked for
    assert (summary["mode"], summary["upserted"], summary["deleted"]) == ("incremental", 2, 1)
    assert "two days" in env.text("intercom", item(env, "article-703")["file_name"])
    assert item(env, "article-704") is not None
    assert item(env, "article-701") is None                      # unpublished
    assert json.loads(sync_store.state(env.ws, "intercom")["cursor"])["watermark"] == 1790005000


def test_a_full_listing_that_lost_everything_is_held_for_an_admin(env, monkeypatch):
    import sync_store
    from connectors import intercom
    from kb.services import sync
    rec = Recorded(monkeypatch, "intercom/full")
    connect_token(env)
    sync.sync(env.ws, "intercom")
    state = json.loads(sync_store.state(env.ws, "intercom")["cursor"])
    sync_store.save_state(env.ws, "intercom", cursor=json.dumps(
        {**state, "full_at": time.time() - intercom.RECONCILE - 1}))
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.load("intercom/full")
    rec.exchanges = [e for e in rec.exchanges if e["used"] or "/articles" not in
                     e["request"]["url"]]
    rec.add("GET", f"{I}/articles", json_body={"type": "list", "data": [],
                                               "pages": {"page": 1, "total_pages": 0}})
    summary = sync.sync(env.ws, "intercom")
    assert (summary["held_deletes"], summary["deleted"]) == (3, 0)
    assert card(env)["held_deletes"] == 3


def test_conversations_are_off_until_turned_on_and_then_hr_and_admins_only(env, monkeypatch):
    import sync_store
    from kb.services import indexing, sync
    monkeypatch.setattr(indexing, "schedule_retag", lambda ws, p: None)
    rec = Recorded(monkeypatch, "intercom/full")
    connect_token(env, access="everyone")
    sync.sync(env.ws, "intercom")
    assert not rec.requests_to("/conversations")
    headers = env.headers()
    r = env.client.put("/api/integrations/intercom/containers", headers=headers,
                       json={"containers": [], "options": {"conversations": True}})
    assert r.status_code == 200, r.text
    c = card(env)
    assert (c["options"], c["access"], c["access_locked"]) == (
        {"conversations": True}, "restricted", True)
    r = env.client.put("/api/integrations/intercom/access", headers=headers,
                       json={"access": "everyone"})
    assert r.status_code == 400 and "conversations" in r.json()["detail"]

    rec.load("intercom/full")
    rec.load("intercom/conversations")
    summary = sync.sync(env.ws, "intercom", full=True)
    assert rec.unused() == []
    [first, second] = [c for c in rec.calls if c["url"].endswith("/conversations/search")]
    closed, since = first["json"]["query"]["value"]
    assert closed == {"field": "state", "operator": "=", "value": "closed"}
    assert since["field"] == "updated_at" and since["operator"] == ">"
    assert second["json"]["pagination"]["starting_after"] == "WzE3OTAwMDAwMDAsNzdd"
    assert summary["upserted"] == 2
    refund = item(env, "conversation-77")
    assert refund["acl"] == {"via": "intercom", "kind": "conversation"}
    assert refund["url"] == "https://app.eu.intercom.com/a/inbox/abc123/inbox/conversation/77"
    text = env.text("intercom", refund["file_name"])
    assert "Please refund order 1234." in text and "Refunded." in text
    assert "watch list" not in text                      # internal notes stay in Intercom
    assert "Jo Customer" not in text and "### Customer" in text   # customers aren't named
    assert item(env, "conversation-78")["title"] == "#78: Invoice address"
    cursor = json.loads(sync_store.state(env.ws, "intercom")["cursor"])
    assert cursor["conversations_at"] == 1790006100

    # Off again: they go, and the lock with them.
    env.client.put("/api/integrations/intercom/containers", headers=headers,
                   json={"containers": [], "options": {"conversations": False}})
    assert card(env)["access_locked"] is False
    rec.load("intercom/incremental")
    sync.sync(env.ws, "intercom", confirm_deletes=True)
    assert item(env, "conversation-77") is None and item(env, "conversation-78") is None


def test_a_429_waits_for_the_windows_reset_and_a_revoked_token_needs_a_reconnect(
        env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_token(env)
    rec.add("GET", f"{I}/help_center/collections", status=429,
            headers={"X-RateLimit-Reset": str(int(time.time()) + 7)},
            json_body={"type": "error.list", "errors": [{"code": "rate_limit_exceeded"}]})
    rec.load("intercom/full")
    sync.sync(env.ws, "intercom")
    assert any(5 <= s <= 8 for s in rec.slept)
    rec.add("GET", f"{I}/help_center/collections", status=401,
            json_body={"type": "error.list", "errors": [{"code": "unauthorized"}]})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "intercom", full=True)
    assert card(env)["error"]["code"] == "auth_expired"


def test_intercom_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["intercom"] == "live"
