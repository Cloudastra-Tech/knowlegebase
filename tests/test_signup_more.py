"""
Sign-up beyond Google Workspace (kb/services/signup.py, kb/services/m365.py;
platform.md PL-17), behind WORKSPACE_SIGNUP like Google's:

  * Microsoft 365: the founder's Entra tenant must have verified their email's
    domain, which Microsoft Graph's `organization` resource lists. Graph is
    faked with recorded answers (tests/fixtures/graph/) over an httpx
    MockTransport; Microsoft's sign-in itself with a stand-in client.
  * Email and DNS: a confirmation link by email, then a TXT record; no workspace
    exists until the record checks out. DNS and the mailer are faked.
"""

import json
import pathlib
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.responses import RedirectResponse

import billing_store
import members_store

GRAPH = pathlib.Path(__file__).parent / "fixtures" / "graph"


def browser():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def unique(name):
    return f"{name}-{uuid.uuid4().hex[:8]}.example"


def error_of(location, tab="signup"):
    assert location.startswith(f"/#{tab}?"), location
    return parse_qs(location.split("?", 1)[1])["error"][0]


@pytest.fixture
def signup_on(monkeypatch):
    monkeypatch.setenv("WORKSPACE_SIGNUP", "on")


@pytest.fixture
def microsoft(monkeypatch):
    """The multi-tenant app configured, Microsoft's sign-in stood in for, and Graph
    answering from the fixtures for `graph.domain` (and `graph.verified`, the
    domains its tenant verified; `graph.user_type`)."""
    from kb.services import m365

    class Graph:
        domain = "contoso.example"
        verified = None
        user_type = "Member"
        calls = []

    def answer(request):
        Graph.calls.append((request.url.path, request.headers["authorization"]))
        name = request.url.path.rsplit("/", 1)[1]
        text = (GRAPH / f"{name}.json").read_text().replace("__DOMAIN__", Graph.domain)
        data = json.loads(text.replace("__TENANT__", "contoso"))
        if name == "me":
            data["userType"] = Graph.user_type
        if name == "organization" and Graph.verified is not None:
            data["value"][0]["verifiedDomains"] = [{"name": d} for d in Graph.verified]
        return httpx.Response(200, json=data)

    class FakeMicrosoft:
        async def authorize_redirect(self, request, redirect_uri, **params):
            Graph.redirect = (redirect_uri, params)
            return RedirectResponse("https://login.microsoftonline.com/organizations/oauth2")

        async def authorize_access_token(self, request):
            return {"access_token": "graph-token", "token_type": "Bearer"}

    monkeypatch.setenv("M365_CLIENT_ID", "app-id")
    monkeypatch.setenv("M365_CLIENT_SECRET", "app-secret")
    monkeypatch.setattr(m365, "client", lambda: FakeMicrosoft())
    monkeypatch.setattr(m365, "TRANSPORT", httpx.MockTransport(answer))
    Graph.calls = []
    return Graph


def m365_sign_up(name, client=None):
    client = client or browser()
    start = client.get("/api/auth/signup/microsoft/start", params={"name": name})
    assert start.status_code in (302, 307), start.text
    if not start.headers["location"].startswith("https://login.microsoftonline.com"):
        return start.headers["location"]
    return client.get("/api/auth/m365/callback?code=x&state=y").headers["location"]


def exchange(client, location):
    token = client.post("/api/auth/sso/exchange",
                        json={"code": location.split("code=")[1]}).json()["token"]
    return {"Authorization": f"Bearer {token}", "X-Role": "hr"}


# ---- Microsoft 365 -----------------------------------------------------------------------
def test_a_founder_creates_a_workspace_with_microsoft_365(microsoft, signup_on, monkeypatch):
    monkeypatch.setenv("BILLING", "on")
    microsoft.domain = unique("contoso")
    client = browser()
    assert "microsoft365" in client.get("/api/auth/providers").json()["signup_methods"]
    location = m365_sign_up("Contoso", client)
    assert location.startswith("/#sso?welcome=1&code="), location
    redirect_uri, params = microsoft.redirect
    assert redirect_uri.endswith("/api/auth/m365/callback") and params["prompt"] == "select_account"
    assert [p for p, _ in microsoft.calls] == ["/v1.0/me", "/v1.0/organization"]
    assert microsoft.calls[0][1] == "Bearer graph-token"
    me = client.get("/api/auth/me", headers=exchange(client, location)).json()
    # The UPN as Graph spells it, lower-cased; the workspace's domain is verified.
    assert me["username"] == f"megan.bowen@{microsoft.domain}" and me["role"] == "admin"
    assert me["provider"] == "microsoft365"
    ws = me["workspace"]
    assert members_store.verified_domains(ws) == {microsoft.domain}
    assert billing_store.is_owner(ws, me["username"])
    assert billing_store.subscription(ws)["status"] == "trialing"        # BILLING=on
    # Colleagues find it, and sign in with Microsoft 365 (not a second sign-up).
    found = client.post("/api/auth/sso/discover", json={"email": f"pat@{microsoft.domain}"}
                        ).json()
    assert found["workspace"]["microsoft365"] is True
    assert client.get("/api/auth/m365/start", params={"login_hint": f"pat@{microsoft.domain}"}
                      ).status_code in (302, 307)
    assert microsoft.redirect[1]["login_hint"] == f"pat@{microsoft.domain}"
    location = client.get("/api/auth/m365/callback?code=x&state=y").headers["location"]
    assert location.startswith("/#sso?code="), location
    # Their session is checked again on every request, like any single sign-on.
    assert client.get("/api/auth/me", headers=exchange(client, location)).json()[
        "workspace"] == ws


def test_only_a_domain_the_tenant_verified_can_sign_up(microsoft, signup_on):
    # The UPN is on a domain this tenant hasn't verified (or doesn't list).
    microsoft.domain = unique("unverified")
    microsoft.verified = ["contoso.onmicrosoft.com"]
    assert error_of(m365_sign_up("Unverified")) == "not_workspace"
    assert members_store.workspace_for_domain(microsoft.domain) is None
    # A guest in the tenant proves nothing about its domains.
    microsoft.verified, microsoft.user_type = None, "Guest"
    assert error_of(m365_sign_up("Guest")) == "not_workspace"


def test_a_domain_with_a_workspace_cant_make_another_with_microsoft(microsoft, signup_on):
    microsoft.domain = unique("taken")
    assert m365_sign_up("First").startswith("/#sso?")
    assert error_of(m365_sign_up("Second")) in ("workspace_exists", "account_exists")


def test_microsoft_365_sign_up_follows_the_server_switches(microsoft, monkeypatch):
    client = browser()
    assert error_of(m365_sign_up("Off", client), tab="login") == "signup_off"
    monkeypatch.setenv("WORKSPACE_SIGNUP", "on")
    monkeypatch.setenv("WORKSPACE_SIGNUP_METHODS", "google,email")
    assert error_of(m365_sign_up("Narrowed", client), tab="login") == "signup_off"
    assert client.get("/api/auth/providers").json()["signup_methods"] == ["email"]
    # An unknown domain signing in with it is just refused.
    monkeypatch.delenv("WORKSPACE_SIGNUP_METHODS")
    microsoft.domain = unique("nobody")
    client.get("/api/auth/m365/start")
    location = client.get("/api/auth/m365/callback?code=x&state=y").headers["location"]
    assert error_of(location, tab="login") == "not_allowed"


# ---- Email and DNS -----------------------------------------------------------------------
@pytest.fixture
def mail_and_dns(monkeypatch):
    """Emails kept (`sent`), and DNS that has whatever TXT records `txt` holds."""
    import mailer
    import sso_setup

    class Fake:
        sent = []
        txt = {}

    monkeypatch.setattr(mailer, "send", lambda to, template, **v: Fake.sent.append(
        (to, template, v)))
    monkeypatch.setattr(sso_setup, "txt_values", lambda name: Fake.txt.get(name, []))
    Fake.sent, Fake.txt = [], {}
    return Fake


def start_email(client, name, email):
    return client.post("/api/auth/signup/email", json={"name": name, "email": email})


def test_a_founder_signs_up_with_email_and_a_dns_record(mail_and_dns, signup_on):
    client = browser()
    domain = unique("dnsco")
    r = start_email(client, " DNS  Co ", f"Founder@{domain}")
    assert r.status_code == 200, r.text
    to, template, values = mail_and_dns.sent[-1]
    assert (to, template, values["workspace_name"]) == (f"founder@{domain}", "signup_verify",
                                                        "DNS Co")
    token = values["link"].split("token=")[1]
    assert values["link"].endswith(f"/signup/verify?token={token}")
    # Pending: there is no workspace yet, so nobody can sign in to one.
    assert members_store.workspace_for_domain(domain) is None
    assert client.post("/api/auth/signup/email/complete",
                       json={"token": token, "password": "long-enough"}).status_code == 400

    view = client.post("/api/auth/signup/email/confirm", json={"token": token}).json()
    assert view["txt_name"] == f"_knowledge-assistant.{domain}"
    assert view["txt_value"].startswith("kb-verify=") and view["domain"] == domain
    r = client.post("/api/auth/signup/email/complete",
                    json={"token": token, "password": "long-enough"})
    assert r.status_code == 400 and "No TXT record" in r.json()["detail"]
    assert members_store.workspace_for_domain(domain) is None
    assert client.post("/api/auth/signup/email/complete",
                       json={"token": token, "password": "short"}).status_code == 400

    mail_and_dns.txt[view["txt_name"]] = ["v=spf1 -all", view["txt_value"]]
    r = client.post("/api/auth/signup/email/complete",
                    json={"token": token, "password": "long-enough"})
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    ws = members_store.workspace_for_domain(domain)
    assert (user["workspace"], user["role"], user["username"]) == (ws, "admin",
                                                                   f"founder@{domain}")
    h = {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}
    assert client.get("/api/auth/me", headers=h).json()["workspace"] == ws
    assert billing_store.is_owner(ws, f"founder@{domain}")
    # The link is used up; the founder signs in with their password from now on.
    assert client.post("/api/auth/signup/email/confirm", json={"token": token}).status_code == 404
    assert client.post("/api/auth/login", json={"username": f"founder@{domain}",
                                                "password": "long-enough"}).status_code == 200


def test_email_sign_up_refuses_what_proves_nothing(mail_and_dns, signup_on):
    client = browser()
    r = start_email(client, "Gmail Co", "fran@gmail.com")
    assert r.status_code == 400 and "work email" in r.json()["detail"]
    members_store.create_workspace("Has One", (taken := unique("hasone")), f"boss@{taken}", "B")
    assert start_email(client, "Again", f"fran@{taken}").status_code == 409
    assert start_email(client, "", f"fran@{unique('noname')}").status_code == 400
    assert client.post("/api/auth/signup/email/confirm", json={"token": "made-up"}
                       ).status_code == 404
    # A few links an hour to one address, no more.
    email = f"spam@{unique('spam')}"
    for _ in range(3):
        assert start_email(client, "Spam", email).status_code == 200
    assert start_email(client, "Spam", email).status_code == 429
    assert len(mail_and_dns.sent) == 3


def test_email_sign_up_is_off_with_sign_up(mail_and_dns, monkeypatch):
    client = browser()
    assert start_email(client, "Off", f"fran@{unique('off')}").status_code == 403
    monkeypatch.setenv("WORKSPACE_SIGNUP", "on")
    monkeypatch.setenv("PASSWORD_LOGIN", "off")          # the founder couldn't sign in again
    assert start_email(client, "Off", f"fran@{unique('off')}").status_code == 403
    assert mail_and_dns.sent == []


def test_email_sign_up_uses_up_an_invite_code(mail_and_dns, signup_on, monkeypatch):
    monkeypatch.setenv("WORKSPACE_SIGNUP_CODES", "dns-pilot")
    client = browser()
    domain = unique("coded")
    assert client.post("/api/auth/signup/email", json={"name": "Coded", "email": f"f@{domain}",
                                                       "code": "wrong"}).status_code == 400
    assert client.post("/api/auth/signup/email", json={"name": "Coded", "email": f"f@{domain}",
                                                       "code": "dns-pilot"}).status_code == 200
    token = mail_and_dns.sent[-1][2]["link"].split("token=")[1]
    view = client.post("/api/auth/signup/email/confirm", json={"token": token}).json()
    mail_and_dns.txt[view["txt_name"]] = [view["txt_value"]]
    assert client.post("/api/auth/signup/email/complete",
                       json={"token": token, "password": "long-enough"}).status_code == 200
    assert members_store.signup_code_used("dns-pilot")
