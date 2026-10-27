"""
HubSpot against recorded HTTP (tests/fixtures/connectors/hubspot/, written from
the shapes in HubSpot's knowledge base API reference (beta); an unrecorded
request fails the test), through the real framework (kb/services/sync.py):
connecting with a private app token or OAuth (30-minute access tokens and the
refresh token), the knowledge base / category picker, the full listing and the
updatedAfter increments, accessType into acl, deletes behind the mass-delete
guard, 429s and typed errors.
"""

import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

H = "https://api.hubapi.com/cms/knowledge-base/2027-03-beta"
TOKEN = "https://api.hubspot.com/oauth/v3/token"
KBS = {"results": [{"id": "1", "name": "Acme Help", "domain": "help.acme.com",
                    "language": "en"}]}


def connect_token(env, **settings):
    import integrations_store
    integrations_store.connect(env.ws, "hubspot", "HubSpot (help.acme.com)",
                               {"token": "pat-na1-test"}, "admin")
    integrations_store.update(env.ws, "hubspot", settings={"account": "help.acme.com",
                                                           **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "hubspot", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "hubspot")


# ---- connecting ------------------------------------------------------------------------
def test_a_private_app_token_connects_after_listing_the_knowledge_bases(env, monkeypatch):
    import db
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{H}/knowledge-bases", json_body=KBS,
            req_headers={"Authorization": "Bearer pat-na1-test"})
    r = env.client.post("/api/integrations/hubspot/setup", headers=env.headers(),
                        json={"token": "pat-na1-test"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "account": "HubSpot (help.acme.com)", "choose": False}
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 'hubspot'", (env.ws,)).fetchone()[0]
    assert b"pat-na1-test" not in blob
    c = card(env)
    assert (c["sign_in"], c["acl"], c["policy"]) == (False, True, "restricted")


@pytest.mark.parametrize("status, message", [
    (401, "HubSpot didn't accept that token."),
    (403, "That token can't read the knowledge base: give the private app the "
          "cms.knowledge_base.articles.read scope."),
])
def test_a_bad_token_or_a_missing_scope_is_refused_in_plain_words(env, monkeypatch, status,
                                                                   message):
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{H}/knowledge-bases", status=status,
            json_body={"category": "MISSING_SCOPES", "message": "x", "correlationId": "c"})
    r = env.client.post("/api/integrations/hubspot/setup", headers=env.headers(),
                        json={"token": "bad"})
    assert r.status_code == 400 and r.json()["detail"] == message


def test_oauth_signs_in_and_refreshes_the_half_hour_token(env, monkeypatch):
    import integrations_store
    from connectors import hubspot
    rec = Recorded(monkeypatch)
    headers = env.headers()
    assert env.client.post("/api/integrations/hubspot/connect",
                           headers=headers).status_code == 400   # no server app: a form
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "hs-client")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "hs-secret")
    assert card(env)["sign_in"] is True
    url = env.client.post("/api/integrations/hubspot/connect", headers=headers).json()["url"]
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.netloc, parts.path) == ("app.hubspot.com", "/oauth/authorize")
    assert (q["client_id"], q["scope"]) == ("hs-client", "cms.knowledge_base.articles.read")
    assert q["redirect_uri"].endswith("/api/integrations/hubspot/callback")
    rec.add("POST", TOKEN, json_body={"access_token": "hat-1", "refresh_token": "hrt-1",
                                      "expires_in": 1800, "token_type": "bearer",
                                      "hub_id": 4455, "scopes": [q["scope"]]})
    r = env.client.get("/api/integrations/hubspot/callback",
                       params={"state": q["state"], "code": "abc"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?connected=hubspot"
    sent = rec.calls[-1]["data"]
    assert (sent["grant_type"], sent["code"], sent["client_secret"]) == (
        "authorization_code", "abc", "hs-secret")
    sec = integrations_store.secret(env.ws, "hubspot")
    assert (sec["access_token"], sec["refresh_token"]) == ("hat-1", "hrt-1")
    assert integrations_store.get(env.ws, "hubspot")["account"] == "HubSpot (4455)"
    # At 80 % of its life the access token is refreshed (the refresh token kept).
    integrations_store.update(env.ws, "hubspot", secret={**sec, "renew_at": time.time() - 1})
    rec.add("POST", TOKEN, json_body={"access_token": "hat-2", "expires_in": 1800})
    assert hubspot.CONNECTOR._access_token(env.ws) == "hat-2"
    assert rec.calls[-1]["data"]["refresh_token"] == "hrt-1"
    assert integrations_store.secret(env.ws, "hubspot")["refresh_token"] == "hrt-1"
    # A refused access token is refreshed once; a refused refresh token: reconnect.
    rec.add("GET", f"{H}/categories", status=401, json_body={"category": "EXPIRED_AUTHENTICATION"})
    rec.add("POST", TOKEN, status=400, json_body={"status": "BAD_REFRESH_TOKEN"})
    with pytest.raises(base.AuthExpired):
        list(hubspot.CONNECTOR._pages(env.ws, "/categories"))


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_is_knowledge_bases_then_categories(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect_token(env)
    rec.add("GET", f"{H}/knowledge-bases", json_body=KBS)
    rec.add("GET", f"{H}/categories", params={"knowledgeBaseIds": "1"}, json_body={"results": [
        {"id": "20", "name": "Internal", "parentCategoryId": None},
        {"id": "10", "name": "Billing", "parentCategoryId": None},
        {"id": "11", "name": "Invoices", "parentCategoryId": "10"}]})
    rec.load("hubspot/full")                     # every category, for has_children
    headers = env.headers()
    top = env.client.get("/api/integrations/hubspot/containers", headers=headers).json()["items"]
    assert [(c["id"], c["name"]) for c in top] == [("kb-1", "Acme Help")]
    inside = env.client.get("/api/integrations/hubspot/containers?parent=kb-1",
                            headers=headers).json()["items"]
    assert [(c["id"], c["has_children"]) for c in inside] == [("category-10", True),
                                                              ("category-20", False)]
    r = env.client.put("/api/integrations/hubspot/containers", headers=headers,
                       json={"containers": [{"id": "folder-1", "name": "x"}]})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_lists_published_articles_with_their_access(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "hubspot/full")
    connect_token(env)
    summary = sync.sync(env.ws, "hubspot")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 4)
    assert set(sync_store.items(env.ws, "hubspot")) == {
        "article-501", "article-502", "article-503", "article-505"}   # not the archived one
    invoice = item(env, "article-501")
    assert invoice["url"] == "https://help.acme.com/kb/501"
    assert env.text("hubspot", invoice["file_name"]) == (
        "Download an invoice\n\n## Steps\n\nOpen Billing, then Invoices.")
    assert invoice["acl"] == {"via": "hubspot", "access_type": "PUBLIC", "access_group_ids": []}
    assert item(env, "article-502")["acl"]["access_type"] == "SSO_LOGIN"
    assert item(env, "article-503")["acl"]["access_group_ids"] == ["7788"]


def test_picked_categories_take_their_subcategories(env, monkeypatch):
    import sync_store
    from kb.services import sync
    Recorded(monkeypatch, "hubspot/full")
    connect_token(env, containers=[{"id": "category-10", "name": "Billing", "kind": "section"}])
    sync.sync(env.ws, "hubspot")
    assert set(sync_store.items(env.ws, "hubspot")) == {"article-501", "article-502"}


def test_then_increments_ask_for_articles_updated_after_the_watermark(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "hubspot/full")
    connect_token(env)
    sync.sync(env.ws, "hubspot")
    rec.load("hubspot/incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "hubspot")
    assert rec.unused() == []
    [listing] = [c for c in rec.calls[before:] if c["url"] == f"{H}/articles"]
    assert listing["params"]["updatedAfter"] == "2026-10-04T08:58:00Z"   # less the overlap
    assert "state" not in listing["params"]                 # so unpublished ones are seen
    assert (summary["mode"], summary["upserted"], summary["unchanged"], summary["deleted"]) == (
        "incremental", 1, 1, 1)
    assert "seven years" in env.text("hubspot", item(env, "article-501")["file_name"])
    assert item(env, "article-502") is None                 # back to a draft
    cursor = json.loads(sync_store.state(env.ws, "hubspot")["cursor"])
    assert cursor["watermark"] == "2026-10-11T09:00:00Z"


def test_a_full_listing_that_lost_everything_is_held_for_an_admin(env, monkeypatch):
    import sync_store
    from connectors import hubspot
    from kb.services import sync
    rec = Recorded(monkeypatch, "hubspot/full")
    connect_token(env)
    sync.sync(env.ws, "hubspot")
    state = json.loads(sync_store.state(env.ws, "hubspot")["cursor"])
    sync_store.save_state(env.ws, "hubspot", cursor=json.dumps(
        {**state, "full_at": time.time() - hubspot.RECONCILE - 1}))
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.add("GET", f"{H}/articles", params={"state": "PUBLISHED"}, json_body={"results": []})
    summary = sync.sync(env.ws, "hubspot")
    assert (summary["held_deletes"], summary["deleted"]) == (4, 0)
    rec.add("GET", f"{H}/articles", params={"state": "PUBLISHED"}, json_body={"results": []})
    assert sync.sync(env.ws, "hubspot", confirm_deletes=True)["deleted"] == 4


def test_a_429_is_waited_out_and_a_revoked_token_needs_a_reconnect(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_token(env)
    rec.add("GET", f"{H}/categories", status=429, headers={"Retry-After": "9"},
            json_body={"category": "RATE_LIMITS"})
    rec.load("hubspot/full")
    sync.sync(env.ws, "hubspot")
    assert 9 in rec.slept
    rec.add("GET", f"{H}/articles", params={"state": "PUBLISHED"}, status=401,
            json_body={"category": "INVALID_AUTHENTICATION"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "hubspot", full=True)
    assert card(env)["error"]["code"] == "auth_expired"


def test_hubspot_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["hubspot"] == "live"
