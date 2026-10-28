"""
Freshdesk against recorded HTTP (tests/fixtures/connectors/freshdesk/, written
from the shapes in Freshdesk's API reference; an unrecorded request fails the
test), through the real framework (kb/services/sync.py): connecting with an API
key, the category / folder picker, listing the knowledge base (Link-header
pages), folder visibility into acl, drafts and deletes behind the mass-delete
guard, tickets (off by default, locked to HR and admins), 429s and typed errors.
"""

import base64
import json
import time

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

F = "https://acme.freshdesk.com/api/v2"
BASIC = "Basic " + base64.b64encode(b"fd-key:X").decode()
ME = {"id": 42, "available": True, "contact": {"name": "Ann Lee", "email": "ann@acme.com"}}


def connect_key(env, **settings):
    import integrations_store
    integrations_store.connect(env.ws, "freshdesk", "acme.freshdesk.com (Ann Lee)",
                               {"api_key": "fd-key"}, "admin")
    integrations_store.update(env.ws, "freshdesk", settings={"subdomain": "acme", **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "freshdesk", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "freshdesk")


# ---- connecting ------------------------------------------------------------------------
def test_an_api_key_connects_after_agents_me_says_who_it_is(env, monkeypatch):
    import db
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{F}/agents/me", json_body=ME, req_headers={"Authorization": BASIC})
    r = env.client.post("/api/integrations/freshdesk/setup", headers=env.headers(), json={
        "url": "https://acme.freshdesk.com/a/", "token": "fd-key"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "account": "acme.freshdesk.com (Ann Lee)", "choose": False}
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 'freshdesk'", (env.ws,)).fetchone()[0]
    assert b"fd-key" not in blob                         # sealed with the workspace's key
    c = card(env)
    assert (c["base_url"], c["options"], c["acl"], c["policy"]) == (
        "acme", {"tickets": False}, True, "restricted")   # restricted until an admin opens it


def test_a_bad_key_or_subdomain_is_refused_in_plain_words(env, monkeypatch):
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{F}/agents/me", status=401, json_body={"code": "invalid_credentials"})
    headers = env.headers()
    r = env.client.post("/api/integrations/freshdesk/setup", headers=headers,
                        json={"url": "acme", "token": "nope"})
    assert r.status_code == 400 and r.json()["detail"] == "Freshdesk didn't accept that API key."
    before = len(rec.calls)
    r = env.client.post("/api/integrations/freshdesk/setup", headers=headers,
                        json={"url": "https://evil.example.com/", "token": "k"})
    assert r.status_code == 400 and "subdomain" in r.json()["detail"]
    assert len(rec.calls) == before                     # refused before any call


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_is_categories_then_folders_then_subfolders(env, monkeypatch):
    Recorded(monkeypatch, "freshdesk/full")
    connect_key(env)
    headers = env.headers()
    top = env.client.get("/api/integrations/freshdesk/containers", headers=headers).json()
    assert [(c["id"], c["kind"]) for c in top["items"]] == [("category-1", "category"),
                                                           ("category-2", "category")]
    inside = env.client.get("/api/integrations/freshdesk/containers?parent=category-1",
                            headers=headers).json()["items"]
    assert [(c["id"], c["has_children"]) for c in inside] == [("folder-10", True)]
    deeper = env.client.get("/api/integrations/freshdesk/containers?parent=folder-10",
                            headers=headers).json()["items"]
    assert [c["id"] for c in deeper] == ["folder-11"]
    r = env.client.put("/api/integrations/freshdesk/containers", headers=headers,
                       json={"containers": [{"id": "../x", "name": "x"}]})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_lists_published_articles_with_their_folder_visibility(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "freshdesk/full")
    connect_key(env)
    summary = sync.sync(env.ws, "freshdesk")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 5)
    assert all(c["headers"]["Authorization"] == BASIC for c in rec.calls)
    assert set(sync_store.items(env.ws, "freshdesk")) == {
        "article-100", "article-102", "article-110", "article-200", "article-210"}  # no draft
    install = item(env, "article-100")
    assert install["url"] == "https://acme.freshdesk.com/support/solutions/articles/100"
    assert env.text("freshdesk", install["file_name"]) == (
        "Install the agent\n\n## Steps\n\nDownload it.\n\n```\ncurl -sL get.acme.com | sh\n```")
    assert install["acl"]["visibility"] == 1 and install["acl"]["via"] == "freshdesk"
    assert item(env, "article-110")["acl"]["visibility"] == 2        # a sub-folder's own
    agents = item(env, "article-200")
    assert agents["acl"]["visibility"] == 3
    assert agents["url"] == "https://acme.freshdesk.com/a/solutions/articles/200"
    assert item(env, "article-210")["acl"]["company_ids"] == [5001]


@pytest.mark.parametrize("picks, synced", [
    (["folder-10"], {"article-100", "article-102", "article-110"}),     # with its sub-folder
    (["category-2"], {"article-200", "article-210"}),
])
def test_picked_categories_and_folders_narrow_it_down(env, monkeypatch, picks, synced):
    import sync_store
    from kb.services import sync
    Recorded(monkeypatch, "freshdesk/full")
    connect_key(env, containers=[{"id": p, "name": p, "kind": p.split("-")[0]} for p in picks])
    sync.sync(env.ws, "freshdesk")
    assert set(sync_store.items(env.ws, "freshdesk")) == synced


def test_the_knowledge_base_is_listed_again_only_after_a_while_and_deletes_are_guarded(
        env, monkeypatch):
    import sync_store
    from connectors import freshdesk
    from kb.services import sync
    rec = Recorded(monkeypatch, "freshdesk/full")
    connect_key(env)
    sync.sync(env.ws, "freshdesk")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "freshdesk")             # within FRESHDESK_ARTICLES_EVERY
    assert len(rec.calls) == before and summary["mode"] == "incremental"
    # Due again: everything is listed; one article was deleted in Freshdesk.
    state = json.loads(sync_store.state(env.ws, "freshdesk")["cursor"])
    sync_store.save_state(env.ws, "freshdesk", cursor=json.dumps(
        {**state, "articles_at": time.time() - freshdesk.ARTICLES_EVERY - 1}))
    rec.load("freshdesk/full")
    rec.exchanges = [e for e in rec.exchanges
                     if not (not e["used"] and e["request"]["url"].endswith("/folders/21/articles"))]
    rec.add("GET", f"{F}/solutions/folders/21/articles", json_body=[])
    summary = sync.sync(env.ws, "freshdesk")
    assert (summary["mode"], summary["deleted"], summary["unchanged"]) == ("full", 1, 4)
    assert item(env, "article-210") is None
    # A listing that lost everything is held for an admin.
    state = json.loads(sync_store.state(env.ws, "freshdesk")["cursor"])
    sync_store.save_state(env.ws, "freshdesk", cursor=json.dumps({**state, "articles_at": 0}))
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.add("GET", f"{F}/solutions/categories", json_body=[])
    summary = sync.sync(env.ws, "freshdesk")
    assert (summary["held_deletes"], summary["deleted"]) == (4, 0)
    assert card(env)["held_deletes"] == 4


def test_tickets_are_off_until_turned_on_and_then_hr_and_admins_only(env, monkeypatch):
    import integrations_store
    import sync_store
    from kb.services import indexing, sync
    retagged = []
    monkeypatch.setattr(indexing, "schedule_retag", lambda ws, p: retagged.append(p))
    rec = Recorded(monkeypatch, "freshdesk/full")
    connect_key(env, access="everyone")
    sync.sync(env.ws, "freshdesk")
    assert not rec.requests_to("/tickets")              # off by default
    headers = env.headers()
    r = env.client.put("/api/integrations/freshdesk/containers", headers=headers,
                       json={"containers": [], "options": {"tickets": True}})
    assert r.status_code == 200, r.text
    c = card(env)
    assert (c["options"], c["access"], c["access_locked"]) == ({"tickets": True}, "restricted",
                                                               True)
    assert retagged == ["freshdesk"]
    assert integrations_store.chunk_access_levels(env.ws)["freshdesk"] == "restricted"
    r = env.client.put("/api/integrations/freshdesk/policy", headers=headers,
                       json={"policy": "source"})
    assert r.status_code == 400 and "tickets" in r.json()["detail"]

    rec.load("freshdesk/full")
    rec.load("freshdesk/tickets")
    summary = sync.sync(env.ws, "freshdesk", full=True)
    assert rec.unused() == []
    first = [c for c in rec.calls if c["url"] == f"{F}/tickets"][0]
    assert first["params"]["updated_since"].endswith("Z")       # the backfill window
    assert (summary["upserted"], summary["unchanged"]) == (2, 5)  # resolved + closed
    assert item(env, "ticket-802") is None              # still open
    refund = item(env, "ticket-801")
    assert refund["acl"] == {"via": "freshdesk", "ticket": True}
    assert refund["url"] == "https://acme.freshdesk.com/a/tickets/801"
    text = env.text("freshdesk", refund["file_name"])
    assert "Please refund order 1234." in text and "Refunded." in text
    assert "watch list" not in text                     # private notes stay in Freshdesk
    cursor = json.loads(sync_store.state(env.ws, "freshdesk")["cursor"])
    assert cursor["tickets_at"] == "2026-10-04T10:00:00Z"

    # Next run: from the watermark; a reopened ticket goes.
    rec.add("GET", f"{F}/tickets", params={"updated_since": "2026-10-04T10:00:00Z"},
            json_body=[{"id": 801, "subject": "Refund", "status": 2,
                        "updated_at": "2026-10-05T10:00:00Z"}])
    summary = sync.sync(env.ws, "freshdesk")
    assert summary["deleted"] == 1 and item(env, "ticket-801") is None

    # Off again: the tickets go, and the lock with them.
    env.client.put("/api/integrations/freshdesk/containers", headers=headers,
                   json={"containers": [], "options": {"tickets": False}})
    assert card(env)["access_locked"] is False and card(env)["access"] == "everyone"
    sync.sync(env.ws, "freshdesk", confirm_deletes=True)
    assert item(env, "ticket-803") is None


def test_a_429_waits_for_retry_after_and_a_revoked_key_needs_a_reconnect(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_key(env)
    rec.add("GET", f"{F}/solutions/categories", status=429, headers={"Retry-After": "13"})
    rec.load("freshdesk/full")
    sync.sync(env.ws, "freshdesk")
    assert 13 in rec.slept
    rec.add("GET", f"{F}/solutions/categories", status=401, json_body={"code": "x"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "freshdesk", full=True)
    assert card(env)["error"]["code"] == "auth_expired"


def test_a_next_link_to_another_host_is_never_followed(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_key(env)
    rec.add("GET", f"{F}/solutions/categories", json_body=[],
            headers={"Link": '<https://evil.example.com/steal>; rel="next"'})
    with pytest.raises(base.ConfigError):
        sync.sync(env.ws, "freshdesk")
    assert not rec.requests_to("evil.example.com")


def test_freshdesk_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["freshdesk"] == "live"
