"""
GitHub (a GitHub App) against recorded HTTP (tests/fixtures/connectors/github/,
written from the shapes in GitHub's REST OpenAPI description; an unrecorded
request fails the test), through the real framework (kb/services/sync.py):
the install-and-authorize connect flow and its spoofed-installation check,
the app JWT and installation-token minting, the repo picker with per-repo
code, docs-first file selection and size caps, the tree listing, the compare
cursor, deletes behind the mass-delete guard, acl capture, GitHub's 403 / 429
rate limits and typed errors.
"""

import base64
import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

API = "https://api.github.com"
H, A = "acme/handbook", "acme/api"


@pytest.fixture
def app(monkeypatch):
    """A GitHub App's settings, with a private key made for the test (none is committed)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    for k, v in {"GITHUB_APP_ID": "42", "GITHUB_APP_SLUG": "kb-app",
                 "GITHUB_APP_CLIENT_ID": "Iv1.test", "GITHUB_APP_CLIENT_SECRET": "test-secret",
                 "GITHUB_APP_PRIVATE_KEY": pem.replace("\n", "\\n")}.items():
        monkeypatch.setenv(k, v)
    return key


def connect(env, repos=((A, True), (H, False)), **settings):
    import integrations_store
    integrations_store.connect(env.ws, "github", "acme (Organization)",
                               {"installation_id": "111"}, "admin")
    integrations_store.update(env.ws, "github", settings={
        "installation_id": "111",
        "repos": [{"id": r, "name": r, "kind": "repo", "code": code} for r, code in repos],
        **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "github", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "github")


def installations(rec, *pairs):
    rec.add("POST", "https://github.com/login/oauth/access_token",
            json_body={"access_token": "ghu_user", "token_type": "bearer", "expires_in": 28800,
                       "refresh_token": "ghr_user", "scope": ""})
    rec.add("GET", f"{API}/user/installations", req_headers={"Authorization": "Bearer ghu_user"},
            json_body={"total_count": len(pairs), "installations": [
                {"id": iid, "app_id": app_id, "account": {"login": login, "type": "Organization"},
                 "repository_selection": "selected"} for iid, app_id, login in pairs]})


# ---- connecting ------------------------------------------------------------------------
def test_connect_installs_the_app_and_checks_the_installation_is_the_users(env, monkeypatch,
                                                                           app):
    import integrations_store
    rec = Recorded(monkeypatch)
    headers = env.headers()
    url = env.client.post("/api/integrations/github/connect", headers=headers).json()["url"]
    parts = urllib.parse.urlsplit(url)
    assert (parts.netloc, parts.path) == ("github.com", "/apps/kb-app/installations/new")
    state = dict(urllib.parse.parse_qsl(parts.query))["state"]
    installations(rec, (111, 42, "acme"), (222, 99, "other-apps-install"))
    r = env.client.get("/api/integrations/github/callback", follow_redirects=False, params={
        "state": state, "code": "abc", "installation_id": "111", "setup_action": "install"})
    assert r.headers["location"] == "/#integrations?connected=github&choose=1"
    assert integrations_store.secret(env.ws, "github") == {"installation_id": "111"}
    assert integrations_store.get(env.ws, "github")["account"] == "acme (Organization)"
    [exchange] = rec.requests_to("login/oauth/access_token")
    assert exchange["data"]["code"] == "abc" and exchange["data"]["client_id"] == "Iv1.test"


def test_a_spoofed_installation_id_is_refused(env, monkeypatch, app):
    import integrations_store
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/github/connect", headers=env.headers()).json()["url"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
    installations(rec, (111, 42, "acme"), (222, 99, "victim"))
    r = env.client.get("/api/integrations/github/callback", follow_redirects=False, params={
        "state": state, "code": "abc", "installation_id": "222", "setup_action": "install"})
    assert r.headers["location"] == "/#integrations?error=wrong_account"
    assert integrations_store.get(env.ws, "github") is None


def test_an_install_waiting_for_an_owner_says_so(env, monkeypatch, app):
    Recorded(monkeypatch)
    url = env.client.post("/api/integrations/github/connect", headers=env.headers()).json()["url"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
    r = env.client.get("/api/integrations/github/callback", follow_redirects=False,
                       params={"state": state, "setup_action": "request"})
    assert r.headers["location"] == "/#integrations?error=install_requested"


def test_no_installation_id_sends_the_admin_to_choose_one(env, monkeypatch, app):
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/github/connect", headers=env.headers()).json()["url"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
    installations(rec, (111, 42, "acme"), (333, 42, "acme-labs"))
    r = env.client.get("/api/integrations/github/callback", follow_redirects=False,
                       params={"state": state, "code": "abc"})
    nxt = urllib.parse.urlsplit(r.headers["location"])
    assert nxt.path == "/apps/kb-app/installations/new"
    again = dict(urllib.parse.parse_qsl(nxt.query))["state"]
    installations(rec, (111, 42, "acme"), (333, 42, "acme-labs"))
    r = env.client.get("/api/integrations/github/callback", follow_redirects=False, params={
        "state": again, "code": "def", "installation_id": "333", "setup_action": "update"})
    assert r.headers["location"] == "/#integrations?connected=github&choose=1"


def test_not_configured_without_the_apps_settings(env, monkeypatch):
    for k in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_FILE"):
        monkeypatch.delenv(k, raising=False)
    assert card(env)["status"] == "not_configured"
    assert env.client.post("/api/integrations/github/connect",
                           headers=env.headers()).status_code == 400


# ---- tokens ----------------------------------------------------------------------------
def test_the_app_jwt_is_rs256_signed_with_the_documented_claims(app):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from connectors import github
    token = github.app_jwt(now=1_800_000_000)
    head, claims, sig = token.split(".")
    pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
    assert json.loads(pad(head)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(pad(claims)) == {"iat": 1_800_000_000 - 60, "exp": 1_800_000_000 + 540,
                                       "iss": "42"}
    app.public_key().verify(pad(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(),
                            hashes.SHA256())


def test_installation_tokens_are_minted_cached_and_minted_again_near_expiry(env, monkeypatch,
                                                                            app):
    import integrations_store
    from connectors import github
    rec = Recorded(monkeypatch)
    connect(env)
    soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    rec.add("POST", f"{API}/app/installations/111/access_tokens", status=201,
            json_body={"token": "ghs_1", "expires_at": soon})
    c = github.CONNECTOR
    assert c._token(env.ws) == "ghs_1"
    assert c._token(env.ws) == "ghs_1"                          # cached
    [mint] = rec.requests_to("/access_tokens")
    assert mint["headers"]["Authorization"].startswith("Bearer ey")
    assert mint["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    sec = integrations_store.secret(env.ws, "github")
    integrations_store.update(env.ws, "github", secret={**sec, "expires_at": time.time() + 120})
    rec.add("POST", f"{API}/app/installations/111/access_tokens", status=201,
            json_body={"token": "ghs_2", "expires_at": soon})
    assert c._token(env.ws) == "ghs_2"                          # under 5 minutes left
    # Uninstalled: a reconnect is needed.
    integrations_store.update(env.ws, "github", secret={"installation_id": "111"})
    rec.add("POST", f"{API}/app/installations/111/access_tokens", status=404,
            json_body={"message": "Not Found"})
    with pytest.raises(base.AuthExpired):
        c._token(env.ws)


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_lists_the_installations_repos_and_keeps_code_per_repo(env, monkeypatch,
                                                                          app):
    rec = Recorded(monkeypatch)
    connect(env, repos=())
    rec.add("POST", f"{API}/app/installations/111/access_tokens", status=201,
            json_body={"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"})
    rec.add("GET", f"{API}/installation/repositories", json_body={
        "total_count": 2, "repositories": [
            {"full_name": H, "private": True}, {"full_name": A, "private": False}]})
    headers = env.headers()
    items = env.client.get("/api/integrations/github/containers", headers=headers).json()["items"]
    assert [(c["id"], c["private"]) for c in items] == [(A, False), (H, True)]
    r = env.client.put("/api/integrations/github/containers", headers=headers, json={
        "containers": [{**items[0], "code": True}, items[1]],
        "options": {"code_extensions": "py, .TS"}})
    assert r.status_code == 200, r.text
    assert [(c["id"], c["code"]) for c in r.json()["containers"]] == [(A, True), (H, False)]
    assert card(env)["options"] == {"code_extensions": [".py", ".ts"]}
    r = env.client.put("/api/integrations/github/containers", headers=headers, json={
        "containers": [], "options": {"code_extensions": ["../etc"]}})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_takes_docs_first_and_code_only_where_turned_on(env, monkeypatch, app):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "github/full")
    connect(env)
    summary = sync.sync(env.ws, "github")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"], summary["skipped"]) == ("full", 5, 2)
    assert {s for s, r in sync_store.items(env.ws, "github").items() if r.get("file_name")} == {
        f"{A}:README", f"{A}:src/server.py", f"{H}:README.md", f"{H}:docs/setup.md",
        f"{H}:docs/guide.rst"}
    # Over the size caps: listed as skipped, never downloaded.
    assert item(env, f"{H}:docs/big.md")["skipped_reason"] == "too_large"
    assert item(env, f"{A}:src/huge.py")["skipped_reason"] == "too_large"
    assert not rec.requests_to("b-big") and not rec.requests_to("b-huge")
    setup = item(env, f"{H}:docs/setup.md")
    assert setup["url"] == f"https://github.com/{H}/blob/main/docs/setup.md"
    assert env.text("github", setup["file_name"]) == (
        "acme/handbook/docs/setup.md\n\n# Setup\n\n```bash\nmake setup\n```")
    server = env.text("github", item(env, f"{A}:src/server.py")["file_name"])
    assert server.endswith("```py\ndef handler(event):\n    return {'ok': True}\n```")
    # Calls are made with the installation token.
    assert all(c["headers"]["Authorization"] == "Bearer ghs_test"
               for c in rec.calls if "/access_tokens" not in c["url"])
    cursor = json.loads(sync_store.state(env.ws, "github")["cursor"])
    assert {r: v["sha"] for r, v in cursor["repos"].items()} == {A: "d1", H: "c1"}


def test_acl_captures_visibility_collaborators_and_unknown_teams(env, monkeypatch, app):
    from kb.services import sync
    Recorded(monkeypatch, "github/full")
    connect(env)
    sync.sync(env.ws, "github")
    assert item(env, f"{A}:README")["acl"] == {"repo": A, "visibility": "public", "public": True}
    assert item(env, f"{H}:README.md")["acl"] == {
        "repo": H, "visibility": "private", "public": False,
        "users": ["github:ann", "github:bob"], "teams": None, "partial": True}


def test_then_the_compare_api_says_what_changed_since_the_last_sha(env, monkeypatch, app):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "github/full")
    connect(env)
    sync.sync(env.ws, "github")
    rec.load("github/incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "github")
    assert rec.unused() == []
    calls = rec.calls[before:]
    assert not [c for c in calls if "/git/trees/" in c["url"] or "/collaborators" in c["url"]]
    assert (summary["mode"], summary["upserted"], summary["deleted"]) == ("incremental", 2, 2)
    assert "make test" in env.text("github", item(env, f"{H}:docs/setup.md")["file_name"])
    assert item(env, f"{H}:docs/guide.rst") is None              # removed
    assert item(env, f"{H}:README.md") is None                   # renamed ...
    assert item(env, f"{H}:docs/intro.md")["file_name"]          # ... to here
    assert item(env, f"{H}:src/app.py") is None                  # code is off in this repo
    cursor = json.loads(sync_store.state(env.ws, "github")["cursor"])
    assert cursor["repos"][H]["sha"] == "c2"


def test_when_compare_cant_say_the_repo_is_listed_again(env, monkeypatch, app):
    from kb.services import sync
    rec = Recorded(monkeypatch, "github/full")
    connect(env, repos=((H, False),))
    sync.sync(env.ws, "github")
    # A force push: the old SHA is gone (404), so the tree is the truth again.
    rec.add("GET", f"{API}/repos/{H}", json_body={"full_name": H, "private": True,
                                                  "default_branch": "main"})
    rec.add("GET", f"{API}/repos/{H}/branches/main", json_body={"commit": {"sha": "c9"}})
    rec.add("GET", f"{API}/repos/{H}/compare/c1...c9", status=404,
            json_body={"message": "Not Found"})
    rec.add("GET", f"{API}/repos/{H}/git/trees/c9", json_body={"truncated": False, "tree": [
        {"path": "README.md", "type": "blob", "sha": "b-readme", "size": 40}]})
    summary = sync.sync(env.ws, "github")
    assert (summary["mode"], summary["deleted"], summary["unchanged"]) == ("incremental", 2, 1)


def test_a_truncated_tree_is_walked_sub_tree_by_sub_tree(env, monkeypatch, app):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env, repos=((H, False),))
    rec.add("POST", f"{API}/app/installations/111/access_tokens", status=201,
            json_body={"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"})
    rec.add("GET", f"{API}/repos/{H}", json_body={"full_name": H, "private": False,
                                                  "default_branch": "main"})
    rec.add("GET", f"{API}/repos/{H}/branches/main", json_body={"commit": {"sha": "c1"}})
    rec.add("GET", f"{API}/repos/{H}/git/trees/c1", params={"recursive": "1"},
            json_body={"truncated": True, "tree": []})
    rec.add("GET", f"{API}/repos/{H}/git/trees/c1", json_body={"truncated": False, "tree": [
        {"path": "docs", "type": "tree", "sha": "t-docs"},
        {"path": "node_modules", "type": "tree", "sha": "t-nm"}]})
    rec.add("GET", f"{API}/repos/{H}/git/trees/t-docs", json_body={"tree": [
        {"path": "a.md", "type": "blob", "sha": "b-a", "size": 10}]})
    rec.add("GET", f"{API}/repos/{H}/git/blobs/b-a", json_body={
        "size": 6, "encoding": "base64", "content": base64.b64encode(b"# A\n\nx").decode()})
    sync.sync(env.ws, "github")
    assert rec.unused() == []
    assert set(sync_store.items(env.ws, "github")) == {f"{H}:docs/a.md"}


def test_a_reconcile_that_finds_an_empty_tree_is_held_for_an_admin(env, monkeypatch, app):
    import sync_store
    from connectors import github
    from kb.services import sync
    rec = Recorded(monkeypatch, "github/full")
    connect(env, repos=((H, False),))
    sync.sync(env.ws, "github")
    state = json.loads(sync_store.state(env.ws, "github")["cursor"])
    sync_store.save_state(env.ws, "github", cursor=json.dumps(
        {**state, "full_at": time.time() - github.RECONCILE - 1}))
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")

    def emptied(rec):
        rec.add("GET", f"{API}/repos/{H}", json_body={"full_name": H, "private": False,
                                                      "default_branch": "main"})
        rec.add("GET", f"{API}/repos/{H}/branches/main", json_body={"commit": {"sha": "c5"}})
        rec.add("GET", f"{API}/repos/{H}/git/trees/c5", json_body={"tree": []})

    emptied(rec)
    summary = sync.sync(env.ws, "github")
    assert (summary["mode"], summary["held_deletes"]) == ("full", 3)
    emptied(rec)
    assert sync.sync(env.ws, "github", confirm_deletes=True)["deleted"] == 3


def test_githubs_rate_limits_are_waited_out_or_handed_back(env, monkeypatch, app):
    from connectors import github, http
    rec = Recorded(monkeypatch)
    connect(env)
    rec.add("POST", f"{API}/app/installations/111/access_tokens", status=201,
            json_body={"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"})
    url = f"{API}/repos/{H}"
    # A secondary limit with retry-after: waited out and retried.
    rec.add("GET", url, status=403, headers={"Retry-After": "4"},
            json_body={"message": "You have exceeded a secondary rate limit."})
    rec.add("GET", url, json_body={"full_name": H})
    assert github.CONNECTOR._json(env.ws, f"/repos/{H}") == {"full_name": H}
    assert 4 in rec.slept
    # The primary limit: remaining 0 until the reset, an hour away: handed back.
    reset = str(int(time.time()) + 3600)
    rec.add("GET", url, status=403, repeat=True, json_body={"message": "API rate limit exceeded"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})
    with pytest.raises(base.RateLimited) as e:
        github.CONNECTOR._json(env.ws, f"/repos/{H}")
    assert e.value.retry_after > http.MAX_WAIT
    # A plain 403 is still Forbidden, a gone repo a settings problem.
    rec.exchanges.clear()
    rec.add("GET", url, status=403, json_body={"message": "Resource not accessible"})
    with pytest.raises(base.Forbidden):
        github.CONNECTOR._json(env.ws, f"/repos/{H}")


def test_a_picked_repo_that_is_gone_needs_an_admin(env, monkeypatch, app):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect(env, repos=((H, False),))
    rec.add("POST", f"{API}/app/installations/111/access_tokens", status=201,
            json_body={"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"})
    rec.add("GET", f"{API}/repos/{H}", status=404, json_body={"message": "Not Found"})
    with pytest.raises(base.ConfigError):
        sync.sync(env.ws, "github")
    assert card(env)["error"]["code"] == "config"


def test_github_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["github"] == "live"
