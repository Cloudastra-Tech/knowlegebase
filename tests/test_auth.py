"""
Who gets in, and with which role — tested through the real API.

Google / Microsoft are the only thing faked: the callback gets the claims a
verified ID token would carry. Everything after that (sso.py's rules, the
one-time code, the session, the role checks) is the real code.
"""

import json
import os

import pytest

HR_EMAIL = "hr.person@cloudastra.in"
SSO_CONFIG = {"workspaces": {"default": {"domains": ["cloudastra.in", "cloudastra.co"],
                                         "hr": [HR_EMAIL.upper()]}}}
GOOGLE_HR = {"email": HR_EMAIL, "email_verified": True, "hd": "cloudastra.in"}
MS_ISS = "https://login.microsoftonline.com/tenant-id/v2.0"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("auth")
    with open(tmp / "sso.json", "w") as f:
        json.dump(SSO_CONFIG, f)
    os.environ.update(OPENAI_API_KEY="sk-test", APP_DB=str(tmp / "app.db"),
                      USERS_FILE=str(tmp / "users.json"), SSO_CONFIG=str(tmp / "sso.json"))
    from fastapi.testclient import TestClient
    import api
    import auth

    users = {"admin": {"name": "Admin", "role": "hr"},
             "staff": {"name": "Staff", "role": "public"},
             "legacy": {"name": "Legacy"},            # made before accounts had roles
             "keeper": {"name": "Keeper", "role": "hr"}}
    for u in users.values():
        u["password"] = auth.hash_password("correct-horse")
    auth.save_users(users)
    return TestClient(api.app, follow_redirects=False)


@pytest.fixture
def sso_config(env):
    """Rewrite config/sso.json for one test; the original comes back afterwards."""
    import sso

    def write(text):
        with open(sso.CONFIG_FILE, "w") as f:
            f.write(text)
    yield write
    write(json.dumps(SSO_CONFIG))


@pytest.fixture
def provider(monkeypatch):
    """Stand in for Google / Microsoft: the callback receives these claims."""
    import sso

    class FakeProvider:
        claims = {}

        async def authorize_access_token(self, request):
            return {"userinfo": self.claims}

    fake = FakeProvider()
    monkeypatch.setattr(sso, "client", lambda name: fake)
    return fake


def bearer(token, role="hr"):
    return {"Authorization": f"Bearer {token}", "X-Role": role}


def password_token(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": "correct-horse"})
    assert r.status_code == 200
    return r.json()["token"]


def sso_callback(client, provider, name, claims, query="code=x&state=y"):
    provider.claims = claims
    r = client.get(f"/api/auth/sso/{name}/callback?{query}")
    assert r.status_code in (302, 307)
    return r.headers["location"]


def sso_token(client, provider, name, claims):
    location = sso_callback(client, provider, name, claims)
    assert location.startswith("/#sso?code=")
    r = client.post("/api/auth/sso/exchange", json={"code": location.split("code=")[1]})
    assert r.status_code == 200
    return r.json()["token"]


@pytest.mark.parametrize("username, x_role, status", [
    ("admin", "hr", 200),
    ("admin", "public", 403),      # HR viewing the app as a public user
    ("staff", "hr", 403),          # the header must not grant HR
    ("legacy", "hr", 403),         # no role on file means public, not HR
])
def test_hr_screens_follow_the_account_role_not_the_header(env, username, x_role, status):
    token = password_token(env, username)
    assert env.get("/api/docs", headers=bearer(token, x_role)).status_code == status


@pytest.mark.parametrize("name, claims, role", [
    ("google", GOOGLE_HR, "hr"),
    ("google", {"email": "Someone@Cloudastra.in", "email_verified": True,
                "hd": "cloudastra.in"}, "public"),
    # hd is always the Workspace's PRIMARY domain, even for a secondary-domain user.
    ("google", {"email": "someone@cloudastra.co", "email_verified": True,
                "hd": "cloudastra.in"}, "public"),
    ("microsoft", {"preferred_username": "someone@cloudastra.co", "iss": MS_ISS,
                   "name": "Some One"}, "public"),
])
def test_sso_signs_in_to_the_workspace_with_the_configured_role(env, provider, name, claims, role):
    me = env.get("/api/auth/me", headers=bearer(sso_token(env, provider, name, claims))).json()
    assert (me["username"], me["workspace"], me["role"]) == (
        (claims.get("email") or claims["preferred_username"]).lower(), "default", role)


@pytest.mark.parametrize("name, claims, error", [
    # A personal Google account can carry a company address: no hd, no entry.
    ("google", {"email": "someone@cloudastra.in", "email_verified": True}, "not_workspace"),
    # Another organisation's Workspace can't vouch for our domain.
    ("google", {"email": "someone@cloudastra.in", "email_verified": True,
                "hd": "othercorp.com"}, "not_allowed"),
    ("google", {"email": "someone@cloudastra.in", "email_verified": False,
                "hd": "cloudastra.in"}, "unverified"),
    ("google", {"email": "someone@othercorp.com", "email_verified": True,
                "hd": "othercorp.com"}, "not_allowed"),
    ("microsoft", {"preferred_username": "someone@othercorp.com", "iss": MS_ISS}, "not_allowed"),
    # A guest's home provider (idp) differs from our tenant.
    ("microsoft", {"preferred_username": "someone@cloudastra.in", "iss": MS_ISS,
                   "idp": "https://sts.windows.net/other-tenant/"}, "not_workspace"),
    # Microsoft's `email` claim is unverified, so it alone is not enough.
    ("microsoft", {"email": "someone@cloudastra.in", "iss": MS_ISS}, "unverified"),
])
def test_sso_turns_away_accounts_it_cannot_trust(env, provider, name, claims, error):
    assert sso_callback(env, provider, name, claims) == f"/#login?error={error}"


@pytest.mark.parametrize("provider_error, shown", [
    ("access_denied", "cancelled"),
    ("invalid_client", "sso_failed"),      # a setup problem, not the user's choice
])
def test_provider_errors_reach_the_login_page(env, provider, provider_error, shown):
    location = sso_callback(env, provider, "google", GOOGLE_HR, query=f"error={provider_error}")
    assert location == f"/#login?error={shown}"


def test_sso_code_works_only_once(env, provider):
    code = sso_callback(env, provider, "google", GOOGLE_HR).split("code=")[1]
    assert env.post("/api/auth/sso/exchange", json={"code": code}).status_code == 200
    assert env.post("/api/auth/sso/exchange", json={"code": code}).status_code == 401


def test_sso_access_changes_apply_to_live_sessions(env, provider, sso_config):
    token = sso_token(env, provider, "google", GOOGLE_HR)
    assert env.get("/api/docs", headers=bearer(token)).status_code == 200
    sso_config(json.dumps({"workspaces": {"default": {"domains": ["cloudastra.in"], "hr": []}}}))
    assert env.get("/api/docs", headers=bearer(token)).status_code == 403    # HR taken away
    sso_config(json.dumps({"workspaces": {}}))
    assert env.get("/api/auth/me", headers=bearer(token)).status_code == 401  # removed


def test_password_role_changes_apply_to_live_sessions(env):
    import auth
    token = password_token(env, "keeper")
    auth._cli(["role", "keeper", "public"])
    try:
        assert env.get("/api/docs", headers=bearer(token)).status_code == 403
    finally:
        auth._cli(["role", "keeper", "hr"])


def test_a_broken_sso_config_fails_safely(env, provider, sso_config):
    token = sso_token(env, provider, "google", GOOGLE_HR)
    sso_config("{ not json")
    assert sso_callback(env, provider, "google", GOOGLE_HR) == "/#login?error=sso_failed"
    assert env.get("/api/auth/me", headers=bearer(token)).status_code == 503


def test_resetting_a_password_keeps_the_role(env, monkeypatch):
    import auth
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt="": "a-new-password")
    auth._cli(["add", "keeper"])                   # no --role: a password reset
    assert auth.authenticate("keeper", "a-new-password")["role"] == "hr"


def test_password_login_can_be_turned_off(env, monkeypatch):
    token = password_token(env, "admin")
    monkeypatch.setenv("PASSWORD_LOGIN", "off")
    assert env.get("/api/auth/providers").json()["password"] is False
    r = env.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    assert r.status_code == 403
    assert env.get("/api/auth/me", headers=bearer(token)).status_code == 401  # ends live sessions too
