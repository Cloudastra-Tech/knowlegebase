"""
Zendesk against recorded HTTP (tests/fixtures/connectors/zendesk/, written from
the shapes in Zendesk's Help Center and Support API references; an unrecorded
request fails the test), through the real framework (kb/services/sync.py):
connecting with an API token or OAuth (with its rotating refresh token), the
category / section picker, the full listing and the incremental article
export, user segments into acl, drafts and deletes behind the mass-delete
guard, tickets (off by default, locked to HR and admins), 429s and typed errors.
"""

import base64
import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

Z = "https://acme.zendesk.com"
HC = f"{Z}/api/v2/help_center"
ME = {"user": {"id": 7002, "name": "Ann Lee", "email": "ann@acme.com", "role": "admin"}}


def connect_token(env, **settings):
    import integrations_store
    integrations_store.connect(env.ws, "zendesk", "acme.zendesk.com (Ann Lee)",
                               {"email": "ann@acme.com", "token": "test-token"}, "admin")
    integrations_store.update(env.ws, "zendesk", settings={"subdomain": "acme", **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "zendesk", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "zendesk")


BASIC = "Basic " + base64.b64encode(b"ann@acme.com/token:test-token").decode()


# ---- connecting ------------------------------------------------------------------------
def test_an_api_token_connects_after_users_me_says_who_it_is(env, monkeypatch):
    import db
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{Z}/api/v2/users/me", json_body=ME, req_headers={"Authorization": BASIC})
    r = env.client.post("/api/integrations/zendesk/setup", headers=env.headers(), json={
        "url": "https://acme.zendesk.com/hc/en-us", "email": "ann@acme.com",
        "token": "test-token"})
    assert r.status_code == 200, r.text
    # Nothing has to be picked: the whole Help Center syncs until narrowed down.
    assert r.json() == {"ok": True, "account": "acme.zendesk.com (Ann Lee)", "choose": False}
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 'zendesk'", (env.ws,)).fetchone()[0]
    assert b"test-token" not in blob
    c = card(env)
    assert (c["base_url"], c["options"], c["sign_in"]) == ("acme", {"tickets": False}, False)


@pytest.mark.parametrize("answer, status, message", [
    ({"error": "Couldn't authenticate you"}, 401, "Zendesk didn't accept that email and token."),
    ({"user": {"id": 9, "name": "Eve", "role": "end-user"}}, 200,
     "That account isn't a Zendesk agent or admin."),
])
def test_a_bad_token_or_an_end_user_is_refused_in_plain_words(env, monkeypatch, answer, status,
                                                              message):
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{Z}/api/v2/users/me", status=status, json_body=answer)
    r = env.client.post("/api/integrations/zendesk/setup", headers=env.headers(), json={
        "url": "acme", "email": "eve@acme.com", "token": "t"})
    assert r.status_code == 400 and r.json()["detail"] == message


def test_a_subdomain_that_isnt_one_is_refused_before_any_call(env, monkeypatch):
    rec = Recorded(monkeypatch)
    r = env.client.post("/api/integrations/zendesk/setup", headers=env.headers(), json={
        "url": "https://evil.example.com/", "email": "a@b.c", "token": "t"})
    assert r.status_code == 400 and "subdomain" in r.json()["detail"]
    assert rec.calls == []


def test_oauth_signs_in_on_the_customers_subdomain_and_rotates_the_refresh_token(env,
                                                                                 monkeypatch):
    import integrations_store
    from connectors import zendesk
    rec = Recorded(monkeypatch)
    headers = env.headers()
    # Without the server's OAuth client, Zendesk is a form only.
    r = env.client.post("/api/integrations/zendesk/connect", headers=headers, json={"url": "acme"})
    assert r.status_code == 400
    monkeypatch.setenv("ZENDESK_CLIENT_ID", "zdg-knowledge")
    monkeypatch.setenv("ZENDESK_CLIENT_SECRET", "zsecret")
    assert card(env)["sign_in"] is True
    url = env.client.post("/api/integrations/zendesk/connect", headers=headers,
                          json={"url": "acme.zendesk.com"}).json()["url"]
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.netloc, parts.path) == ("acme.zendesk.com", "/oauth/authorizations/new")
    assert (q["response_type"], q["client_id"], q["scope"]) == (
        "code", "zdg-knowledge", "hc:read tickets:read users:read")
    assert q["redirect_uri"].endswith("/api/integrations/zendesk/callback")
    rec.add("POST", f"{Z}/oauth/tokens", match_json={
        "grant_type": "authorization_code", "code": "abc", "client_id": "zdg-knowledge",
        "client_secret": "zsecret", "redirect_uri": q["redirect_uri"]},
        json_body={"access_token": "zat-1", "refresh_token": "zrt-1", "token_type": "bearer",
                   "expires_in": 1800, "refresh_token_expires_in": 2592000, "scope": q["scope"]})
    rec.add("GET", f"{Z}/api/v2/users/me", json_body=ME,
            req_headers={"Authorization": "Bearer zat-1"})
    r = env.client.get("/api/integrations/zendesk/callback",
                       params={"state": q["state"], "code": "abc"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?connected=zendesk"
    sec = integrations_store.secret(env.ws, "zendesk")
    assert (sec["access_token"], sec["refresh_token"]) == ("zat-1", "zrt-1")
    # Expired: refreshing returns new tokens and invalidates the old ones (both saved).
    integrations_store.update(env.ws, "zendesk", secret={**sec, "expires_at": time.time() - 1})
    rec.add("POST", f"{Z}/oauth/tokens", match_json={"grant_type": "refresh_token",
                                                     "refresh_token": "zrt-1"},
            json_body={"access_token": "zat-2", "refresh_token": "zrt-2", "expires_in": 1800})
    assert zendesk.CONNECTOR._auth(env.ws) == "Bearer zat-2"
    assert integrations_store.secret(env.ws, "zendesk")["refresh_token"] == "zrt-2"
    # A revoked refresh token: reconnect.
    integrations_store.update(env.ws, "zendesk", secret={
        **integrations_store.secret(env.ws, "zendesk"), "expires_at": time.time() - 1})
    rec.add("POST", f"{Z}/oauth/tokens", status=400, json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        zendesk.CONNECTOR._auth(env.ws)


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_is_categories_then_sections_then_subsections(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect_token(env)
    rec.add("GET", f"{HC}/categories", json_body={"categories": [
        {"id": 300, "name": "Internal"}, {"id": 200, "name": "Customers"}],
        "meta": {"has_more": False}, "links": {"next": None}})
    rec.load("zendesk/full")
    headers = env.headers()
    top = env.client.get("/api/integrations/zendesk/containers", headers=headers).json()["items"]
    assert [(c["id"], c["kind"]) for c in top] == [("category-200", "category"),
                                                   ("category-300", "category")]
    inside = env.client.get("/api/integrations/zendesk/containers?parent=category-200",
                            headers=headers).json()["items"]
    assert [(c["id"], c["has_children"]) for c in inside] == [("section-360002", True),
                                                              ("section-360001", False)]
    r = env.client.put("/api/integrations/zendesk/containers", headers=headers,
                       json={"containers": [{"id": "../x", "name": "x"}]})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_lists_published_articles_with_their_segments(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "zendesk/full")
    connect_token(env)
    summary = sync.sync(env.ws, "zendesk")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 4)
    assert all(c["headers"]["Authorization"] == BASIC for c in rec.calls)
    assert set(sync_store.items(env.ws, "zendesk")) == {
        "article-5001", "article-5002", "article-5003", "article-5004"}   # the draft isn't
    install = item(env, "article-5001")
    assert install["url"] == f"{Z}/hc/en-us/articles/5001"
    assert env.text("zendesk", install["file_name"]) == (
        "Install the agent\n\n## Steps\n\nDownload it.\n\n```\ncurl -sL get.acme.com | sh\n"
        "acme login\n```\n\nOS | Supported\nLinux | yes")
    # Segments captured: everyone; every signed-in user; narrower ones -> staff.
    assert install["acl"] == {"mode": "workspace", "everyone": True, "segments": []}
    assert item(env, "article-5002")["acl"]["mode"] == "workspace"
    vip = item(env, "article-5003")["acl"]
    assert vip["mode"] == "staff" and vip["segments"][0]["organization_ids"] == [9001]
    assert item(env, "article-5004")["acl"]["segments"][0]["user_type"] == "staff"


@pytest.mark.parametrize("picks, synced", [
    (["category-200"], {"article-5001", "article-5002", "article-5003"}),
    (["section-360002"], {"article-5002", "article-5003"}),      # with its subsection
])
def test_picked_categories_and_sections_narrow_it_down(env, monkeypatch, picks, synced):
    import sync_store
    from kb.services import sync
    Recorded(monkeypatch, "zendesk/full")
    connect_token(env, containers=[{"id": p, "name": p, "kind": p.split("-")[0]} for p in picks])
    sync.sync(env.ws, "zendesk")
    assert set(sync_store.items(env.ws, "zendesk")) == synced


def test_then_the_incremental_export_starts_where_the_last_run_ended(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "zendesk/full")
    connect_token(env)
    sync.sync(env.ws, "zendesk")
    start = json.loads(sync_store.state(env.ws, "zendesk")["cursor"])["articles_at"]
    rec.load("zendesk/incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "zendesk")
    assert rec.unused() == []
    [export] = [c for c in rec.calls[before:] if "/incremental/" in c["url"]]
    assert export["params"] == {"start_time": str(start)}
    assert not [c for c in rec.calls[before:] if c["url"] == f"{HC}/articles"]
    assert (summary["mode"], summary["upserted"], summary["unchanged"], summary["deleted"]) == (
        "incremental", 1, 1, 1)
    assert "60 days" in env.text("zendesk", item(env, "article-5002")["file_name"])
    assert item(env, "article-5003") is None            # unpublished (now a draft)
    cursor = json.loads(sync_store.state(env.ws, "zendesk")["cursor"])
    assert cursor["articles_at"] == 1792000000          # the export's end_time


def test_a_full_listing_that_lost_everything_is_held_for_an_admin(env, monkeypatch):
    import sync_store
    from connectors import zendesk
    from kb.services import sync
    rec = Recorded(monkeypatch, "zendesk/full")
    connect_token(env)
    sync.sync(env.ws, "zendesk")
    state = json.loads(sync_store.state(env.ws, "zendesk")["cursor"])
    sync_store.save_state(env.ws, "zendesk", cursor=json.dumps(
        {**state, "full_at": time.time() - zendesk.RECONCILE - 1}))
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")

    def empty(rec):
        for key in ("sections", "user_segments", "articles"):
            rec.add("GET", f"{HC}/{key}", json_body={key: [], "meta": {"has_more": False}})

    empty(rec)
    summary = sync.sync(env.ws, "zendesk")
    assert (summary["held_deletes"], summary["deleted"]) == (4, 0)
    assert card(env)["held_deletes"] == 4
    empty(rec)
    assert sync.sync(env.ws, "zendesk", confirm_deletes=True)["deleted"] == 4
    assert env.files("zendesk") == []


def test_tickets_are_off_until_turned_on_and_then_hr_and_admins_only(env, monkeypatch):
    import integrations_store
    import sync_store
    from kb.services import indexing, sync
    retagged = []
    monkeypatch.setattr(indexing, "schedule_retag", lambda ws, p: retagged.append(p))
    rec = Recorded(monkeypatch, "zendesk/full")
    connect_token(env, access="everyone")
    sync.sync(env.ws, "zendesk")
    assert not rec.requests_to("/tickets")              # off by default
    headers = env.headers()
    r = env.client.put("/api/integrations/zendesk/containers", headers=headers,
                       json={"containers": [], "options": {"tickets": True}})
    assert r.status_code == 200, r.text
    c = card(env)
    assert (c["options"], c["access"], c["access_locked"]) == ({"tickets": True}, "restricted",
                                                               True)
    assert retagged == ["zendesk"]                       # its chunks become HR and admins only
    assert integrations_store.chunk_access_levels(env.ws)["zendesk"] == "restricted"
    r = env.client.put("/api/integrations/zendesk/access", headers=headers,
                       json={"access": "everyone"})
    assert r.status_code == 400 and "tickets" in r.json()["detail"]

    rec.load("zendesk/full")
    rec.load("zendesk/tickets")
    summary = sync.sync(env.ws, "zendesk", full=True)
    assert rec.unused() == []
    [first, second] = [c for c in rec.calls if "/incremental/tickets" in c["url"]]
    assert "start_time" in first["params"] and second["params"] == {"cursor": "tc-1"}
    assert (summary["upserted"], summary["unchanged"]) == (2, 4)   # 2 solved / closed tickets
    assert item(env, "ticket-802") is None               # still open
    refund = item(env, "ticket-801")
    assert refund["acl"] == {"mode": "staff", "reason": "ticket"}
    assert refund["url"] == f"{Z}/agent/tickets/801"
    text = env.text("zendesk", refund["file_name"])
    assert "Please refund order 1234." in text and "Refunded." in text
    assert "watch list" not in text                      # internal notes stay in Zendesk
    assert json.loads(sync_store.state(env.ws, "zendesk")["cursor"])["ticket_cursor"] == "tc-2"
    # Incremental exports are limited to 10 a minute: their calls are spaced out.
    exports = [i for i, c in enumerate(rec.calls) if "/incremental/" in c["url"]]
    assert len(exports) == 2 and sum(rec.slept) >= 5

    # Off again: the tickets go, and the lock with them.
    r = env.client.put("/api/integrations/zendesk/containers", headers=headers,
                       json={"containers": [], "options": {"tickets": False}})
    assert card(env)["access_locked"] is False and card(env)["access"] == "everyone"
    rec.load("zendesk/incremental")
    sync.sync(env.ws, "zendesk", confirm_deletes=True)
    assert item(env, "ticket-801") is None and item(env, "ticket-803") is None


def test_a_429_waits_for_retry_after_and_a_revoked_token_needs_a_reconnect(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_token(env)
    rec.add("GET", f"{HC}/sections", status=429, headers={"Retry-After": "11"})
    rec.load("zendesk/full")
    sync.sync(env.ws, "zendesk")
    assert 11 in rec.slept
    rec.add("GET", f"{HC}/sections", status=401, json_body={"error": "Couldn't authenticate you"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "zendesk", full=True)
    assert card(env)["error"]["code"] == "auth_expired"


def test_a_next_link_to_another_host_is_never_followed(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_token(env)
    rec.add("GET", f"{HC}/sections", json_body={
        "sections": [], "meta": {"has_more": True},
        "links": {"next": "https://evil.example.com/steal"}})
    with pytest.raises(base.ConfigError):
        sync.sync(env.ws, "zendesk")
    assert not rec.requests_to("evil.example.com")


def test_zendesk_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["zendesk"] == "live"
