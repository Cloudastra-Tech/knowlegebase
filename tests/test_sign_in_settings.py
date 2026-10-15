"""
A workspace's own sign-in settings: domains it proves with DNS, OpenID Connect
providers its admin adds, and signing in through them — tested through the API.

Faked: DNS answers, the provider's settings document, and the provider's
answer at the callback (the claims of a verified ID token). The rest is real.
"""

import json
import socket
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

import url_guard

ISSUER = "https://idp.acme.example/realms/acme"
DOC = {"issuer": ISSUER,
       "authorization_endpoint": ISSUER + "/protocol/openid-connect/auth",
       "token_endpoint": ISSUER + "/protocol/openid-connect/token",
       "jwks_uri": ISSUER + "/protocol/openid-connect/certs",
       "id_token_signing_alg_values_supported": ["RS256"],
       "grant_types_supported": ["authorization_code"]}       # not kept
PUBLIC_IP = "93.184.216.34"


def addr(ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Every host resolves to a public address unless a test says otherwise."""
    answers = {}
    monkeypatch.setattr(url_guard, "_resolve",
                        lambda host, port: addr(answers.get(host, PUBLIC_IP)))
    return answers


@pytest.fixture
def provider_doc(monkeypatch):
    """The settings document the provider serves; tests may change it.
    doc["_fetched"] lists the URLs asked for (not a real setting, so not kept)."""
    import sso_setup
    doc = {**DOC, "_fetched": []}

    def get(url):
        doc["_fetched"].append(url)
        return json.dumps(doc).encode()
    monkeypatch.setattr(sso_setup, "_get", get)
    return doc


@pytest.fixture
def dns_txt(monkeypatch):
    import sso_setup
    records = {}
    monkeypatch.setattr(sso_setup, "txt_values", lambda name: records.get(name, []))
    return records


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    import auth
    import members_store
    for name, ws, role in (("acme-admin", "acme", "admin"), ("acme-hr", "acme", "hr"),
                           ("globex-admin", "globex", "admin")):
        members_store.set_password(name, name, auth.hash_password("correct-horse"), ws, role)
    return TestClient(api.app, follow_redirects=False)


def as_user(client, username, role="hr"):
    r = client.post("/api/auth/login", json={"username": username, "password": "correct-horse"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": role}


@pytest.fixture(scope="module")
def acme(client):
    return as_user(client, "acme-admin")


@pytest.fixture(scope="module")
def globex(client):
    return as_user(client, "globex-admin")


def verified_domain(client, headers, dns_txt, domain):
    row = client.post("/api/admin/sign-in/domains", headers=headers, json={"domain": domain}).json()
    dns_txt[row["txt_name"]] = ["v=spf1 -all", row["txt_value"]]
    r = client.post(f"/api/admin/sign-in/domains/{domain}/verify", headers=headers)
    assert r.status_code == 200 and r.json()["verified"]


def new_connection(client, headers, label="Acme SSO"):
    r = client.post("/api/admin/sign-in/connections", headers=headers,
                    json={"label": label, "issuer": ISSUER, "client_id": "kb",
                          "client_secret": "s3cret-value"})
    assert r.status_code == 200, r.text
    return r.json()


# ---- url_guard -------------------------------------------------------------------
@pytest.mark.parametrize("url, ip", [
    ("http://idp.example.com/", PUBLIC_IP),               # not https
    ("https://idp.example.com/", "10.0.0.5"),
    ("https://idp.example.com/", "127.0.0.1"),
    ("https://idp.example.com/", "169.254.169.254"),      # cloud metadata service
    ("https://idp.example.com/", "100.64.0.1"),           # carrier-grade NAT
    ("https://idp.example.com/", "::1"),
    ("https://idp.example.com/", "::ffff:127.0.0.1"),
    ("https://idp.example.com/", "fd00::1"),
    ("https://user:pw@idp.example.com/", PUBLIC_IP),
    ("http://localhost:8181/", "127.0.0.1"),             # only the listed port
])
def test_the_server_only_calls_public_https_addresses(public_dns, monkeypatch, url, ip):
    monkeypatch.setenv("SSO_ALLOW_PRIVATE_HOSTS", "localhost:8180")
    public_dns[urlsplit(url).hostname] = ip
    with pytest.raises(url_guard.UnsafeURL):
        url_guard.check(url)


def test_a_listed_local_provider_is_allowed(monkeypatch):
    monkeypatch.setenv("SSO_ALLOW_PRIVATE_HOSTS", "localhost:8180")
    url_guard.check("http://localhost:8180/realms/kb")
    url_guard.check("https://idp.example.com/realms/kb")


def test_one_private_answer_is_enough_to_refuse(monkeypatch):
    monkeypatch.setattr(url_guard, "_resolve",
                        lambda host, port: addr(PUBLIC_IP) + addr("10.1.2.3"))
    with pytest.raises(url_guard.UnsafeURL, match="private"):
        url_guard.check("https://idp.example.com/")


# ---- Who may change the settings -------------------------------------------------
def test_only_admins_change_sign_in_settings(client, acme):
    assert client.get("/api/admin/sign-in", headers=acme).status_code == 200
    hr = as_user(client, "acme-hr")
    assert client.get("/api/admin/sign-in", headers=hr).status_code == 403
    viewing_as_public = {**acme, "X-Role": "public"}
    assert client.get("/api/admin/sign-in", headers=viewing_as_public).status_code == 403
    assert client.get("/api/admin/sign-in").status_code == 401


# ---- Domains ---------------------------------------------------------------------
def test_a_domain_signs_nobody_in_until_dns_proves_it(client, acme, dns_txt):
    r = client.post("/api/admin/sign-in/domains", headers=acme, json={"domain": " @Acme.example. "})
    row = r.json()
    assert (row["domain"], row["verified"], row["txt_name"]) == (
        "acme.example", False, "_knowledge-assistant.acme.example")
    assert row["txt_value"].startswith("kb-verify=")
    assert client.post("/api/auth/sso/discover", json={"email": "a@acme.example"}).json() == {
        "connections": []}
    r = client.post("/api/admin/sign-in/domains/acme.example/verify", headers=acme)
    assert r.status_code == 400 and "No TXT record" in r.json()["detail"]
    dns_txt[row["txt_name"]] = ["kb-verify=someone-elses-token"]
    assert client.post("/api/admin/sign-in/domains/acme.example/verify",
                       headers=acme).status_code == 400
    dns_txt[row["txt_name"]] = [row["txt_value"]]
    r = client.post("/api/admin/sign-in/domains/acme.example/verify", headers=acme)
    assert r.json()["verified"]
    # Claiming it again doesn't reset it.
    again = client.post("/api/admin/sign-in/domains", headers=acme,
                        json={"domain": "acme.example"}).json()
    assert again["verified"] and again["txt_value"] == row["txt_value"]


def test_only_one_workspace_can_verify_a_domain(client, acme, globex, dns_txt):
    verified_domain(client, acme, dns_txt, "taken.example")
    row = client.post("/api/admin/sign-in/domains", headers=globex,
                      json={"domain": "taken.example"}).json()
    dns_txt[row["txt_name"]] = dns_txt[row["txt_name"]] + [row["txt_value"]]
    r = client.post("/api/admin/sign-in/domains/taken.example/verify", headers=globex)
    assert r.status_code == 409


def test_workspaces_only_see_and_change_their_own_domains(client, acme, globex, dns_txt):
    verified_domain(client, acme, dns_txt, "private.example")
    seen = [d["domain"] for d in client.get("/api/admin/sign-in", headers=globex).json()["domains"]]
    assert "private.example" not in seen
    assert client.delete("/api/admin/sign-in/domains/private.example",
                         headers=globex).status_code == 404
    assert client.post("/api/admin/sign-in/domains/private.example/verify",
                       headers=globex).status_code == 404
    assert client.delete("/api/admin/sign-in/domains/private.example",
                         headers=acme).status_code == 200


@pytest.mark.parametrize("domain", ["", "localhost", "acme", "-acme.com", "acme..com",
                                    "acme.com/x", "a" * 64 + ".com"])
def test_a_domain_must_look_like_one(client, acme, domain):
    r = client.post("/api/admin/sign-in/domains", headers=acme, json={"domain": domain})
    assert r.status_code == 400


# ---- Providers -------------------------------------------------------------------
def test_a_provider_is_checked_and_its_secret_kept_encrypted(client, acme, provider_doc):
    conn = new_connection(client, acme)
    assert provider_doc["_fetched"] == [ISSUER + "/.well-known/openid-configuration"]
    assert conn["redirect_uri"].endswith(f"/api/auth/sso/c/{conn['id']}/callback")
    assert "client_secret" not in conn and "secret" not in conn
    listed = client.get("/api/admin/sign-in", headers=acme).json()["connections"]
    assert conn["id"] in [c["id"] for c in listed]
    assert "s3cret-value" not in json.dumps(listed)
    import members_store
    with sqlite3.connect(members_store.DB_PATH) as db:
        raw, metadata = db.execute("SELECT secret, metadata FROM sso_connections WHERE id = ?",
                                   (conn["id"],)).fetchone()
    assert b"s3cret-value" not in raw
    assert members_store.connection_keys(conn["id"]) == {"client_secret": "s3cret-value"}
    assert "grant_types_supported" not in json.loads(metadata)


@pytest.mark.parametrize("change, message", [
    ({"issuer": "https://someone-else.example"}, "calls itself"),
    ({"jwks_uri": None}, "no jwks_uri"),
    ({"token_endpoint": "http://idp.acme.example/token"}, "https"),
])
def test_a_provider_with_wrong_settings_is_refused(client, acme, provider_doc, change, message):
    provider_doc.update(change)
    r = client.post("/api/admin/sign-in/connections", headers=acme,
                    json={"label": "Bad", "issuer": ISSUER, "client_id": "kb",
                          "client_secret": "x"})
    assert r.status_code == 400 and message in r.json()["detail"]


def test_a_provider_on_a_private_address_is_refused(client, acme, provider_doc, public_dns):
    public_dns["idp.acme.example"] = "169.254.169.254"
    r = client.post("/api/admin/sign-in/connections", headers=acme,
                    json={"label": "Metadata", "issuer": ISSUER, "client_id": "kb",
                          "client_secret": "x"})
    assert r.status_code == 400 and "private" in r.json()["detail"]
    assert provider_doc["_fetched"] == []                  # never even asked


def test_workspaces_only_see_and_change_their_own_providers(client, acme, globex, provider_doc):
    conn = new_connection(client, acme)
    assert conn["id"] not in [c["id"] for c in
                              client.get("/api/admin/sign-in", headers=globex).json()["connections"]]
    path = f"/api/admin/sign-in/connections/{conn['id']}"
    assert client.patch(path, headers=globex, json={"enabled": False}).status_code == 404
    assert client.delete(path, headers=globex).status_code == 404
    assert client.patch(path, headers=acme, json={"label": "Renamed"}).json()["label"] == "Renamed"


# ---- Signing in through a workspace's own provider -------------------------------
@pytest.fixture
def idp(monkeypatch):
    """Stand in for the provider at the callback: it returns these claims."""
    import sso

    class FakeProvider:
        claims = {}

        async def authorize_access_token(self, request):
            return {"userinfo": self.claims}
    fake = FakeProvider()
    monkeypatch.setattr(sso, "connection_client", lambda conn: fake)
    return fake


@pytest.fixture(scope="module")
def acme_sso(client, acme):
    """acme.example verified, with an enabled provider."""
    import members_store
    import sso_setup
    members_store.add_verified_domain("acme", "acme.example")
    return members_store.create_connection("acme", "oidc", "Acme SSO", ISSUER, "kb",
                                           {"client_secret": "s3cret-value"},
                                           {k: DOC[k] for k in sso_setup.METADATA_KEYS
                                            if k in DOC}, "test")


def callback(client, idp, conn, claims):
    idp.claims = claims
    r = client.get(f"/api/auth/sso/c/{conn['id']}/callback?code=x&state=y")
    assert r.status_code in (302, 303, 307)
    return r.headers["location"]


def session_for(client, idp, conn, claims):
    location = callback(client, idp, conn, claims)
    assert location.startswith("/#sso?code="), location
    r = client.post("/api/auth/sso/exchange", json={"code": location.split("code=")[1]})
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def test_the_login_page_finds_the_provider_from_the_email(client, acme_sso):
    assert client.get("/api/auth/providers").json()["email_sso"] is True
    found = client.post("/api/auth/sso/discover", json={"email": "Pat@Acme.example"}).json()
    ids = [c["id"] for c in found["connections"]]
    assert acme_sso["id"] in ids
    url = next(c["url"] for c in found["connections"] if c["id"] == acme_sso["id"])
    assert url == f"/api/auth/sso/c/{acme_sso['id']}/start?login_hint=pat%40acme.example"
    assert client.post("/api/auth/sso/discover",
                       json={"email": "pat@unknown.example"}).json() == {"connections": []}
    assert client.post("/api/auth/sso/discover", json={"email": "not-an-email"}).status_code == 400


def test_start_sends_the_browser_to_the_saved_endpoint(client, acme_sso):
    r = client.get(f"/api/auth/sso/c/{acme_sso['id']}/start?login_hint=pat%40acme.example")
    assert r.status_code == 302
    target = urlsplit(r.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == DOC["authorization_endpoint"]
    q = parse_qs(target.query)
    assert q["client_id"] == ["kb"] and q["login_hint"] == ["pat@acme.example"]
    assert q["code_challenge_method"] == ["S256"] and q["state"] and q["nonce"]
    assert q["redirect_uri"][0].endswith(f"/api/auth/sso/c/{acme_sso['id']}/callback")
    assert client.get("/api/auth/sso/c/nope/start").headers["location"] == (
        "/#login?error=sso_unavailable")


def test_a_workspace_provider_signs_people_in_to_that_workspace(client, idp, acme_sso):
    headers = session_for(client, idp, acme_sso, {"email": "pat@acme.example",
                                                  "email_verified": True, "name": "Pat"})
    me = client.get("/api/auth/me", headers=headers).json()
    assert (me["username"], me["workspace"], me["role"], me["connection"]) == (
        "pat@acme.example", "acme", "public", acme_sso["id"])
    # No email_verified claim at all (Entra ID): the domain check is enough.
    session_for(client, idp, acme_sso, {"email": "lee@acme.example"})


@pytest.mark.parametrize("claims, error", [
    # A provider can only vouch for its own workspace's verified domains.
    ({"email": "boss@globex.example", "email_verified": True}, "not_allowed"),
    ({"email": "pat@acme.example", "email_verified": False}, "unverified"),
    ({"email": "pat@acme.example", "email_verified": "false"}, "unverified"),
    ({"email_verified": True}, "unverified"),
])
def test_a_workspace_provider_cant_vouch_for_anyone_else(client, idp, acme_sso, claims, error):
    import members_store
    members_store.add_verified_domain("globex", "globex.example")
    assert callback(client, idp, acme_sso, claims) == f"/#login?error={error}"


def test_turning_a_provider_off_signs_its_people_out(client, idp, acme_sso, acme):
    headers = session_for(client, idp, acme_sso, {"email": "kim@acme.example",
                                                  "email_verified": True})
    path = f"/api/admin/sign-in/connections/{acme_sso['id']}"
    assert client.patch(path, headers=acme, json={"enabled": False}).status_code == 200
    try:
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        assert callback(client, idp, acme_sso, {"email": "kim@acme.example"}) == (
            "/#login?error=sso_unavailable")
        found = client.post("/api/auth/sso/discover", json={"email": "kim@acme.example"}).json()
        assert acme_sso["id"] not in [c["id"] for c in found["connections"]]
    finally:
        client.patch(path, headers=acme, json={"enabled": True})


# ---- "Test sign-in" and the event log ----------------------------------------------
def start_test(client, acme, conn):
    r = client.post(f"/api/admin/sign-in/connections/{conn['id']}/test", headers=acme)
    assert r.status_code == 200
    body = r.json()
    started = client.get(body["url"])
    assert started.status_code in (302, 307)
    return body["ticket"], started.headers["location"]


def result_of(client, acme, ticket):
    return client.get(f"/api/admin/sign-in/tests/{ticket}", headers=acme).json()


def test_an_admin_can_try_a_provider_without_signing_anyone_in(client, acme, acme_sso,
                                                               monkeypatch):
    import members_store
    import sso
    ticket, location = start_test(client, acme, acme_sso)
    assert "prompt=login" in location                     # sign in fresh, as anyone
    assert result_of(client, acme, ticket) == {"done": False, "result": None}
    fake = type("P", (), {"authorize_access_token": None})()

    async def answer(request):
        return {"userinfo": {"email": "tester@acme.example", "email_verified": True,
                             "name": "Tess", "groups": ["eng", "hr"], "nonce": "x"}}
    fake.authorize_access_token = answer
    monkeypatch.setattr(sso, "connection_client", lambda conn: fake)
    r = client.get(f"/api/auth/sso/c/{acme_sso['id']}/callback?code=x&state=y")
    assert r.headers["location"] == "/#sso-test"           # no session code
    result = result_of(client, acme, ticket)["result"]
    assert result["ok"] and (result["email"], result["role"], result["new_member"]) == (
        "tester@acme.example", "public", True)
    assert result["sent"]["groups"] == "eng, hr" and "nonce" not in result["sent"]
    assert members_store.get("tester@acme.example") is None   # nobody was added
    events = client.get("/api/admin/sign-in", headers=acme).json()["events"]
    assert events[0]["outcome"] == "test" and events[0]["connection"] == "Acme SSO"


def test_a_test_reports_why_someone_would_be_refused(client, acme, acme_sso, idp):
    ticket, _ = start_test(client, acme, acme_sso)
    callback(client, idp, acme_sso, {"email": "x@globex.example", "email_verified": True})
    result = result_of(client, acme, ticket)["result"]
    assert (result["ok"], result["reason"], result["detail"]) == (
        False, "not_allowed", "globex.example isn't a verified domain of this workspace.")


def test_a_provider_can_be_tried_while_it_is_off(client, acme, acme_sso, monkeypatch):
    path = f"/api/admin/sign-in/connections/{acme_sso['id']}"
    client.patch(path, headers=acme, json={"enabled": False})
    try:
        _, location = start_test(client, acme, acme_sso)
        assert location.startswith(DOC["authorization_endpoint"])
        # ... but a real sign-in still can't use it.
        assert client.get(f"/api/auth/sso/c/{acme_sso['id']}/start").headers["location"] == (
            "/#login?error=sso_unavailable")
    finally:
        client.patch(path, headers=acme, json={"enabled": True})


def test_a_real_sign_in_after_a_test_is_a_real_sign_in(client, acme, acme_sso, idp):
    start_test(client, acme, acme_sso)                    # started, never finished
    client.get(f"/api/auth/sso/c/{acme_sso['id']}/start")  # then a real one
    location = callback(client, idp, acme_sso, {"email": "real@acme.example",
                                                 "email_verified": True})
    assert location.startswith("/#sso?code=")


def test_tests_belong_to_their_workspace(client, acme, globex, acme_sso):
    ticket, _ = start_test(client, acme, acme_sso)
    assert client.get(f"/api/admin/sign-in/tests/{ticket}", headers=globex).status_code == 404
    client.cookies.clear()          # drop the unfinished test (a real /start clears it too)
    assert client.post(f"/api/admin/sign-in/connections/{acme_sso['id']}/test",
                       headers=globex).status_code == 404


def test_sign_ins_and_refusals_are_logged_for_the_admin(client, acme, globex, acme_sso, idp):
    session_for(client, idp, acme_sso, {"email": "logged@acme.example", "email_verified": True})
    callback(client, idp, acme_sso, {"email": "logged@acme.example", "email_verified": False})
    events = client.get("/api/admin/sign-in", headers=acme).json()["events"]
    assert [(e["email"], e["outcome"], e["reason"]) for e in events[:2]] == [
        ("logged@acme.example", "refused", "unverified"),
        ("logged@acme.example", "signed_in", None)]
    assert all(e["email"] != "logged@acme.example"
               for e in client.get("/api/admin/sign-in", headers=globex).json()["events"])


def test_the_idp_addresses_are_known_before_the_provider_is_added(client, acme, provider_doc):
    new = client.get("/api/admin/sign-in/new-provider", headers=acme).json()
    assert new["redirect_uri"].endswith(f"/api/auth/sso/c/{new['id']}/callback")
    assert new["sp_entity_id"].endswith(f"/api/auth/saml/{new['id']}/metadata")
    body = {"id": new["id"], "label": "Planned", "issuer": ISSUER, "client_id": "kb",
            "client_secret": "s"}
    added = client.post("/api/admin/sign-in/connections", headers=acme, json=body).json()
    assert added["id"] == new["id"] and added["redirect_uri"] == new["redirect_uri"]
    assert client.post("/api/admin/sign-in/connections", headers=acme,
                       json=body).status_code == 409
    assert client.post("/api/admin/sign-in/connections", headers=acme,
                       json={**body, "id": "../../x"}).status_code == 422


# ---- Roles from groups, and members ------------------------------------------------
def set_rules(client, acme, conn, rules, claim=None):
    r = client.put(f"/api/admin/sign-in/connections/{conn['id']}/roles", headers=acme,
                   json={"group_claim": claim, "rules": [{"group": g, "role": role}
                                                         for g, role in rules]})
    assert r.status_code == 200, r.text
    return r.json()


def me(client, headers):
    return client.get("/api/auth/me", headers=headers).json()


def test_groups_from_the_provider_set_the_role(client, acme, acme_sso, idp):
    out = set_rules(client, acme, acme_sso, [("Engineering", "hr"), ("it-admins", "admin")])
    assert out["group_rules"] == [{"group": "Engineering", "role": "hr"},
                                  {"group": "it-admins", "role": "admin"}]
    try:
        claims = {"email": "grp@acme.example", "email_verified": True,
                  "groups": ["engineering", "sales"]}
        assert me(client, session_for(client, idp, acme_sso, claims))["role"] == "hr"
        claims["groups"] = ["it-admins", "engineering"]
        assert me(client, session_for(client, idp, acme_sso, claims))["role"] == "admin"
        claims["groups"] = []                          # left the groups at the provider
        assert me(client, session_for(client, idp, acme_sso, claims))["role"] == "public"
    finally:
        set_rules(client, acme, acme_sso, [])


def test_a_nested_claim_can_carry_the_groups(client, acme, acme_sso, idp):
    set_rules(client, acme, acme_sso, [("hr", "hr")], claim="realm_access.roles")
    try:
        claims = {"email": "nested@acme.example", "email_verified": True,
                  "realm_access": {"roles": ["offline_access", "hr"]}}
        assert me(client, session_for(client, idp, acme_sso, claims))["role"] == "hr"
    finally:
        set_rules(client, acme, acme_sso, [])


def test_an_admin_override_beats_the_groups(client, acme, acme_sso, idp):
    set_rules(client, acme, acme_sso, [("Engineering", "hr")])
    try:
        claims = {"email": "over@acme.example", "email_verified": True, "groups": ["Engineering"]}
        headers = session_for(client, idp, acme_sso, claims)
        path = "/api/admin/members/over@acme.example"
        out = client.patch(path, headers=acme, json={"role": "public"}).json()
        assert (out["role"], out["role_source"], out["group_role"]) == ("public", "override", "hr")
        assert me(client, headers)["role"] == "public"                   # straight away
        out = client.patch(path, headers=acme, json={"role": None}).json()
        assert (out["role"], out["role_source"]) == ("hr", "groups")
    finally:
        set_rules(client, acme, acme_sso, [])


def test_a_test_sign_in_shows_the_groups_and_the_role_they_give(client, acme, acme_sso,
                                                               monkeypatch):
    import sso
    set_rules(client, acme, acme_sso, [("it-admins", "admin")])
    try:
        ticket, _ = start_test(client, acme, acme_sso)
        fake = type("P", (), {})()

        async def answer(request):
            return {"userinfo": {"email": "would@acme.example", "email_verified": True,
                                 "groups": ["it-admins"]}}
        fake.authorize_access_token = answer
        monkeypatch.setattr(sso, "connection_client", lambda conn: fake)
        client.get(f"/api/auth/sso/c/{acme_sso['id']}/callback?code=x&state=y")
        result = result_of(client, acme, ticket)["result"]
        assert (result["role"], result["role_source"], result["groups"]) == (
            "admin", "groups", ["it-admins"])
    finally:
        set_rules(client, acme, acme_sso, [])


def test_turning_someone_off_ends_their_access(client, acme, acme_sso, idp):
    claims = {"email": "off@acme.example", "email_verified": True}
    headers = session_for(client, idp, acme_sso, claims)
    path = "/api/admin/members/off@acme.example"
    assert client.patch(path, headers=acme, json={"disabled": True}).json()["disabled"]
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert callback(client, idp, acme_sso, claims) == "/#login?error=disabled"
    client.patch(path, headers=acme, json={"disabled": False})
    assert callback(client, idp, acme_sso, claims).startswith("/#sso?code=")
    assert client.delete(path, headers=acme).status_code == 200
    assert "off@acme.example" not in [m["username"] for m in
                                      client.get("/api/admin/members", headers=acme).json()["members"]]


def test_members_are_managed_per_workspace_and_not_by_yourself(client, acme, globex, acme_sso,
                                                               idp):
    session_for(client, idp, acme_sso, {"email": "mine@acme.example", "email_verified": True})
    listed = [m["username"] for m in client.get("/api/admin/members", headers=acme).json()["members"]]
    assert "mine@acme.example" in listed and "globex-admin" not in listed
    assert client.patch("/api/admin/members/mine@acme.example", headers=globex,
                        json={"role": "admin"}).status_code == 404
    assert client.patch("/api/admin/members/acme-admin", headers=acme,
                        json={"role": "public"}).status_code == 400
    assert client.delete("/api/admin/members/acme-admin", headers=acme).status_code == 400
    hr = as_user(client, "acme-hr")
    assert client.get("/api/admin/members", headers=hr).status_code == 403
    assert client.patch("/api/admin/members/mine@acme.example", headers=acme,
                        json={"role": "owner"}).status_code == 422


# ---- Requiring single sign-on, signing out at the provider, limits -----------------
def test_requiring_sso_leaves_passwords_to_admins(client, acme, acme_sso):
    hr_headers = as_user(client, "acme-hr")
    assert client.put("/api/admin/sign-in/policy", headers=acme,
                      json={"require_sso": True}).json() == {"require_sso": True}
    try:
        assert client.get("/api/admin/sign-in", headers=acme).json()["require_sso"] is True
        assert client.get("/api/auth/me", headers=hr_headers).status_code == 401   # live session
        r = client.post("/api/auth/login", json={"username": "acme-hr",
                                                 "password": "correct-horse"})
        assert r.status_code == 403 and "single sign-on" in r.json()["detail"]
        wrong = client.post("/api/auth/login", json={"username": "acme-hr", "password": "nope"})
        assert wrong.status_code == 401        # says nothing until the password is right
        as_user(client, "acme-admin")          # the admin's way in if the provider breaks
        events = client.get("/api/admin/sign-in", headers=acme).json()["events"]
        assert ("acme-hr", "refused", "sso_required", "password") in [
            (e["email"], e["outcome"], e["reason"], e["connection"]) for e in events]
    finally:
        client.put("/api/admin/sign-in/policy", headers=acme, json={"require_sso": False})
    as_user(client, "acme-hr")


def test_sso_can_only_be_required_once_someone_could_sign_in(client):
    import auth
    import members_store
    members_store.set_password("lonely-admin", "L", auth.hash_password("correct-horse"),
                               "lonely", "admin")
    lonely = as_user(client, "lonely-admin")
    r = client.put("/api/admin/sign-in/policy", headers=lonely, json={"require_sso": True})
    assert r.status_code == 400 and "Verify a domain" in r.json()["detail"]
    members_store.add_verified_domain("lonely", "lonely.example")
    r = client.put("/api/admin/sign-in/policy", headers=lonely, json={"require_sso": True})
    assert r.status_code == 400 and "provider" in r.json()["detail"]


def test_signing_out_can_sign_out_at_the_provider_too(client, acme, monkeypatch):
    import members_store
    import sso
    conn = members_store.create_connection(
        "acme", "oidc", "With logout", ISSUER, "kb", {"client_secret": "s"},
        {**{k: DOC[k] for k in ("issuer", "authorization_endpoint", "token_endpoint",
                                "jwks_uri")},
         "end_session_endpoint": ISSUER + "/protocol/openid-connect/logout"}, "test")
    fake = type("P", (), {})()

    async def answer(request):
        return {"userinfo": {"email": "bye@acme.example", "email_verified": True},
                "id_token": "the.id.token"}
    fake.authorize_access_token = answer
    monkeypatch.setattr(sso, "connection_client", lambda c: fake)

    def signed_in():
        location = client.get(f"/api/auth/sso/c/{conn['id']}/callback?code=x&state=y")
        r = client.post("/api/auth/sso/exchange",
                        json={"code": location.headers["location"].split("code=")[1]})
        assert "id_token" not in r.json()["user"]                 # never to the browser
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        assert "id_token" not in client.get("/api/auth/me", headers=headers).json()
        return headers

    out = client.get("/api/admin/sign-in", headers=acme).json()["connections"]
    assert next(c for c in out if c["id"] == conn["id"])["supports_logout"] is True
    # Off by default: signing out here doesn't sign them out of everything else.
    assert client.post("/api/auth/logout", headers=signed_in()).json()["logout_url"] is None
    client.patch(f"/api/admin/sign-in/connections/{conn['id']}", headers=acme,
                 json={"logout_at_idp": True})
    url = client.post("/api/auth/logout", headers=signed_in()).json()["logout_url"]
    target = urlsplit(url)
    q = parse_qs(target.query)
    assert f"{target.scheme}://{target.netloc}{target.path}" == (
        ISSUER + "/protocol/openid-connect/logout")
    assert q["id_token_hint"] == ["the.id.token"] and q["client_id"] == ["kb"]
    assert q["post_logout_redirect_uri"][0].endswith("/")
    client.delete(f"/api/admin/sign-in/connections/{conn['id']}", headers=acme)


def test_the_public_sign_in_endpoints_are_rate_limited(client, monkeypatch):
    import api
    monkeypatch.setenv("RATE_LIMITS", "on")
    monkeypatch.setattr(api, "_hits", {})
    codes = [client.post("/api/auth/sso/discover", json={"email": "x@acme.example"}).status_code
             for _ in range(api.LIMITS["discover"] + 1)]
    assert codes[-2] == 200 and codes[-1] == 429
