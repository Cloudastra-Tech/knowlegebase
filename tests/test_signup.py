"""
A new company creates its workspace (sign-up), and the sign-in page's route
lookup — tested through the real API, with only Google faked: its callback gets
the claims a verified ID token would carry.
"""

import uuid
from urllib.parse import parse_qs

import pytest
from fastapi.responses import RedirectResponse


@pytest.fixture
def google(monkeypatch):
    """Google configured on the server and faked: the callback receives `claims`."""
    import sso

    class FakeGoogle:
        claims = {}

        async def authorize_redirect(self, request, redirect_uri, **params):
            return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth")

        async def authorize_access_token(self, request):
            return {"userinfo": self.claims}

    fake = FakeGoogle()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(sso, "client", lambda name: fake)
    return fake


@pytest.fixture
def signup_on(monkeypatch):
    monkeypatch.setenv("WORKSPACE_SIGNUP", "on")


def browser():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def sign_up(google, name, claims, client=None):
    """A founder names the workspace and comes back from Google: where they land."""
    client = client or browser()
    start = client.get("/api/auth/signup/google/start", params={"name": name})
    assert start.status_code in (302, 307), start.text
    if not start.headers["location"].startswith("https://accounts.google.com"):
        return start.headers["location"]
    google.claims = claims
    return client.get("/api/auth/sso/google/callback?code=x&state=y").headers["location"]


def error_of(location, tab="signup"):
    assert location.startswith(f"/#{tab}?"), location
    return parse_qs(location.split("?", 1)[1])["error"][0]


def unique(name):
    """A domain no other test (they share one database) has touched."""
    return f"{name}-{uuid.uuid4().hex[:8]}.example"


def founder(domain, local="founder"):
    return {"email": f"{local}@{domain}", "email_verified": True, "hd": domain,
            "name": "Fran Founder"}


def test_sign_up_is_off_unless_the_server_turns_it_on(google):
    client = browser()
    assert client.get("/api/auth/providers").json()["signup"] is False
    location = client.get("/api/auth/signup/google/start", params={"name": "Acme"}).headers["location"]
    assert error_of(location, tab="login") == "signup_off"


def test_a_founder_creates_a_workspace_with_google_workspace(google, signup_on):
    client = browser()
    assert client.get("/api/auth/providers").json()["signup"] is True
    location = sign_up(google, "  Signup  Corp ", founder("signup-corp.example"), client)
    assert location.startswith("/#sso?welcome=1&code=")
    token = client.post("/api/auth/sso/exchange",
                        json={"code": location.split("code=")[1]}).json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}",
                                             "X-Role": "hr"}).json()
    assert (me["username"], me["workspace"], me["role"]) == (
        "founder@signup-corp.example", "signup-corp", "admin")
    # The domain is verified for the new workspace: colleagues are found by it.
    # The lookup never names the workspace (anyone can ask about any domain).
    found = client.post("/api/auth/sso/discover",
                        json={"email": "colleague@signup-corp.example"}).json()
    assert found["workspace"]["google"] is True and "name" not in found["workspace"]
    # A colleague signing in with Google joins it as a regular member.
    google.claims = founder("signup-corp.example", "colleague")
    colleague = client.get("/api/auth/sso/google/callback?code=x&state=y").headers["location"]
    assert colleague.startswith("/#sso?code=")          # a normal sign-in, not a second sign-up


def test_a_domain_that_already_has_a_workspace_cant_make_another(google, signup_on):
    taken = unique("taken")
    assert sign_up(google, "First", founder(taken)).startswith("/#sso?")
    assert error_of(sign_up(google, "Second", founder(taken, "other"))) == "workspace_exists"


def test_only_a_google_workspace_account_on_its_own_domain_can_sign_up(google, signup_on):
    personal = {"email": "fran@gmail.com", "email_verified": True}
    assert error_of(sign_up(google, "Gmail Co", personal)) == "not_workspace"
    unverified = {**founder("unverified.example"), "email_verified": False}
    assert error_of(sign_up(google, "Unverified", unverified)) == "unverified"
    # An address on a secondary domain doesn't prove the primary one is theirs.
    alias = {"email": "fran@alias.example", "email_verified": True, "hd": "primary.example"}
    assert error_of(sign_up(google, "Alias", alias)) == "signup_domain"


def test_someone_who_already_has_an_account_cant_found_a_workspace(google, signup_on):
    import members_store
    elsewhere = unique("elsewhere")
    members_store.set_role(f"member@{elsewhere}", "public", "default")
    assert error_of(sign_up(google, "Elsewhere", founder(elsewhere, "member"))) == "account_exists"


def test_a_workspace_needs_a_name(google, signup_on):
    location = browser().get("/api/auth/signup/google/start", params={"name": "   "}).headers[
        "location"]
    assert location == "/#signup?error=name"


def test_a_normal_google_sign_in_never_turns_into_a_sign_up(google, signup_on):
    client = browser()
    assert client.get("/api/auth/signup/google/start",
                      params={"name": "Abandoned"}).status_code in (302, 307)
    # They gave up, then signed in normally: the half-finished sign-up is dropped.
    client.get("/api/auth/sso/google/start")
    google.claims = founder("abandoned.example")
    location = client.get("/api/auth/sso/google/callback?code=x&state=y").headers["location"]
    assert error_of(location, tab="login") == "not_allowed"
    found = client.post("/api/auth/sso/discover", json={"email": "x@abandoned.example"}).json()
    assert found["workspace"] is None


def test_the_route_lookup_says_how_a_workspace_signs_in(google, signup_on):
    import members_store
    members_store.create_workspace("Route Co", "route.example", "boss@route.example", "Boss")
    client = browser()
    found = client.post("/api/auth/sso/discover", json={"email": "pat@route.example"}).json()
    assert found["workspace"] == {"google": True, "password": True, "roles_from": None}
    members_store.set_requires_sso("route-co", True)
    found = client.post("/api/auth/sso/discover", json={"email": "pat@route.example"}).json()
    assert found["workspace"]["password"] is False
    # An unknown domain: no workspace, and whether one can be created here.
    unknown = client.post("/api/auth/sso/discover", json={"email": "pat@nobody.example"}).json()
    assert (unknown["workspace"], unknown["signup"]) == (None, True)


def test_the_route_lookup_names_a_known_providers_kind(google, signup_on):
    import members_store
    import sso_setup
    from tests.test_sign_in_settings import DOC
    domain = unique("kind")
    slug = members_store.create_workspace("Kind Co", domain, f"boss@{domain}", "Boss")
    members_store.create_connection(
        slug, "oidc", "Google Workspace", "https://accounts.google.com", "kb",
        {"client_secret": "s"}, {k: DOC[k] for k in sso_setup.METADATA_KEYS if k in DOC}, "test")
    found = browser().post("/api/auth/sso/discover", json={"email": f"pat@{domain}"}).json()
    assert [c["kind"] for c in found["connections"]] == ["google"]


@pytest.fixture
def invite_only(monkeypatch, signup_on):
    monkeypatch.setenv("WORKSPACE_SIGNUP_CODES", "pilot-one, pilot-two")


def test_with_invite_codes_sign_up_needs_one_and_each_makes_one_workspace(google, invite_only):
    client = browser()
    assert client.get("/api/auth/providers").json()["signup_invite"] is True
    no_code = client.get("/api/auth/signup/google/start", params={"name": "Nope"})
    assert no_code.headers["location"] == "/#signup?error=invite"
    wrong = client.get("/api/auth/signup/google/start", params={"name": "Nope", "code": "guess"})
    assert wrong.headers["location"] == "/#signup?error=invite"

    def with_code(name, domain, code):
        c = browser()
        start = c.get("/api/auth/signup/google/start", params={"name": name, "code": code})
        if not start.headers["location"].startswith("https://accounts.google.com"):
            return start.headers["location"]
        google.claims = founder(domain)
        return c.get("/api/auth/sso/google/callback?code=x&state=y").headers["location"]

    assert with_code("Pilot A", unique("pilot-a"), "pilot-one").startswith("/#sso?welcome=1&code=")
    # Used up: the same code can't make a second workspace.
    assert with_code("Pilot B", unique("pilot-b"), "pilot-one") == "/#signup?error=invite"
    assert with_code("Pilot C", unique("pilot-c"), "pilot-two").startswith("/#sso?welcome=1&code=")


def test_two_founders_racing_on_one_code_make_one_workspace(google, invite_only):
    import members_store
    members_store.create_workspace("Race A", unique("race-a"), "a@race-a.example", "A",
                                   code="pilot-two-race")
    with pytest.raises(members_store.WorkspaceTaken) as e:
        members_store.create_workspace("Race B", unique("race-b"), "b@race-b.example", "B",
                                       code="pilot-two-race")
    assert e.value.reason == "invite"
