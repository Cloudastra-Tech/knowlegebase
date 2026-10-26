"""
SAML sign-in through a workspace's own identity provider, tested through the
API against a REAL SAML IdP: pysaml2's own IdP, with its own keys, signs every
answer here. Nothing in the signature, audience, timing or replay checks is
faked. Needs the xmlsec1 program (CI installs it; locally set XMLSEC_BINARY).
"""

import base64
import os
import re
import time
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

    def __init__(self, folder, want_signed_requests=True, key=None, lifetime=None,
                 entity=IDP_ENTITY):
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
            "entityid": entity, "xmlsec_binary": saml_sp.xmlsec_binary(),
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


def failure(client, admin, location):
    """(error the person sees, code the admin's log has under the same reference)."""
    q = dict(urllib.parse.parse_qsl(location.split("?", 1)[1]))
    events = client.get("/api/admin/sign-in", headers=admin).json()["events"]
    return q["error"], next((e["reason"] for e in events
                             if q.get("ref") and e["ref"] == q["ref"]), None)


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
    assert {"id": conn["id"], "name": "SamlCo SSO", "url": f"/api/auth/saml/{conn['id']}/start",
            "kind": None} in found["connections"]           # no logo for an unknown IdP


def test_the_email_can_come_from_the_name_id_alone(client, conn, idp):
    headers = sign_in(client, conn, idp, email="nameid@samlco.example", attributes={})
    assert client.get("/api/auth/me", headers=headers).json()["username"] == (
        "nameid@samlco.example")


# ---- Answers that must not sign anyone in ----------------------------------------
def test_an_unsigned_answer_is_refused(client, admin, conn, idp):
    assert failure(client, admin, sign_in(client, conn, idp, sign_assertion=False)) == (
        "sso_failed", "saml_unsigned")


def test_an_answer_signed_with_another_key_is_refused(client, admin, conn, idp, tmp_path):
    impostor = TestIdP(str(tmp_path / "impostor"))       # same entity ID, its own key
    impostor.trust(client.get(f"/api/auth/saml/{conn['id']}/metadata").text)
    request = start(client, conn, idp)
    forged = impostor.answer(request, "pat@samlco.example")
    # (what a rotated certificate looks like until the metadata is refreshed)
    assert failure(client, admin, finish(client, post_answer(client, conn, forged))) == (
        "sso_failed", "saml_signature")


def test_an_answer_can_be_used_only_once(client, admin, conn, idp):
    request = start(client, conn, idp)
    answer = idp.answer(request, "pat@samlco.example")
    assert isinstance(finish(client, post_answer(client, conn, answer)), dict)
    assert failure(client, admin, finish(client, post_answer(client, conn, answer))) == (
        "sso_failed", "saml_unknown_request")       # its request was already answered


def test_an_answer_nobody_asked_for_is_refused(client, admin, conn, idp):
    request = start(client, conn, idp)
    unsolicited = idp.answer(request, "pat@samlco.example", in_response_to=None)
    assert failure(client, admin, finish(client, post_answer(client, conn, unsolicited))) == (
        "sso_failed", "saml_unsolicited")


def test_an_expired_answer_is_refused(client, admin, conn, idp, tmp_path):
    # The real IdP's key, but its assertions ran out 10 minutes ago (more than
    # the 2 minutes of clock difference allowed).
    stale = TestIdP(str(tmp_path / "stale"), key=idp.key, lifetime={"minutes": -10})
    stale.trust(client.get(f"/api/auth/saml/{conn['id']}/metadata").text)
    assert failure(client, admin, sign_in(client, conn, stale)) == ("sso_failed",
                                                                    "saml_expired")


def test_an_answer_meant_for_another_connection_is_refused(client, admin, conn, idp):
    other = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                        json={"label": "Other", "metadata_xml": idp.metadata()}).json()
    try:
        request = start(client, conn, idp)                # asked for by `conn`...
        answer = idp.answer(request, "pat@samlco.example")
        location = post_answer(client, other, answer)     # ...posted to `other`
        assert failure(client, admin, finish(client, location)) == (
            "sso_failed", "saml_unknown_request")
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
        assert failure(client, admin, finish(client, post_answer(client, other, answer))) == (
            "sso_failed", "saml_audience")
    finally:
        client.delete(f"/api/admin/sign-in/connections/{other['id']}", headers=admin)


def test_a_wrapped_signature_doesnt_smuggle_in_another_user(client, admin, conn, idp):
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
    assert failure(client, admin, finish(client, location))[0] == "sso_failed"


def test_an_answer_only_signs_in_the_browser_that_asked(client, conn, idp):
    """Someone can't start a sign-in, get their own answer, and make another
    person's browser post it (logging them in to the wrong account)."""
    from fastapi.testclient import TestClient
    import api
    request = start(client, conn, idp)                    # the attacker's browser
    location = post_answer(client, conn, idp.answer(request, "attacker@samlco.example"))
    victim = TestClient(api.app, follow_redirects=False)  # no cookie from /start
    assert victim.get(location).headers["location"] == "/#login?error=sso_failed"   # no ref


def test_an_idp_can_only_vouch_for_its_workspaces_domains(client, admin, conn, idp):
    import members_store
    members_store.add_verified_domain("elsewhere", "elsewhere.example")
    assert failure(client, admin, sign_in(client, conn, idp, email="boss@elsewhere.example")) == (
        "not_allowed", "not_allowed")


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
    assert (result["reason"], result["reason_text"]) == (
        "saml_unsigned", "Neither the response nor the assertion is signed")


def test_a_rotated_certificate_shows_until_the_metadata_is_refreshed(client, admin, idp,
                                                                     monkeypatch, tmp_path):
    import sso_setup
    served = {"xml": idp.metadata()}             # what the IdP's metadata URL serves
    monkeypatch.setattr(sso_setup, "_get", lambda url, accept, **kw: served["xml"].encode())
    c = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                    json={"label": "Rotating", "metadata_url": "https://idp.samlco.example/md"}
                    ).json()
    try:
        sp_metadata = client.get(f"/api/auth/saml/{c['id']}/metadata").text
        rotated = TestIdP(str(tmp_path / "rotated"))     # same IdP, a new signing key
        rotated.trust(sp_metadata)
        for _ in range(2):
            assert failure(client, admin, sign_in(client, c, rotated)) == (
                "sso_failed", "saml_signature")
        settings = client.get("/api/admin/sign-in", headers=admin).json()
        health = next(x["health"] for x in settings["connections"] if x["id"] == c["id"])
        assert (health["status"], health["code"], health["count"]) == (
            "failing", "saml_signature", 2)
        assert "refresh its metadata" in health["reason"]
        # Refresh: the screen sends the saved metadata URL again.
        served["xml"] = rotated.metadata()
        r = client.patch(f"/api/admin/sign-in/saml-connections/{c['id']}", headers=admin,
                         json={"metadata_url": c["metadata_url"]})
        assert r.status_code == 200 and r.json()["health"]["status"] == "failing"
        assert isinstance(sign_in(client, c, rotated), dict)       # signed in
        settings = client.get("/api/admin/sign-in", headers=admin).json()
        assert next(x["health"] for x in settings["connections"]
                    if x["id"] == c["id"]) == {"status": "ok"}
    finally:
        client.delete(f"/api/admin/sign-in/connections/{c['id']}", headers=admin)


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


def test_an_expired_saml_test_never_turns_into_a_real_sign_in(client, admin, conn, idp):
    from kb import state
    import members_store
    started = client.post(f"/api/admin/sign-in/connections/{conn['id']}/test",
                          headers=admin).json()
    request = idp.read_request(client.get(started["url"]).headers["location"])
    state.sso_tests[started["ticket"]]["expires"] = 0
    location = post_answer(client, conn, idp.answer(request, "late@samlco.example"))
    assert finish(client, location) == "/#sso-test"
    assert members_store.get("late@samlco.example") is None


def test_the_sp_metadata_is_built_once_per_version(client, conn, monkeypatch):
    import saml_sp
    calls = []
    real = saml_sp.sp_metadata
    monkeypatch.setattr(saml_sp, "sp_metadata", lambda *a: calls.append(1) or real(*a))
    first = client.get(f"/api/auth/saml/{conn['id']}/metadata").text
    assert client.get(f"/api/auth/saml/{conn['id']}/metadata").text == first
    assert len(calls) <= 1


# ---- Metadata from a URL, fetched again: the IdP rolls over to a new key ------------
class MetadataURL:
    """The IdP's metadata URL: answers with `xml`, and counts the fetches."""

    def __init__(self, xml):
        self.xml, self.fetches = xml, 0

    def get(self, url, accept, **kwargs):
        self.fetches += 1
        return self.xml.encode()


@pytest.fixture
def from_url(client, admin, monkeypatch, tmp_path):
    """A SAML provider set up from its IdP's ("idp") metadata URL ("url")."""
    import sso_setup
    idp = TestIdP(str(tmp_path / "current"))
    url = MetadataURL(idp.metadata())
    monkeypatch.setattr(sso_setup, "_get", url.get)
    r = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                    json={"label": "Rolls over",
                          "metadata_url": "https://idp.samlco.example/metadata"})
    assert r.status_code == 200, r.text
    c = {**r.json(), "url": url, "idp": idp}
    idp.trust(client.get(f"/api/auth/saml/{c['id']}/metadata").text)
    yield c
    client.delete(f"/api/admin/sign-in/connections/{c['id']}", headers=admin)


def new_key(tmp_path, client, conn, name="next", **kwargs):
    """The IdP with a new signing key (or another IdP), trusting `conn`."""
    other = TestIdP(str(tmp_path / name), **kwargs)
    other.trust(client.get(f"/api/auth/saml/{conn['id']}/metadata").text)
    return other


def provider(client, admin, cid):
    return next(c for c in client.get("/api/admin/sign-in", headers=admin).json()["connections"]
                if c["id"] == cid)


def events(client, admin, cid):
    return [(e["outcome"], e["reason"])
            for e in client.get("/api/admin/sign-in", headers=admin).json()["events"]
            if e["connection_id"] == cid]


def a_day_later():
    from kb.services import saml_refresh
    return time.time() + saml_refresh.SAML_REFRESH_EVERY + 60


def test_a_new_key_is_fetched_when_a_signature_fails_and_the_sign_in_succeeds(
        client, admin, from_url, tmp_path):
    rotated = new_key(tmp_path, client, from_url)
    from_url["url"].xml = rotated.metadata()             # the IdP publishes its new key
    headers = sign_in(client, from_url, rotated)
    assert client.get("/api/auth/me", headers=headers).json()["connection"] == from_url["id"]
    assert from_url["url"].fetches == 2                  # when it was added, and now
    assert ("updated", "Certificate updated automatically") in events(client, admin,
                                                                      from_url["id"])


def test_an_answer_is_still_used_only_once_after_a_refresh(client, from_url, tmp_path):
    rotated = new_key(tmp_path, client, from_url)
    from_url["url"].xml = rotated.metadata()
    answer = rotated.answer(start(client, from_url, rotated), "pat@samlco.example")
    assert isinstance(finish(client, post_answer(client, from_url, answer)), dict)
    assert finish(client, post_answer(client, from_url, answer)).startswith("/#login?error=sso_failed&ref=")


def test_a_failing_signature_fetches_the_metadata_at_most_every_few_minutes(
        client, from_url, tmp_path):
    impostor = new_key(tmp_path, client, from_url, "impostor")   # a key it never lists
    for _ in range(3):
        assert sign_in(client, from_url, impostor).startswith("/#login?error=sso_failed&ref=")
    assert from_url["url"].fetches == 2                  # when it was added, then once


def test_the_schedule_fetches_metadata_once_its_due(client, admin, from_url, tmp_path):
    from kb.services import saml_refresh
    rotated = new_key(tmp_path, client, from_url)
    from_url["url"].xml = rotated.metadata()
    saml_refresh.refresh_due_saml_metadata()       # just added: not due yet
    assert from_url["url"].fetches == 1
    saml_refresh.refresh_due_saml_metadata(now=a_day_later())
    assert from_url["url"].fetches == 2
    assert ("updated", "Certificate updated automatically") in events(client, admin,
                                                                      from_url["id"])
    assert isinstance(sign_in(client, from_url, rotated), dict)
    assert from_url["url"].fetches == 2                  # it already had the new key


def test_a_new_entity_id_is_held_back_for_review(client, admin, from_url, tmp_path):
    from kb.services import saml_refresh
    other = TestIdP(str(tmp_path / "other"), entity="https://elsewhere.example/saml")
    from_url["url"].xml = other.metadata()
    saml_refresh.refresh_due_saml_metadata(now=a_day_later())
    p = provider(client, admin, from_url["id"])
    assert p["needs_review"] and p["issuer"] == IDP_ENTITY       # nothing applied
    [(_, reason)] = [e for e in events(client, admin, from_url["id"]) if e[0] == "attention"]
    assert "entity ID changed" in reason and "elsewhere" not in reason
    assert isinstance(sign_in(client, from_url, from_url["idp"]), dict)
    # The admin loads it again: their decision, so it applies.
    r = client.patch(f"/api/admin/sign-in/saml-connections/{from_url['id']}", headers=admin,
                     json={"metadata_url": "https://idp.samlco.example/metadata"})
    assert (r.json()["issuer"], r.json()["needs_review"]) == ("https://elsewhere.example/saml",
                                                              False)


def test_a_failed_refresh_is_logged_once_never_verbatim_and_keeps_the_metadata(
        client, admin, from_url, monkeypatch):
    from kb.services import saml_refresh
    monkeypatch.setattr(saml_refresh, "SAML_REFRESH_GAP", 0)
    from_url["url"].xml = "<html>Down for maintenance: call the helpdesk at 555</html>"
    saml_refresh.refresh_due_saml_metadata(now=a_day_later())
    saml_refresh.refresh_due_saml_metadata(now=a_day_later() + saml_refresh.SAML_REFRESH_RETRY)
    assert from_url["url"].fetches == 3
    [(_, reason)] = [e for e in events(client, admin, from_url["id"]) if e[0] == "attention"]
    assert reason.startswith("Metadata refresh failed") and "helpdesk" not in reason
    assert isinstance(sign_in(client, from_url, from_url["idp"]), dict)


def test_every_signing_certificate_in_the_metadata_verifies(client, admin, idp, tmp_path):
    following = TestIdP(str(tmp_path / "following"))     # the key it will roll over to
    second = re.search(r"<ns0:KeyDescriptor.*?</ns0:KeyDescriptor>", following.metadata(),
                       re.S).group(0)
    xml = idp.metadata().replace("</ns0:KeyDescriptor>", "</ns0:KeyDescriptor>" + second, 1)
    assert saml_sp.read_idp_metadata(xml)["certificates"] == 2
    c = client.post("/api/admin/sign-in/saml-connections", headers=admin,
                    json={"label": "Two keys", "metadata_xml": xml}).json()
    try:
        current = TestIdP(str(tmp_path / "current"), key=idp.key)
        for signer in (current, following):
            signer.trust(client.get(f"/api/auth/saml/{c['id']}/metadata").text)
            assert isinstance(sign_in(client, c, signer), dict)
    finally:
        client.delete(f"/api/admin/sign-in/connections/{c['id']}", headers=admin)


def test_a_pasted_providers_certificate_expiry_is_shown(client, admin, conn, idp):
    from cryptography import x509
    p = provider(client, admin, conn["id"])
    cert = x509.load_pem_x509_certificate(idp.key[1].encode())
    assert p["cert_expires_at"] == cert.not_valid_after_utc.timestamp()
    assert (p["metadata_refreshed_at"], p["needs_review"]) == (None, False)


def test_the_metadata_can_ask_to_be_fetched_sooner_but_not_constantly(idp):
    from kb.services import saml_refresh

    def with_cache(duration):
        return idp.metadata().replace(" entityID=", f' cacheDuration="{duration}" entityID=', 1)
    assert saml_sp.read_idp_metadata(with_cache("PT2H"))["refresh_within"] == 2 * 60 * 60
    assert saml_sp.read_idp_metadata(idp.metadata())["refresh_within"] is None
    wait = saml_refresh.next_refresh(saml_sp.read_idp_metadata(with_cache("PT1S"))) - time.time()
    assert wait >= saml_refresh.SAML_REFRESH_CHECK_EVERY - 1
