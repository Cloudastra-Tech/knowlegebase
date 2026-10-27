"""
SharePoint / OneDrive (Microsoft Graph, app-only with admin consent) against
recorded HTTP (tests/fixtures/connectors/sharepoint/, written from the shapes
in the Graph reference; an unrecorded request fails the test), through the real
framework (kb/services/sync.py): the two-step connect (sign-in, then the
tenant's admin consent), the site / library picker, a full delta listing, the
deltaLink cursor, deletes, 410 resync, permission capture, throttling and
typed errors (revoked consent, missing permission).
"""

import base64
import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded, make_docx
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

G = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
SITE = "acme.sharepoint.com,site-guid,web-guid"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(autouse=True)
def app(monkeypatch):
    monkeypatch.setenv("SHAREPOINT_CLIENT_ID", "app-id")
    monkeypatch.setenv("SHAREPOINT_CLIENT_SECRET", "app-secret")


def id_token(**claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.sig"


def connect(env, picks=({"id": SITE, "name": "HR", "kind": "site"},), expires_at=None):
    import integrations_store
    integrations_store.connect(env.ws, "sharepoint", "acme.com (admin@acme.com)", {
        "tenant": "tid-1", "access_token": "app-token",
        "expires_at": expires_at or time.time() + 3600}, "admin")
    integrations_store.update(env.ws, "sharepoint", settings={"tenant": "tid-1",
                                                              "containers": list(picks)})


def full(rec):
    rec.load("sharepoint/full_sync")
    rec.add("GET", f"{G}/drives/drv-1/items/f-docx/content",
            body=make_docx(["Twenty days a year."], heading="Leave"))


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "sharepoint", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "sharepoint")


def query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


# ---- connecting ------------------------------------------------------------------------
def start(env):
    url = env.client.post("/api/integrations/sharepoint/connect",
                          headers=env.headers()).json()["url"]
    assert url.startswith(f"{LOGIN}/organizations/oauth2/v2.0/authorize?")
    q = query(url)
    assert (q["scope"], q["response_type"]) == ("openid profile email", "code")
    return q["state"]


def sign_in(rec, tenant="tid-1"):
    rec.add("POST", f"{LOGIN}/organizations/oauth2/v2.0/token", json_body={
        "token_type": "Bearer", "access_token": "user-token", "expires_in": 3599,
        "id_token": id_token(tid=tenant, preferred_username="admin@acme.com")})


def no_consent(rec, tenant="tid-1", code="AADSTS65001"):
    rec.add("POST", f"{LOGIN}/{tenant}/oauth2/v2.0/token", status=400, json_body={
        "error": "invalid_grant" if code == "AADSTS65001" else "unauthorized_client",
        "error_description": f"{code}: The user or administrator hasn't consented.",
        "error_codes": [int(code[6:])]})


def app_token(rec, tenant="tid-1"):
    rec.add("POST", f"{LOGIN}/{tenant}/oauth2/v2.0/token",
            json_body={"token_type": "Bearer", "expires_in": 3599, "access_token": "app-token"})


def test_connect_signs_the_admin_in_then_asks_their_tenant_for_consent(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    state = start(env)
    sign_in(rec)
    no_consent(rec)
    r = env.client.get("/api/integrations/sharepoint/callback",
                       params={"state": state, "code": "c1"}, follow_redirects=False)
    consent = r.headers["location"]
    # Step 2: the admin consent screen of the tenant they signed in to.
    assert consent.startswith(f"{LOGIN}/tid-1/v2.0/adminconsent?")
    q = query(consent)
    assert q["scope"] == "https://graph.microsoft.com/.default" and q["state"] != state
    assert not integrations_store.get(env.ws, "sharepoint")
    app_token(rec)
    rec.add("GET", f"{G}/sites/getAllSites", params={"$top": "1"},
            json_body={"value": [{"id": SITE, "name": "HR"}]})
    r = env.client.get("/api/integrations/sharepoint/callback", follow_redirects=False,
                       params={"state": q["state"], "admin_consent": "True", "tenant": "tid-1"})
    assert r.headers["location"] == "/#integrations?connected=sharepoint&choose=1"
    row = integrations_store.get(env.ws, "sharepoint")
    assert row["account"] == "acme.com (admin@acme.com)"
    assert integrations_store.secret(env.ws, "sharepoint")["tenant"] == "tid-1"
    token_call = rec.requests_to("tid-1/oauth2/v2.0/token")[-1]
    assert token_call["data"]["grant_type"] == "client_credentials"
    assert token_call["data"]["scope"] == "https://graph.microsoft.com/.default"
    # The state is single-use: the same redirect again connects nothing.
    r = env.client.get("/api/integrations/sharepoint/callback", follow_redirects=False,
                       params={"state": q["state"], "admin_consent": "True", "tenant": "tid-1"})
    assert r.headers["location"] == "/#integrations?error=expired"


def test_a_tenant_that_already_consented_connects_in_one_step(env, monkeypatch):
    rec = Recorded(monkeypatch)
    state = start(env)
    sign_in(rec)
    app_token(rec)
    rec.add("GET", f"{G}/sites/getAllSites", json_body={"value": []})
    r = env.client.get("/api/integrations/sharepoint/callback",
                       params={"state": state, "code": "c1"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?connected=sharepoint&choose=1"


def test_consent_for_another_tenant_than_the_sign_in_is_refused(env, monkeypatch):
    """The tenant in Microsoft's redirect can be forged: only the signed-in one counts."""
    import integrations_store
    rec = Recorded(monkeypatch)
    state = start(env)
    sign_in(rec)
    no_consent(rec)
    consent = env.client.get("/api/integrations/sharepoint/callback", follow_redirects=False,
                             params={"state": state, "code": "c1"}).headers["location"]
    r = env.client.get("/api/integrations/sharepoint/callback", follow_redirects=False, params={
        "state": query(consent)["state"], "admin_consent": "True", "tenant": "victim-tenant"})
    assert r.headers["location"] == "/#integrations?error=wrong_account"
    assert not rec.requests_to("victim-tenant")
    assert not integrations_store.get(env.ws, "sharepoint")


@pytest.mark.parametrize("params, reason", [
    ({"error": "access_denied", "error_description": "AADSTS65004"}, "consent_denied"),
    ({"error": "consent_required"}, "consent_denied"),
    ({"admin_consent": "True"}, "cancelled"),                       # no tenant
])
def test_a_declined_consent_connects_nothing(env, monkeypatch, params, reason):
    rec = Recorded(monkeypatch)
    state = start(env)
    sign_in(rec)
    no_consent(rec)
    consent = env.client.get("/api/integrations/sharepoint/callback", follow_redirects=False,
                             params={"state": state, "code": "c1"}).headers["location"]
    r = env.client.get("/api/integrations/sharepoint/callback", follow_redirects=False,
                       params={"state": query(consent)["state"], **params})
    assert r.headers["location"] == f"/#integrations?error={reason}"


def test_consent_without_the_sites_permission_is_explained(env, monkeypatch):
    rec = Recorded(monkeypatch)
    state = start(env)
    sign_in(rec)
    app_token(rec)
    rec.add("GET", f"{G}/sites/getAllSites", status=403, json_body={
        "error": {"code": "accessDenied", "message": "Either scp or roles claim need to be "
                                                     "present in the token."}})
    r = env.client.get("/api/integrations/sharepoint/callback",
                       params={"state": state, "code": "c1"}, follow_redirects=False)
    assert r.headers["location"] == "/#integrations?error=missing_scope"


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_shows_sites_then_their_libraries(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect(env, picks=())
    rec.add("GET", f"{G}/sites/getAllSites", json_body={"value": [
        {"id": "s-ann", "name": "Ann", "isPersonalSite": True, "webUrl": "https://acme-my/ann"},
        {"id": SITE, "displayName": "HR", "isPersonalSite": False, "webUrl": "https://acme/hr"}],
        "@odata.nextLink": f"{G}/sites/oneDrive.getAllSites?$skiptoken=t2"})
    rec.add("GET", f"{G}/sites/oneDrive.getAllSites", params={"$skiptoken": "t2"},
            json_body={"value": [{"id": "s-eng", "displayName": "Engineering"}]})
    rec.add("GET", f"{G}/sites/{SITE}/drives", json_body={"value": [
        {"id": "drv-1", "name": "Documents", "webUrl": "https://acme/hr/Shared%20Documents"}]})
    headers = env.headers()
    r = env.client.get("/api/integrations/sharepoint/containers", headers=headers)
    assert r.status_code == 200, r.text
    assert [(c["name"], c["kind"]) for c in r.json()["items"]] == [
        ("Engineering", "site"), ("HR", "site"), ("Ann", "onedrive")]
    r = env.client.get("/api/integrations/sharepoint/containers", headers=headers,
                       params={"parent": SITE})
    [lib] = r.json()["items"]
    assert (lib["id"], lib["kind"], lib["site"]) == ("drv-1", "drive", SITE)
    r = env.client.put("/api/integrations/sharepoint/containers", headers=headers,
                       json={"containers": [lib]})
    assert r.json()["containers"] == [{"id": "drv-1", "name": "Documents", "kind": "drive",
                                       "site": SITE}]
    r = env.client.put("/api/integrations/sharepoint/containers", headers=headers,
                       json={"containers": [{"id": "x", "name": "x", "kind": "folder"}]})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_full_sync_reads_the_delta_and_captures_permissions(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    full(rec)
    connect(env)
    summary = sync.sync(env.ws, "sharepoint")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"], summary["skipped"]) == ("full", 2, 1)
    assert all(c["headers"]["Authorization"] == "Bearer app-token" for c in rec.calls)
    assert all(c["headers"]["User-Agent"] == "ISV|Cloudastra|KnowledgeAssistant/1.0"
               for c in rec.calls)
    leave = item(env, "drv-1:f-docx")
    assert env.text("sharepoint", leave["file_name"]) == ("Leave policy.docx\n\n# Leave\n"
                                                          "Twenty days a year.")
    assert leave["acl"] == {"users": ["ann@acme.com"],
                            "groups": ["entra:g-hr", f"sharepoint:{SITE}:4"],
                            "anyone": False, "organization": True}
    assert item(env, "drv-1:f-txt")["acl"]["anyone"] is True
    assert item(env, "drv-1:f-big")["skipped_reason"] == "too_large"
    assert not rec.requests_to("f-big/") and not rec.requests_to("f-png")
    cursor = json.loads(sync_store.state(env.ws, "sharepoint")["cursor"])
    assert cursor["links"] == {"drv-1": f"{G}/drives/drv-1/root/delta?token=d1"}


def test_then_follows_the_delta_link(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    full(rec)
    connect(env)
    sync.sync(env.ws, "sharepoint")
    env.changed.clear()
    rec.load("sharepoint/incremental")
    summary = sync.sync(env.ws, "sharepoint")
    assert rec.unused() == []
    # f-docx deleted; f-new added; f-txt's sharing changed (permissions only).
    assert (summary["mode"], summary["upserted"], summary["deleted"], summary["unchanged"]) == (
        "incremental", 1, 1, 1)
    assert item(env, "drv-1:f-docx") is None
    assert item(env, "drv-1:f-txt")["acl"]["users"] == ["bob@acme.com"]
    assert not rec.requests_to("f-txt/content")[1:]                # not downloaded again
    assert json.loads(sync_store.state(env.ws, "sharepoint")["cursor"])["links"] == {
        "drv-1": f"{G}/drives/drv-1/root/delta?token=d2"}


def test_an_expired_delta_token_lists_everything_again(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    full(rec)
    connect(env)
    sync.sync(env.ws, "sharepoint")
    rec.add("GET", f"{G}/sites/{SITE}/drives", json_body={"value": [{"id": "drv-1"}]})
    rec.add("GET", f"{G}/drives/drv-1/root/delta", params={"token": "d1"}, status=410,
            headers={"Location": f"{G}/drives/drv-1/root/delta"},
            json_body={"error": {"code": "resyncRequired", "message": "Resync required."}})
    rec.add("GET", f"{G}/drives/drv-1/root/delta", json_body={
        "value": [], "@odata.deltaLink": f"{G}/drives/drv-1/root/delta?token=d9"})
    summary = sync.sync(env.ws, "sharepoint", confirm_deletes=True)
    assert summary["mode"] == "full" and summary["deleted"] == 2
    assert json.loads(sync_store.state(env.ws, "sharepoint")["cursor"])["links"]["drv-1"].endswith(
        "token=d9")


def test_the_mass_delete_guard_holds_a_delta_full_of_deletes(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    full(rec)
    connect(env)
    sync.sync(env.ws, "sharepoint")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.add("GET", f"{G}/sites/{SITE}/drives", json_body={"value": [{"id": "drv-1"}]})
    rec.add("GET", f"{G}/drives/drv-1/root/delta", params={"token": "d1"}, json_body={
        "value": [{"id": "f-docx", "deleted": {}}, {"id": "f-txt", "deleted": {}}],
        "@odata.deltaLink": f"{G}/drives/drv-1/root/delta?token=d2"})
    summary = sync.sync(env.ws, "sharepoint")
    assert (summary["held_deletes"], summary["deleted"]) == (2, 0)
    assert item(env, "drv-1:f-docx") is not None
    # Held: the cursor didn't move, so confirming replays the same delta.
    assert json.loads(sync_store.state(env.ws, "sharepoint")["cursor"])["links"]["drv-1"].endswith(
        "token=d1")
    assert card(env)["held_deletes"] == 2


def test_graph_throttling_is_waited_out(env, monkeypatch):
    from connectors import sharepoint
    rec = Recorded(monkeypatch)
    connect(env)
    url = f"{G}/sites/{SITE}/drives"
    rec.add("GET", url, status=429, headers={"Retry-After": "10"}, json_body={
        "error": {"code": "TooManyRequests", "message": "Please retry again later."}})
    rec.add("GET", url, status=503, headers={"Retry-After": "4"})
    rec.add("GET", url, json_body={"value": [{"id": "drv-1"}]})
    assert sharepoint.CONNECTOR._drives(env.ws, [{"id": SITE, "kind": "site"}]) == [
        ("drv-1", SITE)]
    assert rec.slept[:2] == [10.0, 4.0]


def test_revoked_consent_waits_for_a_reconnect(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env, expires_at=time.time() - 10)
    no_consent(rec, code="AADSTS700016")
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "sharepoint")
    assert card(env)["error"]["code"] == "auth_expired"


def test_a_missing_permission_on_a_listing_is_its_own_error(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("GET", f"{G}/sites/{SITE}/drives", status=403, repeat=True,
            json_body={"error": {"code": "accessDenied", "message": "Access denied"}})
    with pytest.raises(base.MissingScope):
        sync.sync(env.ws, "sharepoint")
    assert card(env)["error"] == {"code": "missing_scope",
                                  "message": base.ERRORS["missing_scope"]}


def test_a_private_file_is_skipped_not_fatal(env, monkeypatch):
    """A 403 on one file's download (not a listing) skips that file."""
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    rec.load("sharepoint/full_sync")
    rec.add("GET", f"{G}/drives/drv-1/items/f-docx/content", status=403,
            json_body={"error": {"code": "accessDenied"}})
    connect(env)
    summary = sync.sync(env.ws, "sharepoint")
    assert summary["upserted"] == 1
    assert sync_store.item(env.ws, "sharepoint", "drv-1:f-docx")["skipped_reason"] == "no_access"
