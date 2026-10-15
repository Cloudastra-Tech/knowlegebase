"""
SAML sign-in through a workspace's own identity provider, tested through the
API against a REAL SAML IdP: pysaml2's own IdP, with its own keys, signs every
answer here. Nothing in the signature, audience, timing or replay checks is
faked. Needs the xmlsec1 program (CI installs it; locally set XMLSEC_BINARY).
"""

import base64
import os
import re
import urllib.parse

import pytest

import saml_sp

pytestmark = pytest.mark.skipif(not saml_sp.xmlsec_binary(),
                                reason="xmlsec1 isn't installed (set XMLSEC_BINARY)")

IDP_ENTITY = "https://idp.samlco.example/saml"
IDP_SSO = "https://idp.samlco.example/saml/sso"
PASSWORD_CLASS = "urn:oasis:names:tc:SAML:2.0:ac:classes:Password"


class TestIdP:
    """A real SAML identity provider (pysaml2's), with its own signing key."""

    def __init__(self, folder, want_signed_requests=True, key=None, lifetime=None):
        from saml2.config import IdPConfig
        from saml2 import BINDING_HTTP_REDIRECT
        os.makedirs(folder, exist_ok=True)
        key_pem, cert_pem = key or saml_sp.new_keypair("test idp")
        self.key = (key_pem, cert_pem)
        paths = {}
        for name, text in (("idp.key", key_pem), ("idp.crt", cert_pem)):
            paths[name] = os.path.join(folder, name)
            with open(paths[name], "w") as f:
                f.write(text)
        self.settings = {
            "entityid": IDP_ENTITY, "xmlsec_binary": saml_sp.xmlsec_binary(),
            "key_file": paths["idp.key"], "cert_file": paths["idp.crt"],
            "service": {"idp": {
                "endpoints": {"single_sign_on_service": [(IDP_SSO, BINDING_HTTP_REDIRECT)]},
                "want_authn_requests_signed": want_signed_requests}},
            "metadata": {}}
        if lifetime:                     # how long its assertions are valid
            self.settings["service"]["idp"]["policy"] = {"default": {"lifetime": lifetime}}
        self.config = IdPConfig().load(self.settings)

    def metadata(self):
        from saml2.metadata import entity_descriptor
        return str(entity_descriptor(self.config))

    def trust(self, *sp_metadata):
        """Load the app's SP metadata, as an IdP admin would."""
        from saml2.config import IdPConfig
        from saml2.server import Server
        config = IdPConfig().load(self.settings)
        for xml in sp_metadata:
            config.metadata.load("inline", xml)
        self.server = Server(config=config)

    def read_request(self, location):
        """The login request the app sent the browser here with. Checks its signature."""
        from saml2 import BINDING_HTTP_REDIRECT
        assert location.startswith(IDP_SSO + "?")
        q = dict(urllib.parse.parse_qsl(location.split("?", 1)[1]))
        req = self.server.parse_authn_request(q["SAMLRequest"], BINDING_HTTP_REDIRECT,
                                              relay_state=q.get("RelayState"),
                                              sigalg=q.get("SigAlg"),
                                              signature=q.get("Signature"))
        return req.message

    def answer(self, request, email, sign_assertion=True, sign_response=False,
               in_response_to="from-request", attributes=None, audience=None):
        """The signed answer the IdP posts back (base64, as in the form)."""
        from saml2.saml import NAMEID_FORMAT_EMAILADDRESS, NameID
        response = self.server.create_authn_response(
            attributes if attributes is not None else {"mail": [email], "displayName": ["Pat"]},
            in_response_to=request.id if in_response_to == "from-request" else in_response_to,
            destination=request.assertion_consumer_service_url,
            sp_entity_id=audience or request.issuer.text,
            name_id=NameID(format=NAMEID_FORMAT_EMAILADDRESS, text=email),
            sign_assertion=sign_assertion, sign_response=sign_response, userid=email,
            authn={"class_ref": PASSWORD_CLASS})
        return base64.b64encode(str(response).encode()).decode()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    import auth
    import members_store
    members_store.set_password("saml-admin", "Admin", auth.hash_password("correct-horse"),
                               "samlco", "admin")
    members_store.add_verified_domain("samlco", "samlco.example")
    return TestClient(api.app, follow_redirects=False)


@pytest.fixture(scope="module")
def admin(client):
    r = client.post("/api/auth/login", json={"username": "saml-admin", "password": "correct-horse"})
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


@pytest.fixture(scope="module")
def idp(tmp_path_factory):
    return TestIdP(str(tmp_path_factory.mktemp("idp")))


@pytest.fixture(scope="module")
def conn(client, admin, idp):
    """A SAML connection for samlco, set up from the IdP's metadata XML."""
    r = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                    json={"label": "SamlCo SSO", "metadata_xml": idp.metadata()})
    assert r.status_code == 200, r.text
    c = r.json()
    idp.trust(client.get(f"/api/auth/saml/{c['id']}/metadata").text)
    return c


def start(client, conn, idp):
    r = client.get(f"/api/auth/saml/{conn['id']}/start")
    assert r.status_code in (302, 307), r.headers.get("location")
    return idp.read_request(r.headers["location"])


def post_answer(client, conn, saml_response):
    r = client.post(f"/api/auth/saml/{conn['id']}/acs",
                    data={"SAMLResponse": saml_response, "RelayState": "kb"})
    assert r.status_code == 303
    return r.headers["location"]


def finish(client, location):
    """Follow /done to the login page or a session."""
    if location.startswith("/api/auth/saml/"):
        location = client.get(location).headers["location"]
    if not location.startswith("/#sso?code="):
        return location
    r = client.post("/api/auth/sso/exchange", json={"code": location.split("code=")[1]})
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def sign_in(client, conn, idp, email="pat@samlco.example", **answer):
    request = start(client, conn, idp)
    return finish(client, post_answer(client, conn, idp.answer(request, email, **answer)))


# ---- The happy paths --------------------------------------------------------------
def test_a_signed_assertion_signs_in_to_the_workspace(client, conn, idp):
    headers = sign_in(client, conn, idp)
    me = client.get("/api/auth/me", headers=headers).json()
    assert (me["username"], me["name"], me["workspace"], me["provider"], me["connection"]) == (
        "pat@samlco.example", "Pat", "samlco", "saml", conn["id"])


def test_a_signed_response_is_enough_too(client, conn, idp):
    headers = sign_in(client, conn, idp, sign_assertion=False, sign_response=True)
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_the_login_request_is_signed_when_the_idp_asks(client, conn, idp):
    location = client.get(f"/api/auth/saml/{conn['id']}/start").headers["location"]
    q = dict(urllib.parse.parse_qsl(location.split("?", 1)[1]))
    assert q["SigAlg"].endswith("rsa-sha256") and q["Signature"]
    assert conn["signs_requests"] is True


def test_the_sp_metadata_has_what_an_idp_needs(client, conn):
    xml = client.get(f"/api/auth/saml/{conn['id']}/metadata").text
    assert f'entityID="{conn["sp_entity_id"]}"' in xml
    assert conn["acs_url"] in xml and "X509Certificate" in xml
    assert conn["sp_entity_id"] == f"http://testserver/api/auth/saml/{conn['id']}/metadata"


def test_the_login_page_finds_a_saml_provider(client, conn):
    found = client.post("/api/auth/sso/discover", json={"email": "x@samlco.example"}).json()
    assert {"id": conn["id"], "name": "SamlCo SSO",
            "url": f"/api/auth/saml/{conn['id']}/start"} in found["connections"]


def test_the_email_can_come_from_the_name_id_alone(client, conn, idp):
    headers = sign_in(client, conn, idp, email="nameid@samlco.example", attributes={})
    assert client.get("/api/auth/me", headers=headers).json()["username"] == (
        "nameid@samlco.example")


# ---- Answers that must not sign anyone in ----------------------------------------
def test_an_unsigned_answer_is_refused(client, conn, idp):
    assert sign_in(client, conn, idp, sign_assertion=False) == "/#login?error=sso_failed"


def test_an_answer_signed_with_another_key_is_refused(client, conn, idp, tmp_path):
    impostor = TestIdP(str(tmp_path / "impostor"))       # same entity ID, its own key
    impostor.trust(client.get(f"/api/auth/saml/{conn['id']}/metadata").text)
    request = start(client, conn, idp)
    forged = impostor.answer(request, "pat@samlco.example")
    assert finish(client, post_answer(client, conn, forged)) == "/#login?error=sso_failed"


def test_an_answer_can_be_used_only_once(client, conn, idp):
    request = start(client, conn, idp)
    answer = idp.answer(request, "pat@samlco.example")
    assert isinstance(finish(client, post_answer(client, conn, answer)), dict)
    assert finish(client, post_answer(client, conn, answer)) == "/#login?error=sso_failed"


def test_an_answer_nobody_asked_for_is_refused(client, conn, idp):
    request = start(client, conn, idp)
    unsolicited = idp.answer(request, "pat@samlco.example", in_response_to=None)
    assert finish(client, post_answer(client, conn, unsolicited)) == "/#login?error=sso_failed"


def test_an_expired_answer_is_refused(client, conn, idp, tmp_path):
    # The real IdP's key, but its assertions ran out 10 minutes ago (more than
    # the 2 minutes of clock difference allowed).
    stale = TestIdP(str(tmp_path / "stale"), key=idp.key, lifetime={"minutes": -10})
    stale.trust(client.get(f"/api/auth/saml/{conn['id']}/metadata").text)
    assert sign_in(client, conn, stale) == "/#login?error=sso_failed"


def test_an_answer_meant_for_another_connection_is_refused(client, admin, conn, idp):
    other = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                        json={"label": "Other", "metadata_xml": idp.metadata()}).json()
    try:
        request = start(client, conn, idp)                # asked for by `conn`...
        answer = idp.answer(request, "pat@samlco.example")
        location = post_answer(client, other, answer)     # ...posted to `other`
        assert finish(client, location) == "/#login?error=sso_failed"
    finally:
        client.delete(f"/api/admin/sign-in/connections/{other['id']}", headers=admin)


def test_an_answer_addressed_to_another_connection_is_refused(client, admin, conn, idp,
                                                             tmp_path):
    """It really answers `other`'s request, but its audience is `conn`."""
    other = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                        json={"label": "Other", "metadata_xml": idp.metadata()}).json()
    try:
        both = TestIdP(str(tmp_path / "both"), key=idp.key)
        both.trust(*(client.get(f"/api/auth/saml/{c['id']}/metadata").text
                     for c in (conn, other)))
        request = start(client, other, both)
        answer = both.answer(request, "pat@samlco.example", audience=conn["sp_entity_id"])
        assert finish(client, post_answer(client, other, answer)) == "/#login?error=sso_failed"
    finally:
        client.delete(f"/api/admin/sign-in/connections/{other['id']}", headers=admin)


def test_a_wrapped_signature_doesnt_smuggle_in_another_user(client, conn, idp):
    """XML signature wrapping: keep the IdP's signed assertion, but put an
    unsigned one for someone else first and hope it's the one read."""
    request = start(client, conn, idp)
    xml = base64.b64decode(idp.answer(request, "pat@samlco.example")).decode()
    signed = re.search(r"<ns\d+:Assertion .*</ns\d+:Assertion>", xml, re.S).group(0)
    evil = re.sub(r"<ds:Signature.*?</ds:Signature>", "", signed, flags=re.S)
    evil = evil.replace("pat@samlco.example", "boss@samlco.example").replace(
        'ID="', 'ID="evil-')
    wrapped = xml.replace(signed, evil + signed)
    location = post_answer(client, conn, base64.b64encode(wrapped.encode()).decode())
    assert finish(client, location) == "/#login?error=sso_failed"


def test_an_answer_only_signs_in_the_browser_that_asked(client, conn, idp):
    """Someone can't start a sign-in, get their own answer, and make another
    person's browser post it (logging them in to the wrong account)."""
    from fastapi.testclient import TestClient
    import api
    request = start(client, conn, idp)                    # the attacker's browser
    location = post_answer(client, conn, idp.answer(request, "attacker@samlco.example"))
    victim = TestClient(api.app, follow_redirects=False)  # no cookie from /start
    assert victim.get(location).headers["location"] == "/#login?error=sso_failed"


def test_an_idp_can_only_vouch_for_its_workspaces_domains(client, conn, idp):
    import members_store
    members_store.add_verified_domain("elsewhere", "elsewhere.example")
    assert sign_in(client, conn, idp, email="boss@elsewhere.example") == (
        "/#login?error=not_allowed")


def test_turning_a_saml_provider_off_signs_its_people_out(client, admin, conn, idp):
    headers = sign_in(client, conn, idp)
    path = f"/api/admin/sign-in/saml-connections/{conn['id']}"
    assert client.patch(path, headers=admin, json={"enabled": False}).json()["enabled"] is False
    try:
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        assert client.get(f"/api/auth/saml/{conn['id']}/start").headers["location"] == (
            "/#login?error=sso_unavailable")
    finally:
        client.patch(path, headers=admin, json={"enabled": True})


# ---- Setting it up -----------------------------------------------------------------
def test_the_three_values_by_hand_work_too(client, admin, idp):
    cert = idp.key[1]
    r = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                    json={"label": "By hand", "entity_id": IDP_ENTITY, "sso_url": IDP_SSO,
                          "certificate": cert})
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["issuer"], c["sso_url"], c["signs_requests"]) == (IDP_ENTITY, IDP_SSO, False)
    client.delete(f"/api/admin/sign-in/connections/{c['id']}", headers=admin)


def test_metadata_can_come_from_a_url(client, admin, idp, monkeypatch):
    import sso_setup
    fetched = []
    # Ask for XML: some IdPs (Keycloak) answer 406 to the JSON an OIDC fetch asks for.
    monkeypatch.setattr(sso_setup, "_get", lambda url, accept, **kwargs: fetched.append(
        (url, "samlmetadata+xml" in accept)) or idp.metadata().encode())
    r = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                    json={"label": "From URL",
                          "metadata_url": "https://idp.samlco.example/metadata"})
    assert r.status_code == 200 and fetched == [("https://idp.samlco.example/metadata", True)]
    assert r.json()["metadata_url"] == "https://idp.samlco.example/metadata"
    client.delete(f"/api/admin/sign-in/connections/{r.json()['id']}", headers=admin)


@pytest.mark.parametrize("body, message", [
    ({"metadata_xml": "<not-saml/>"}, "SAML metadata"),
    ({"metadata_xml": "x", "metadata_url": "https://idp.example/m"}, "only one"),
    ({"entity_id": IDP_ENTITY, "sso_url": IDP_SSO}, "all three"),
    ({"entity_id": IDP_ENTITY, "sso_url": IDP_SSO, "certificate": "bm90IGEgY2VydA=="},
     "certificate"),
    ({"entity_id": IDP_ENTITY, "sso_url": "http://idp.samlco.example/sso",
      "certificate": "CERT"}, "https"),
    ({}, "Give the identity provider's metadata"),
])
def test_bad_settings_are_refused_with_a_reason(client, admin, idp, body, message):
    if body.get("certificate") == "CERT":
        body = {**body, "certificate": idp.key[1]}
    r = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                    json={"label": "Bad", **body})
    assert r.status_code == 400 and message in r.json()["detail"], r.text


def test_only_this_workspaces_admin_changes_it(client, conn):
    import auth
    import members_store
    members_store.set_password("other-admin", "Other", auth.hash_password("correct-horse"),
                               "other-co", "admin")
    r = client.post("/api/auth/login", json={"username": "other-admin",
                                             "password": "correct-horse"})
    other = {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}
    path = f"/api/admin/sign-in/saml-connections/{conn['id']}"
    assert client.patch(path, headers=other, json={"enabled": False}).status_code == 404
    assert client.patch(f"/api/admin/sign-in/connections/{conn['id']}", headers=other,
                        json={"enabled": False}).status_code == 404


# ---- "Test sign-in" ----------------------------------------------------------------
def test_an_admin_can_try_saml_without_signing_anyone_in(client, admin, conn, idp):
    import members_store
    ticket_url = client.post(f"/api/admin/sign-in/connections/{conn['id']}/test",
                             headers=admin).json()
    location = client.get(ticket_url["url"]).headers["location"]
    request = idp.read_request(location)
    assert request.force_authn == "true"                  # a fresh login at the IdP
    answer = idp.answer(request, "trial@samlco.example")
    assert finish(client, post_answer(client, conn, answer)) == "/#sso-test"
    result = client.get(f"/api/admin/sign-in/tests/{ticket_url['ticket']}",
                        headers=admin).json()["result"]
    assert result["ok"] and result["email"] == "trial@samlco.example"
    assert result["sent"]["NameID"] == "trial@samlco.example"
    assert members_store.get("trial@samlco.example") is None


def test_a_failed_saml_test_says_why(client, admin, conn, idp):
    ticket_url = client.post(f"/api/admin/sign-in/connections/{conn['id']}/test",
                             headers=admin).json()
    location = client.get(ticket_url["url"]).headers["location"]
    relay = dict(urllib.parse.parse_qsl(location.split("?", 1)[1]))["RelayState"]
    unsigned = idp.answer(idp.read_request(location), "x@samlco.example", sign_assertion=False)
    r = client.post(f"/api/auth/saml/{conn['id']}/acs",
                    data={"SAMLResponse": unsigned, "RelayState": relay})
    assert r.headers["location"] == "/#sso-test"
    result = client.get(f"/api/admin/sign-in/tests/{ticket_url['ticket']}",
                        headers=admin).json()["result"]
    assert not result["ok"] and "signed" in result["detail"]


def test_a_saml_attribute_can_carry_the_groups(client, admin, conn, idp):
    r = client.put(f"/api/admin/sign-in/connections/{conn['id']}/roles", headers=admin,
                   json={"group_claim": "Role", "rules": [{"group": "hr-team", "role": "hr"}]})
    assert r.status_code == 200
    try:
        headers = sign_in(client, conn, idp, email="grouped@samlco.example",
                          attributes={"mail": ["grouped@samlco.example"],
                                      "Role": ["staff", "hr-team"]})
        assert client.get("/api/auth/me", headers=headers).json()["role"] == "hr"
    finally:
        client.put(f"/api/admin/sign-in/connections/{conn['id']}/roles", headers=admin,
                   json={"group_claim": None, "rules": []})
