"""
Salesforce Knowledge against recorded HTTP (tests/fixtures/connectors/salesforce/,
written from the shapes of Salesforce's REST query and describe resources and
the Knowledge__kav object; an unrecorded request fails the test), through the
real framework (kb/services/sync.py): the web server flow with PKCE through the
customer's own External Client App (or the server's packaged one), refresh on
401 with a rotating refresh token, the record type picker, SOQL batches
(nextRecordsUrl), SystemModstamp increments, archived versions (and why a new
version mustn't delete its article), channels and data categories into acl,
REQUEST_LIMIT_EXCEEDED and typed errors, and revoking on disconnect.
"""

import base64
import hashlib
import json
import pathlib
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

S = "https://acme.my.salesforce.com"
Q = f"{S}/services/data/v62.0/query"
TOKEN = f"{S}/services/oauth2/token"
TOKENS = {"access_token": "00Dxx!at-1", "refresh_token": "5Aep-rt-1", "instance_url": S,
          "id": "https://login.salesforce.com/id/00Dxx0000001gEREAY/005xx000001Sv6AAAS",
          "token_type": "Bearer", "issued_at": "1790000000000", "signature": "x",
          "scope": "api refresh_token"}


def connect(env, **settings):
    import integrations_store
    integrations_store.connect(env.ws, "salesforce", "acme.my.salesforce.com", {
        "access_token": "00Dxx!at-1", "refresh_token": "5Aep-rt-1",
        "login": "acme.my.salesforce.com", "instance_url": S,
        "client_id": "3MVG-key", "client_secret": "eca-secret"}, "admin")
    integrations_store.update(env.ws, "salesforce", settings={
        "login": "acme.my.salesforce.com", "org": "00Dxx0000001gEREAY", **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "salesforce", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "salesforce")


DESCRIBE = json.loads((pathlib.Path(__file__).parent / "fixtures" / "connectors" / "salesforce"
                       / "full.json").read_text())["exchanges"][0]["response"]["json"]
EN, ES = "kA0000000000001AAA:en_US", "kA0000000000001AAA:es"
REFUND = "kA0000000000002AAA:en_US"


# ---- connecting ------------------------------------------------------------------------
def test_the_customers_own_app_signs_in_with_pkce_and_is_sealed(env, monkeypatch):
    import db
    import integrations_store
    rec = Recorded(monkeypatch)
    headers = env.headers()
    c = card(env)
    assert (c["status"], c["sign_in"], c["server_app"], c["auth"]) == (
        "disconnected", True, False, "token")
    r = env.client.post("/api/integrations/salesforce/connect", headers=headers,
                        json={"url": "https://acme.my.salesforce.com", "client_id": "3MVG-key",
                              "client_secret": "eca-secret"})
    assert r.status_code == 200, r.text
    parts = urllib.parse.urlsplit(r.json()["url"])
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.netloc, parts.path) == ("acme.my.salesforce.com", "/services/oauth2/authorize")
    assert (q["response_type"], q["client_id"], q["scope"], q["code_challenge_method"]) == (
        "code", "3MVG-key", "api refresh_token", "S256")
    assert q["redirect_uri"].endswith("/api/integrations/salesforce/callback")
    rec.add("POST", TOKEN, json_body=TOKENS)
    r = env.client.get("/api/integrations/salesforce/callback",
                       params={"state": q["state"], "code": "aPrx-code"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?connected=salesforce"
    sent = rec.calls[-1]["data"]
    assert (sent["grant_type"], sent["code"], sent["client_id"], sent["client_secret"]) == (
        "authorization_code", "aPrx-code", "3MVG-key", "eca-secret")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(sent["code_verifier"].encode()).digest()).decode().rstrip("=")
    assert challenge == q["code_challenge"]               # PKCE: the verifier matches
    sec = integrations_store.secret(env.ws, "salesforce")
    assert (sec["refresh_token"], sec["instance_url"], sec["client_id"]) == (
        "5Aep-rt-1", S, "3MVG-key")
    row = integrations_store.get(env.ws, "salesforce")
    assert row["account"] == "acme.my.salesforce.com"
    assert row["settings"]["org"] == "00Dxx0000001gEREAY"
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 'salesforce'", (env.ws,)).fetchone()[0]
    assert b"eca-secret" not in blob and b"5Aep-rt-1" not in blob
    assert card(env)["policy"] == "restricted"


@pytest.mark.parametrize("body, message", [
    ({"url": "https://acme.my.salesforce.com"}, "consumer key"),
    ({"url": "https://evil.example.com", "client_id": "k", "client_secret": "s"}, "My Domain"),
    ({"url": "https://acme.my.salesforce.com.evil.example", "client_id": "k",
      "client_secret": "s"}, "My Domain"),
])
def test_a_missing_app_or_an_address_that_isnt_salesforce_is_refused(env, monkeypatch, body,
                                                                      message):
    rec = Recorded(monkeypatch)
    r = env.client.post("/api/integrations/salesforce/connect", headers=env.headers(), json=body)
    assert r.status_code == 400 and message in r.json()["detail"]
    assert rec.calls == []
    r = env.client.post("/api/integrations/salesforce/setup", headers=env.headers(),
                        json={"url": "acme.my.salesforce.com"})
    assert r.status_code == 400 and "Sign in" in r.json()["detail"]


def test_the_servers_packaged_app_needs_only_the_address(env, monkeypatch):
    import integrations_store
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "3MVG-packaged")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "pkg-secret")
    rec = Recorded(monkeypatch)
    assert card(env)["server_app"] is True
    url = env.client.post("/api/integrations/salesforce/connect", headers=env.headers(),
                          json={"url": "login.salesforce.com"}).json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert q["client_id"] == "3MVG-packaged"
    rec.add("POST", "https://login.salesforce.com/services/oauth2/token", json_body=TOKENS)
    env.client.get("/api/integrations/salesforce/callback",
                   params={"state": q["state"], "code": "c"}, follow_redirects=False)
    assert rec.calls[-1]["data"]["client_secret"] == "pkg-secret"
    assert "client_id" not in integrations_store.secret(env.ws, "salesforce")


def test_an_instance_url_off_salesforce_is_never_trusted(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/salesforce/connect", headers=env.headers(), json={
        "url": "acme.my.salesforce.com", "client_id": "k", "client_secret": "s"}).json()["url"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
    rec.add("POST", TOKEN, json_body={**TOKENS, "instance_url": "https://evil.example.com"})
    r = env.client.get("/api/integrations/salesforce/callback",
                       params={"state": state, "code": "c"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?error=connect_failed"
    assert integrations_store.get(env.ws, "salesforce") is None


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_lists_the_article_record_types(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", Q, params={"q~": "FROM RecordType WHERE SobjectType = 'Knowledge__kav'"},
            json_body={"totalSize": 2, "done": True, "records": [
                {"Id": "012000000000001AAA", "Name": "FAQ"},
                {"Id": "012000000000002AAA", "Name": "How-to"}]})
    headers = env.headers()
    got = env.client.get("/api/integrations/salesforce/containers", headers=headers).json()
    assert [(c["id"], c["name"], c["kind"]) for c in got["items"]] == [
        ("012000000000001AAA", "FAQ", "record_type"), ("012000000000002AAA", "How-to",
                                                      "record_type")]
    r = env.client.put("/api/integrations/salesforce/containers", headers=headers,
                       json={"containers": [{"id": "' OR Id != '", "name": "x"}]})
    assert r.status_code == 400                          # never reaches a SOQL string


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_reads_every_online_article_in_batches(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "salesforce/full")
    connect(env)
    summary = sync.sync(env.ws, "salesforce")
    assert rec.unused() == []
    assert all(c["headers"]["Authorization"] == "Bearer 00Dxx!at-1" for c in rec.calls)
    assert (summary["mode"], summary["upserted"]) == ("full", 3)
    assert set(sync_store.items(env.ws, "salesforce")) == {EN, ES, REFUND}
    soql = rec.calls[1]["params"]["q"]
    assert "Answer__c" in soql and "Internal_Notes__c" not in soql   # rich text fields only
    assert "DataCategorySelections" in soql
    reset = item(env, EN)
    assert reset["url"] == f"{S}/lightning/r/Knowledge__kav/ka0V1/view"
    assert env.text("salesforce", reset["file_name"]) == (
        "Reset your password\n\nSummary of Reset your password.\n\n"
        "Use Forgot password on the sign-in page.")
    assert reset["acl"] == {"via": "salesforce", "data_categories": [],
                            "channels": {"app": True, "pkb": True, "csp": True}}
    assert item(env, REFUND)["acl"]["data_categories"] == ["Products:Billing"]
    assert item(env, ES)["acl"]["data_categories"] == ["Region:Spain"]


def test_picked_record_types_narrow_the_query(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env, record_types=[{"id": "012000000000001AAA", "name": "FAQ"}])
    rec.add("GET", f"{S}/services/data/v62.0/sobjects/Knowledge__kav/describe",
            json_body=DESCRIBE)
    rec.add("GET", Q, params={"q~": "AND RecordTypeId IN ('012000000000001AAA')"},
            json_body={"totalSize": 0, "done": True, "records": []})
    sync.sync(env.ws, "salesforce")
    assert rec.unused() == []


def test_increments_follow_systemmodstamp_and_a_new_version_doesnt_delete_its_article(
        env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "salesforce/full")
    connect(env)
    sync.sync(env.ws, "salesforce")
    rec.load("salesforce/incremental")
    summary = sync.sync(env.ws, "salesforce")
    assert rec.unused() == []
    # The new version of the English article archived its old one: it stays, updated.
    # The refund article was archived with no version online: it goes.
    assert (summary["mode"], summary["upserted"], summary["deleted"]) == ("incremental", 2, 1)
    assert "14 characters" in env.text("salesforce", item(env, EN)["file_name"])
    assert item(env, REFUND) is None and item(env, ES) is not None
    assert item(env, "kA0000000000004AAA:en_US")["acl"]["channels"]["pkb"] is False
    cursor = json.loads(sync_store.state(env.ws, "salesforce")["cursor"])
    assert cursor["watermark"] == "2026-10-11T09:00:00.000+0000"


def test_a_401_refreshes_once_saving_the_rotated_refresh_token(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", f"{S}/services/data/v62.0/sobjects/Knowledge__kav/describe", status=401,
            json_body=[{"errorCode": "INVALID_SESSION_ID", "message": "Session expired"}])
    rec.add("POST", TOKEN, json_body={**TOKENS, "access_token": "00Dxx!at-2",
                                      "refresh_token": "5Aep-rt-2"})
    rec.load("salesforce/full")
    sync.sync(env.ws, "salesforce")
    refresh = [c for c in rec.calls if c["url"] == TOKEN][0]["data"]
    assert (refresh["grant_type"], refresh["refresh_token"], refresh["client_id"]) == (
        "refresh_token", "5Aep-rt-1", "3MVG-key")
    sec = integrations_store.secret(env.ws, "salesforce")
    assert (sec["access_token"], sec["refresh_token"]) == ("00Dxx!at-2", "5Aep-rt-2")
    assert rec.calls[-1]["headers"]["Authorization"] == "Bearer 00Dxx!at-2"
    # The refresh token was revoked (or reused after rotation): reconnect.
    rec.add("GET", f"{S}/services/data/v62.0/sobjects/Knowledge__kav/describe", status=401,
            json_body=[{"errorCode": "INVALID_SESSION_ID"}])
    rec.add("POST", TOKEN, status=400, json_body={"error": "invalid_grant",
                                                  "error_description": "expired access/refresh token"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "salesforce", full=True)
    assert card(env)["error"]["code"] == "auth_expired"


def test_the_orgs_daily_api_limit_is_a_rate_limit_not_a_permission_error(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", f"{S}/services/data/v62.0/sobjects/Knowledge__kav/describe", status=403,
            json_body=[{"errorCode": "REQUEST_LIMIT_EXCEEDED",
                        "message": "TotalRequests Limit exceeded."}])
    with pytest.raises(base.RateLimited):
        sync.sync(env.ws, "salesforce")
    assert card(env)["error"]["code"] == "rate_limited"
    rec.add("GET", f"{S}/services/data/v62.0/sobjects/Knowledge__kav/describe", status=404,
            json_body=[{"errorCode": "NOT_FOUND"}])
    with pytest.raises(base.ConfigError):                 # Lightning Knowledge is off
        sync.sync(env.ws, "salesforce")


def test_a_next_records_url_on_another_host_is_never_followed(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", f"{S}/services/data/v62.0/sobjects/Knowledge__kav/describe",
            json_body=DESCRIBE)
    rec.add("GET", Q, json_body={"done": False, "records": [],
                                 "nextRecordsUrl": "https://evil.example.com/steal"})
    with pytest.raises(base.ConfigError):
        sync.sync(env.ws, "salesforce")
    assert not rec.requests_to("evil.example.com")


def test_disconnecting_revokes_the_refresh_token(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("POST", f"{S}/services/oauth2/revoke")
    r = env.client.delete("/api/integrations/salesforce", headers=env.headers())
    assert r.status_code == 200
    assert rec.calls[-1]["data"] == {"token": "5Aep-rt-1"}


def test_salesforce_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["salesforce"] == "live"
