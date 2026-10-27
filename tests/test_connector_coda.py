"""
Coda against recorded HTTP (tests/fixtures/connectors/coda/, written from
Coda's OpenAPI spec by make_work_tools.py; an unrecorded request fails the
test), through the real framework (kb/services/sync.py): connecting with an API
token, the doc picker (page-token paging), pages exported as Markdown (begin,
poll, download), embed pages skipped, updatedAt increments, deletes behind the
mass-delete guard, sharing into acl (failing closed, and the source policy
offered only once every doc's sharing could be read), 429s and typed errors.
"""

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

D = "https://docs.superhuman.com/apis/v1"
PICKS = [{"id": "AbC1", "name": "Team handbook", "kind": "document"}]


def connect(env, picks=PICKS, **settings):
    import integrations_store
    integrations_store.connect(env.ws, "coda", "ann@acme.example", {"token": "coda-token"},
                               "admin")
    integrations_store.update(env.ws, "coda", settings={"account": "ann@acme.example",
                                                        "containers": list(picks), **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "coda", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "coda")


def test_a_token_connects_after_whoami_and_is_sealed(env, monkeypatch):
    import db
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{D}/whoami", json_body={"name": "Ann", "loginId": "Ann@acme.example",
                                             "type": "user", "scoped": False},
            req_headers={"Authorization": "Bearer coda-token"})
    r = env.client.post("/api/integrations/coda/setup", headers=env.headers(),
                        json={"token": "coda-token"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "account": "ann@acme.example", "choose": True}
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 'coda'", (env.ws,)).fetchone()[0]
    assert b"coda-token" not in blob
    c = card(env)
    assert (c["policy"], c["policies"]) == ("restricted", ["workspace", "restricted"])
    rec.add("GET", f"{D}/whoami", status=401, json_body={"statusCode": 401})
    r = env.client.post("/api/integrations/coda/setup", headers=env.headers(),
                        json={"token": "bad"})
    assert (r.status_code, r.json()["detail"]) == (400, "Coda didn't accept that token.")


def test_the_picker_lists_every_page_of_docs(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect(env, picks=[])
    rec.add("GET", f"{D}/docs", json_body={"items": [{"id": "Zz9", "name": "Zeta"}],
                                           "nextPageToken": "t2"})
    rec.add("GET", f"{D}/docs", params={"pageToken": "t2"},
            json_body={"items": [{"id": "AbC1", "name": "Team handbook",
                                  "browserLink": "https://coda.io/d/_dAbC1"}]})
    r = env.client.get("/api/integrations/coda/containers", headers=env.headers())
    assert [i["id"] for i in r.json()["items"]] == ["AbC1", "Zz9"]
    r = env.client.put("/api/integrations/coda/containers", headers=env.headers(),
                       json={"containers": [{"id": "../x", "name": "x"}]})
    assert r.status_code == 400


def test_a_full_sync_exports_pages_and_reads_the_sharing(env, monkeypatch):
    from kb.services import permissions, sync
    import integrations_store
    rec = Recorded(monkeypatch, "coda/full")
    connect(env)
    summary = sync.sync(env.ws, "coda")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"], summary["skipped"]) == ("full", 2, 1)
    leave = item(env, "AbC1:canvas-1")
    assert leave["title"] == "Team handbook: Leave"
    assert "Everyone gets **25 days**." in env.text("coda", leave["file_name"])
    assert leave["acl"] == {"via": "coda", "doc": "AbC1", "anyone": False,
                            "users": ["ann@acme.example", "owner@acme.example"],
                            "domains": ["acme.example"], "groups": ["group:grp-1"]}
    assert item(env, "AbC1:canvas-3")["skipped_reason"] == "embed"
    assert 1.0 in rec.slept                                # waited once for the export
    # The download came from Coda's own host: with the token.
    blob = rec.requests_to("/blobs/")[0]
    assert blob["headers"]["Authorization"] == "Bearer coda-token"
    # Every doc's sharing was read: "as shared in Coda" is offered.
    row = integrations_store.get(env.ws, "coda")
    assert row["settings"]["sharing"] == "readable"
    assert "source" in permissions.policies("coda", row)


def test_then_only_changed_docs_and_unreadable_sharing_fails_closed(env, monkeypatch):
    import access
    import integrations_store
    from kb.services import permissions, sync
    rec = Recorded(monkeypatch, "coda/full")
    connect(env)
    sync.sync(env.ws, "coda")
    rec.load("coda/incremental")
    summary = sync.sync(env.ws, "coda")
    assert rec.unused() == []
    assert (summary["mode"], summary["deleted"]) == ("incremental", 1)    # Travel
    leave = item(env, "AbC1:canvas-1")
    assert "**30 days**" in env.text("coda", leave["file_name"])
    assert leave["acl"] == {"via": "coda", "doc": "AbC1", "unavailable": True}
    assert permissions.Resolver(env.ws).resolve(leave["acl"]) == ([access.NOBODY], 1)
    row = integrations_store.get(env.ws, "coda")
    assert row["settings"]["sharing"] == "unreadable"
    assert permissions.policies("coda", row) == ("workspace", "restricted")
    r = env.client.put("/api/integrations/coda/policy", headers=env.headers(),
                       json={"policy": "source"})
    assert r.status_code == 400


def test_a_doc_whose_updated_at_did_not_move_is_left_alone(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "coda/full")
    connect(env)
    sync.sync(env.ws, "coda")
    before = len(rec.calls)
    rec.add("GET", f"{D}/docs/AbC1", json_body={"id": "AbC1", "name": "Team handbook",
                                                "updatedAt": "2026-10-10T10:00:00.000Z"})
    rec.add("GET", f"{D}/docs/AbC1/acl/permissions", json_body={"items": []})
    summary = sync.sync(env.ws, "coda")
    assert summary["mode"] == "incremental" and summary["deleted"] == 0
    assert [c["url"] for c in rec.calls[before:]] == [f"{D}/docs/AbC1",
                                                      f"{D}/docs/AbC1/acl/permissions"]


def test_a_full_listing_that_lost_everything_is_held_and_a_gone_doc_too(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "coda/full")
    connect(env)
    sync.sync(env.ws, "coda")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.add("GET", f"{D}/docs/AbC1", status=404, json_body={"statusCode": 404})
    summary = sync.sync(env.ws, "coda", full=True)
    assert (summary["held_deletes"], summary["deleted"]) == (2, 0)
    assert card(env)["held_deletes"] == 2


def test_a_failed_export_and_429s(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", f"{D}/docs/AbC1", status=429, headers={"Retry-After": "9"})
    rec.load("coda/full")
    sync.sync(env.ws, "coda")
    assert 9 in rec.slept
    rec.add("GET", f"{D}/docs/AbC1", status=403, json_body={"statusCode": 403})
    with pytest.raises(base.Forbidden):
        sync.sync(env.ws, "coda", full=True)


def test_coda_is_live_and_says_why_the_source_policy_may_be_missing(env):
    c = card(env)
    assert (c["availability"], c["auth"], c["picker"], c["acl"]) == ("live", "token", "list",
                                                                      True)
    assert "sharing" in c["source_policy_note"]
