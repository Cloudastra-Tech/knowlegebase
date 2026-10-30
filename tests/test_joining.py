"""
Joining a workspace by signing in (join_store.py, kb/services/joining.py): the
joining policy on every sign-in path that can add someone (SAML's is in
test_saml.py), join requests from creation to approval, denial and expiry, who
never meets the policy, the migration's defaults, and claiming a workspace
nobody can administer any more. Through the real API; the providers, Microsoft
Graph and email are faked.
"""

import json
import sqlite3
import time
import uuid
from urllib.parse import parse_qs

import pytest
from fastapi.responses import RedirectResponse

import audit_store
import auth
import db
import join_store
import members_store
from test_operator import operator_sign_in
from test_sign_in_settings import DOC, ISSUER
from test_signup_more import microsoft  # noqa: F401  (the Microsoft 365 and Graph fake)

PASSWORD = "correct-horse"
MS_ISS = "https://login.microsoftonline.com/tenant-id/v2.0"
OPERATOR = "op@cloudastra.example"


@pytest.fixture
def client():
    """A browser of its own per test: a claim started in one never leaks into another."""
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def new_workspace(policy=None, prefix="join"):
    """A workspace with a verified domain and a password admin. (slug, domain)"""
    ws = f"{prefix}-{uuid.uuid4().hex[:8]}"
    domain = f"{ws}.example"
    members_store.set_password(f"admin@{domain}", "Admin", auth.hash_password(PASSWORD), ws,
                               "admin")
    members_store.add_verified_domain(ws, domain)
    if policy:
        join_store.set_workspace_policy(ws, policy)
    return ws, domain


def login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def events(ws, action=None):
    return [e for e in audit_store.list_events(ws, limit=200)[0]
            if action is None or e["action"] == action]


@pytest.fixture
def mail(monkeypatch):
    """Every email sent: (to, template, values)."""
    import mailer
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, template, **v: sent.append((to, template, v)))
    return sent


@pytest.fixture
def providers(monkeypatch):
    """The server-wide providers, faked: their callback gets `claims`."""
    import sso

    class Fake:
        claims = {}
        redirect = None

        async def authorize_redirect(self, request, redirect_uri, **params):
            Fake.redirect = (redirect_uri, params)
            return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth")

        async def authorize_access_token(self, request):
            return {"userinfo": self.claims}

    fake = Fake()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(sso, "client", lambda name: fake)
    monkeypatch.setattr(sso, "connection_client", lambda conn: fake)
    return fake


def _connection(ws):
    import sso_setup
    return members_store.create_connection(ws, "oidc", "Acme Okta", ISSUER, "kb",
                                           {"client_secret": "s"},
                                           {k: DOC[k] for k in sso_setup.METADATA_KEYS
                                            if k in DOC}, "test")


# Every sign-in path that can add a member, except SAML (test_saml.py).
PATHS = ("google", "microsoft", "okta", "microsoft365", "connection")


def sign_in(client, fakes, path, domain, local="pat"):
    """Sign in through `path` as local@domain: (where the browser goes, the email)."""
    providers, graph = fakes
    email = f"{local}@{domain}"
    if path == "microsoft365":
        graph.domain, graph.verified, graph.user_type = domain, None, "Member"
        email = f"megan.bowen@{domain}"             # who Graph's recorded answer is
        url = "/api/auth/m365/callback?code=x&state=y"
    elif path == "connection":
        ws = members_store.workspace_for_domain(domain)
        conn = (members_store.list_connections(ws) or [_connection(ws)])[0]
        providers.claims = {"email": email, "email_verified": True, "name": "Pat"}
        url = f"/api/auth/sso/c/{conn['id']}/callback?code=x&state=y"
    else:
        providers.claims = {
            "google": {"email": email, "email_verified": True, "hd": domain, "name": "Pat"},
            "microsoft": {"preferred_username": email, "iss": MS_ISS, "name": "Pat"},
            "okta": {"email": email, "email_verified": "true", "name": "Pat"},
        }[path]
        url = f"/api/auth/sso/{path}/callback?code=x&state=y"
    r = client.get(url)
    assert r.status_code in (302, 303, 307), r.text
    return r.headers["location"], email


@pytest.fixture
def fakes(providers, microsoft):  # noqa: F811
    return providers, microsoft


def error_of(location, tab="login"):
    assert location.startswith(f"/#{tab}?"), location
    return parse_qs(location.split("?", 1)[1])["error"][0]


# ---- The policy on every path ---------------------------------------------------------
@pytest.mark.parametrize("path", PATHS)
def test_auto_adds_them_as_a_member(client, fakes, path):
    ws, domain = new_workspace("auto")
    location, email = sign_in(client, fakes, path, domain)
    assert location.startswith("/#sso?code="), location
    member = members_store.get(email)
    assert (member["workspace"], member["role"]) == (ws, "public")
    assert [e["actor"] for e in events(ws, "member.join")] == [email]
    assert join_store.pending(ws) == []


@pytest.mark.parametrize("path", PATHS)
def test_request_records_a_request_and_adds_nobody(client, fakes, mail, path):
    ws, domain = new_workspace("request")
    location, email = sign_in(client, fakes, path, domain)
    assert location == "/#join-requested"               # its own page, no session
    assert members_store.get(email) is None
    (req,) = join_store.pending(ws)
    assert (req["email"], req["status"]) == (email, "pending")
    assert req["provider"] in ("Google", "Microsoft", "Okta", "Microsoft 365", "Acme Okta")
    assert [(to, t) for to, t, _ in mail] == [(f"admin@{domain}", "join_request")]
    assert mail[0][2]["email"] == email and mail[0][2]["link"].endswith("/admin/members")
    assert [e["actor"] for e in events(ws, "join_request.create")] == [email]
    # Signing in again only updates it: no second request, no second email.
    assert sign_in(client, fakes, path, domain)[0] == "/#join-requested"
    assert len(join_store.pending(ws)) == 1 and len(mail) == 1
    assert len(events(ws, "join_request.create")) == 1


@pytest.mark.parametrize("path", PATHS)
def test_invite_only_turns_them_away(client, fakes, mail, path):
    ws, domain = new_workspace("invite_only")
    location, email = sign_in(client, fakes, path, domain)
    assert error_of(location) == "invite_only"
    assert members_store.get(email) is None and join_store.pending(ws) == [] and mail == []


def test_a_domain_can_have_its_own_policy(client, fakes):
    ws, domain = new_workspace("auto")
    other = f"other-{domain}"
    members_store.add_verified_domain(ws, other)
    join_store.set_domain_policy(ws, other, "request")
    assert sign_in(client, fakes, "google", domain)[0].startswith("/#sso?code=")
    assert sign_in(client, fakes, "google", other)[0] == "/#join-requested"
    # ...and the other way round: the workspace asks, one domain joins straight away.
    join_store.set_workspace_policy(ws, "request")
    join_store.set_domain_policy(ws, other, "auto")
    assert sign_in(client, fakes, "google", domain, "kim")[0] == "/#join-requested"
    assert sign_in(client, fakes, "google", other, "kim")[0].startswith("/#sso?code=")
    assert join_store.policy_for(ws, other) == "auto" and join_store.policy_for(ws, domain) == (
        "request")


# ---- Who never meets the policy -------------------------------------------------------------
@pytest.mark.parametrize("policy", ["request", "invite_only"])
def test_existing_members_sign_in_whatever_the_policy(client, fakes, policy):
    ws, domain = new_workspace(policy)
    members_store.set_role(f"given@{domain}", "hr", ws)        # a role before their first sign-in
    location, _ = sign_in(client, fakes, "google", domain, "given")
    assert location.startswith("/#sso?code=")
    assert members_store.get(f"given@{domain}")["role"] == "hr"


@pytest.mark.parametrize("path", ["google", "connection"])
def test_a_pending_invite_lets_them_in_and_is_used_up(client, fakes, path):
    import invites_store
    ws, domain = new_workspace("invite_only")
    invite, _ = invites_store.create(ws, f"invited@{domain}", "hr", f"admin@{domain}")
    location, email = sign_in(client, fakes, path, domain, "invited")
    assert location.startswith("/#sso?code="), location
    assert members_store.get(email)["role"] == "hr"
    assert invites_store.list_open(ws) == []                   # accepted
    (e,) = events(ws, "invite.accept")
    assert (e["actor"], e["details"]["role"]) == (email, "hr")


def test_scim_provisioned_people_sign_in_whatever_the_policy(client, fakes):
    import scim_store
    ws, domain = new_workspace("invite_only")
    _, token = scim_store.create_token(ws, "test")
    r = client.post("/scim/v2/Users", headers={"Authorization": f"Bearer {token}",
                                                "Content-Type": "application/scim+json"},
                    content=json.dumps({"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                                        "userName": f"prov@{domain}", "active": True}))
    assert r.status_code == 201, r.text
    assert sign_in(client, fakes, "okta", domain, "prov")[0].startswith("/#sso?code=")


def test_an_account_in_another_workspace_is_still_refused(client, fakes):
    ws, domain = new_workspace("auto")
    members_store.set_password(f"elsewhere@{domain}", "E", auth.hash_password(PASSWORD),
                               f"other-{ws}")
    assert error_of(sign_in(client, fakes, "google", domain, "elsewhere")[0]) == "not_allowed"


# ---- The request's life ----------------------------------------------------------------------
def requests_of(client, h):
    r = client.get("/api/admin/members/requests", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["requests"]


def test_an_admin_approves_with_a_role_and_their_next_sign_in_works(client, fakes, mail):
    ws, domain = new_workspace("request")
    h = login(client, f"admin@{domain}")
    _, email = sign_in(client, fakes, "google", domain)
    (req,) = requests_of(client, h)
    assert (req["email"], req["name"], req["provider"], req["notified"]) == (
        email, "Pat", "Google", True)
    r = client.post(f"/api/admin/members/requests/{req['id']}/approve", headers=h,
                    json={"role": "hr"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert members_store.get(email)["role"] == "hr"
    assert requests_of(client, h) == []
    (e,) = events(ws, "join_request.approve")
    assert (e["actor"], e["details"]) == (f"admin@{domain}", {"email": email, "role": "hr"})
    assert (email, "join_approved") in [(to, t) for to, t, _ in mail]
    location, _ = sign_in(client, fakes, "google", domain)
    assert location.startswith("/#sso?code=")
    token = client.post("/api/auth/sso/exchange", json={"code": location.split("code=")[1]}
                        ).json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert (me["workspace"], me["role"]) == (ws, "hr")
    # Answered once: again is a 404.
    assert client.post(f"/api/admin/members/requests/{req['id']}/deny",
                       headers=h).status_code == 404


def test_approving_as_a_member_leaves_their_groups_in_charge(client, fakes):
    ws, domain = new_workspace("request")
    h = login(client, f"admin@{domain}")
    _, email = sign_in(client, fakes, "google", domain)
    (req,) = requests_of(client, h)
    client.post(f"/api/admin/members/requests/{req['id']}/approve", headers=h, json={})
    member = members_store.get(email)
    assert (member["role"], member["override_role"]) == ("public", None)


def test_a_denial_is_remembered_then_they_can_ask_again(client, fakes, mail, monkeypatch):
    ws, domain = new_workspace("request")
    h = login(client, f"admin@{domain}")
    _, email = sign_in(client, fakes, "google", domain)
    (req,) = requests_of(client, h)
    assert client.post(f"/api/admin/members/requests/{req['id']}/deny",
                       headers=h).status_code == 200
    assert [e["details"] for e in events(ws, "join_request.deny")] == [{"email": email}]
    assert members_store.get(email) is None
    mail.clear()
    # Within the cooldown: refused, and nobody is asked again.
    assert error_of(sign_in(client, fakes, "google", domain)[0]) == "join_denied"
    assert requests_of(client, h) == [] and mail == []
    # After it: a new request.
    with db.connect() as conn:             # the app's database, either backend
        conn.execute("UPDATE join_requests SET decided_at = ? WHERE id = ?",
                     (time.time() - (join_store.deny_days() + 1) * 86400, req["id"]))
    conn.close()
    assert sign_in(client, fakes, "google", domain)[0] == "/#join-requested"
    assert [r["email"] for r in requests_of(client, h)] == [email] and len(mail) == 1


def test_requests_expire_after_thirty_days(client, fakes):
    ws, domain = new_workspace("request")
    h = login(client, f"admin@{domain}")
    assert join_store.request_days() == 30
    old, _ = join_store.request(ws, f"old@{domain}", "Old", "Google",
                                now=time.time() - 31 * 86400)
    assert requests_of(client, h) == []                       # expired when listed
    (e,) = events(ws, "join_request.expire")
    assert (e["actor"], e["actor_type"], e["target_id"]) == (None, "system", str(old["id"]))
    assert client.post(f"/api/admin/members/requests/{old['id']}/approve", headers=h,
                       json={"role": "admin"}).status_code == 404
    assert members_store.get(f"old@{domain}") is None
    # Signing in again asks afresh.
    assert sign_in(client, fakes, "google", domain, "old")[0] == "/#join-requested"
    assert len(requests_of(client, h)) == 1


def test_admins_are_emailed_at_most_so_often(client, fakes, mail, monkeypatch):
    monkeypatch.setenv("JOIN_NOTICES_PER_HOUR", "2")
    ws, domain = new_workspace("request")
    members_store.set_role(f"second-admin@{domain}", "admin", ws)
    for local in ("a", "b", "c"):
        sign_in(client, fakes, "google", domain, local)
    assert len(join_store.pending(ws)) == 3
    told = sorted({to for to, t, _ in mail if t == "join_request"})
    assert told == [f"admin@{domain}", f"second-admin@{domain}"]
    assert len(mail) == 4                                     # 2 requests x 2 admins
    assert [bool(r["notified_at"]) for r in join_store.pending(ws)] == [True, True, False]


def test_only_the_workspaces_own_admins_see_and_answer_its_requests(client, fakes):
    ws, domain = new_workspace("request")
    other, other_domain = new_workspace("request")
    members_store.set_password(f"staff@{domain}", "S", auth.hash_password(PASSWORD), ws, "hr")
    sign_in(client, fakes, "google", domain)
    (req,) = join_store.pending(ws)
    stranger = login(client, f"admin@{other_domain}")
    assert requests_of(client, stranger) == []
    for verb in ("approve", "deny"):
        assert client.post(f"/api/admin/members/requests/{req['id']}/{verb}", headers=stranger,
                           json={"role": "admin"}).status_code == 404
    hr = login(client, f"staff@{domain}")
    assert client.get("/api/admin/members/requests", headers=hr).status_code == 403
    assert client.post(f"/api/admin/members/requests/{req['id']}/approve", headers=hr,
                       json={"role": "admin"}).status_code == 403
    assert join_store.pending(ws)[0]["status"] == "pending"


def test_approving_someone_who_joined_elsewhere_meanwhile_is_refused(client, fakes):
    ws, domain = new_workspace("request")
    h = login(client, f"admin@{domain}")
    _, email = sign_in(client, fakes, "google", domain)
    members_store.set_password(email, "P", auth.hash_password(PASSWORD), f"else-{ws}")
    (req,) = requests_of(client, h)
    assert client.post(f"/api/admin/members/requests/{req['id']}/approve", headers=h,
                       json={"role": "public"}).status_code == 409


def test_a_data_request_exports_and_erases_their_requests(client, fakes):
    ws, domain = new_workspace("request")
    h = login(client, f"admin@{domain}")
    _, email = sign_in(client, fakes, "google", domain)
    (req,) = requests_of(client, h)
    client.post(f"/api/admin/members/requests/{req['id']}/approve", headers=h, json={})
    exported = client.get(f"/api/admin/members/{email}/export", headers=h).json()
    assert [r["status"] for r in exported["join_requests"]] == ["approved"]
    r = client.post(f"/api/admin/members/{email}/erase", headers=h, json={"confirm": email})
    assert r.status_code == 200, r.text
    with db.connect() as conn:             # the app's database, either backend
        left = conn.execute("SELECT COUNT(*) FROM join_requests WHERE workspace = ?",
                            (ws,)).fetchone()[0]
    conn.close()
    assert left == 0


# ---- The setting -----------------------------------------------------------------------------
def test_admins_set_the_policy_for_the_workspace_and_per_domain(client):
    ws, domain = new_workspace()
    h = login(client, f"admin@{domain}")
    settings = client.get("/api/admin/sign-in", headers=h).json()
    assert settings["join_policy"] == "request"                # a new workspace's default
    assert settings["domains"][0]["join_policy"] is None
    r = client.put("/api/admin/sign-in/joining", headers=h, json={"policy": "invite_only"})
    assert r.status_code == 200 and join_store.workspace_policy(ws) == "invite_only"
    assert client.put("/api/admin/sign-in/joining", headers=h,
                      json={"policy": "everyone"}).status_code == 422
    r = client.put(f"/api/admin/sign-in/domains/{domain}/joining", headers=h,
                   json={"policy": "auto"})
    assert r.status_code == 200 and r.json()["join_policy"] == "auto"
    assert client.get("/api/admin/sign-in", headers=h).json()["domains"][0]["join_policy"] == (
        "auto")
    assert client.put(f"/api/admin/sign-in/domains/{domain}/joining", headers=h,
                      json={"policy": None}).json()["join_policy"] is None
    assert client.put("/api/admin/sign-in/domains/nope.example/joining", headers=h,
                      json={"policy": "auto"}).status_code == 404
    assert [e["details"] for e in events(ws, "sign_in.joining")] == [
        {"join_policy": "invite_only", "was": "request"}]
    assert [e["details"] for e in reversed(events(ws, "domain.joining"))] == [
        {"join_policy": "auto"}, {"join_policy": None}]


# ---- The migration's defaults ---------------------------------------------------------------
@pytest.mark.sqlite_only("upgrades a SQLite file one migration at a time with alembic")
def test_existing_workspaces_migrate_to_auto_and_new_ones_start_at_request(tmp_path,
                                                                          monkeypatch):
    from tests import alembic_steps
    path = str(tmp_path / "app.db")
    alembic_steps.upgrade(path, "0020")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO workspaces (slug, name, created_at) VALUES ('old', 'Old', 1)")
        conn.execute("INSERT INTO workspace_domains (workspace, domain, verified_at) "
                     "VALUES ('old', 'old.example', 1)")
    conn.close()
    alembic_steps.upgrade(path, "head")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO workspaces (slug, name, created_at) VALUES ('new', 'New', 2)")
        policies = dict(conn.execute("SELECT slug, join_policy FROM workspaces"))
        domain_policy = conn.execute("SELECT join_policy FROM workspace_domains").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE workspaces SET join_policy = 'anyone' WHERE slug = 'new'")
    conn.close()
    assert policies == {"old": "auto", "new": "request"} and domain_policy is None


def test_workspaces_made_by_the_app_start_at_request():
    slug = members_store.create_workspace("Fresh Co", f"fresh-{uuid.uuid4().hex[:6]}.example",
                                          f"f-{uuid.uuid4().hex[:6]}@fresh.example", "F")
    assert join_store.workspace_policy(slug) == "request"
    lazy = f"lazy-{uuid.uuid4().hex[:6]}"                     # made by the first row in it
    members_store.set_role(f"someone@{lazy}.example", "public", lazy)
    assert join_store.workspace_policy(lazy) == "request"


# ---- Claiming a workspace nobody can administer ----------------------------------------------
def orphan(admin_password=False, admin_seen_days=None):
    """A hand-made workspace with no verified domain and an admin who can't get in
    (unless asked otherwise). (slug, domain)"""
    ws = f"orphan-{uuid.uuid4().hex[:8]}"
    domain = f"{ws}.example"
    members_store.set_role(f"gone@{ws}.old", "admin", ws)       # never signed in, no password
    members_store.set_role(f"staff@{domain}", "public", ws)
    if admin_password:
        members_store.set_password(f"pw-admin@{ws}.old", "A", auth.hash_password(PASSWORD), ws,
                                   "admin")
    if admin_seen_days is not None:
        with db.connect() as conn:             # the app's database, either backend
            conn.execute("UPDATE members SET last_login_at = ? WHERE username = ?",
                         (time.time() - admin_seen_days * 86400, f"gone@{ws}.old"))
        conn.close()
    return ws, domain


def claimant(domain, local="owner", hd=None):
    return {"email": f"{local}@{domain}", "email_verified": True, "hd": hd or domain,
            "name": "Olive Owner"}


def claim(client, providers, code, claims):
    """POST the code, start, come back from Google: (POST response, where they land)."""
    r = client.post("/api/auth/claim", json={"code": code})
    if r.status_code != 200:
        return r, None
    start = client.get(r.json()["url"])
    assert start.status_code in (302, 307), start.text
    providers.claims = claims
    return r, client.get("/api/auth/sso/google/callback?code=x&state=y").headers["location"]


def test_a_workspace_nobody_can_administer_is_claimed_with_google_workspace(client, providers):
    ws, domain = orphan()
    _, code = join_store.new_claim_code(ws, domain, OPERATOR)
    r, location = claim(client, providers, code, claimant(domain))
    assert r.json()["domain"] == domain
    assert providers.redirect[1]["hd"] == domain               # Google shows that Workspace
    assert location.startswith("/#sso?welcome=1&code="), location
    token = client.post("/api/auth/sso/exchange", json={"code": location.split("code=")[1]}
                        ).json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert (me["username"], me["workspace"], me["role"]) == (f"owner@{domain}", ws, "admin")
    assert members_store.verified_domains(ws) == {domain}
    assert join_store.workspace_policy(ws) == "request"        # the new admin decides who joins
    (e,) = events(ws, "workspace.claim")
    assert (e["actor"], e["details"]["domain"], e["details"]["issued_by"]) == (
        f"owner@{domain}", domain, OPERATOR)
    assert [x["target_id"] for x in events(audit_store.PLATFORM, "workspace.claim")
            if x["target_id"] == ws] == [ws]
    assert code not in json.dumps(events(ws)) + json.dumps(events(audit_store.PLATFORM))
    # Once only, and now it has a verified domain and an admin anyway.
    assert claim(client, providers, code, claimant(domain, "again"))[0].status_code == 400
    with pytest.raises(join_store.ClaimRefused, match="claim_verified"):
        join_store.new_claim_code(ws, domain, OPERATOR)


def test_an_existing_member_who_claims_becomes_its_admin(client, providers):
    ws, domain = orphan()
    _, code = join_store.new_claim_code(ws, domain, OPERATOR)
    _, location = claim(client, providers, code, claimant(domain, "staff"))
    assert location.startswith("/#sso?welcome=1&code=")
    member = members_store.get(f"staff@{domain}")
    assert (member["workspace"], member["role"]) == (ws, "admin")


@pytest.mark.parametrize("make", [
    lambda: orphan(admin_password=True),                     # a password admin can still get in
    lambda: orphan(admin_seen_days=10),                      # an admin signed in lately
    lambda: new_workspace("auto"),                           # it has a verified domain
], ids=["password_admin", "recent_admin", "verified_domain"])
def test_a_workspace_with_a_way_in_cant_be_claimed(client, make):
    ws, domain = make()
    assert join_store.claimable(ws) in ("claim_admin", "claim_verified")
    with pytest.raises(join_store.ClaimRefused):
        join_store.new_claim_code(ws, f"x-{domain}", OPERATOR)


def test_an_admin_unseen_for_long_or_disabled_doesnt_count(client):
    ws, _ = orphan(admin_seen_days=join_store.admin_idle_days() + 1)
    assert join_store.claimable(ws) is None
    ws, _ = orphan(admin_password=True)
    members_store.set_disabled(f"pw-admin@{ws}.old", True, ws)
    assert join_store.claimable(ws) is None


def test_claims_that_change_their_mind_midway_are_refused(client, providers):
    """The workspace gets a way in after the code was issued: refused at the end."""
    ws, domain = orphan()
    _, code = join_store.new_claim_code(ws, domain, OPERATOR)
    client.post("/api/auth/claim", json={"code": code})
    client.get("/api/auth/claim/google/start")
    members_store.set_password(f"back@{ws}.old", "B", auth.hash_password(PASSWORD), ws, "admin")
    providers.claims = claimant(domain)
    location = client.get("/api/auth/sso/google/callback?code=x&state=y").headers["location"]
    assert error_of(location, tab="claim") == "claim_admin"
    assert members_store.verified_domains(ws) == set() and members_store.get(
        f"owner@{domain}") is None
    assert join_store.find_code(code) is not None              # not used up by a refusal


@pytest.mark.parametrize("claims, reason", [
    (lambda d: claimant(d, hd=f"other-{d}"), "claim_domain"),     # another Google Workspace
    (lambda d: {**claimant(f"alias-{d}"), "hd": d}, "claim_domain"),  # email not on the domain
    (lambda d: {"email": f"me@{d}", "email_verified": True}, "not_workspace"),  # personal
    (lambda d: {**claimant(d), "email_verified": False}, "unverified"),
])
def test_only_a_google_workspace_account_on_the_codes_domain_can_claim(client, providers,
                                                                       claims, reason):
    ws, domain = orphan()
    _, code = join_store.new_claim_code(ws, domain, OPERATOR)
    _, location = claim(client, providers, code, claims(domain))
    assert error_of(location, tab="claim") == reason
    assert members_store.verified_domains(ws) == set()


def test_a_domain_or_account_of_another_workspace_cant_claim(client, providers):
    ws, domain = orphan()
    _, code = join_store.new_claim_code(ws, domain, OPERATOR)
    members_store.set_password(f"owner@{domain}", "O", auth.hash_password(PASSWORD),
                               f"taken-{ws}")
    _, location = claim(client, providers, code, claimant(domain))
    assert error_of(location, tab="claim") == "claim_taken"
    # A domain another workspace verifies after the code was issued.
    ws2, domain2 = orphan()
    _, code2 = join_store.new_claim_code(ws2, domain2, OPERATOR)
    members_store.add_verified_domain(f"rival-{ws2}", domain2)
    _, location = claim(client, providers, code2, claimant(domain2))
    assert error_of(location, tab="claim") in ("claim_taken", "claim_code")
    assert members_store.workspace_for_domain(domain2) == f"rival-{ws2}"


def test_bad_expired_and_replaced_codes_and_closed_workspaces(client, providers, monkeypatch):
    import lifecycle_store
    assert client.post("/api/auth/claim", json={"code": "kbclaim-nope"}).status_code == 400
    ws, domain = orphan()
    row, old = join_store.new_claim_code(ws, domain, OPERATOR, now=time.time() - 73 * 3600)
    assert client.post("/api/auth/claim", json={"code": old}).status_code == 400   # expired
    _, first = join_store.new_claim_code(ws, domain, OPERATOR)
    _, second = join_store.new_claim_code(ws, domain, OPERATOR)
    assert client.post("/api/auth/claim", json={"code": first}).status_code == 400  # replaced
    lifecycle_store.suspend(ws, "abuse", OPERATOR)
    r = client.post("/api/auth/claim", json={"code": second})
    assert r.status_code == 409 and "suspended" in r.json()["detail"]
    lifecycle_store.unsuspend(ws)
    assert client.post("/api/auth/claim", json={"code": second}).status_code == 200
    monkeypatch.setattr("sso.client", lambda name: None)       # Google isn't set up
    assert client.post("/api/auth/claim", json={"code": second}).status_code == 400


def test_an_ordinary_sign_in_after_starting_a_claim_doesnt_claim(client, providers):
    ws, domain = orphan()
    _, code = join_store.new_claim_code(ws, domain, OPERATOR)
    assert client.post("/api/auth/claim", json={"code": code}).status_code == 200
    client.get("/api/auth/sso/google/start")                   # a normal sign-in instead
    providers.claims = claimant(domain)
    location = client.get("/api/auth/sso/google/callback?code=x&state=y").headers["location"]
    assert error_of(location) == "not_allowed"                 # no verified domain: nobody in
    assert members_store.verified_domains(ws) == set()


@pytest.fixture
def operator_idp(monkeypatch):
    import sso
    from kb.routers import operator

    class Client:
        async def authorize_redirect(self, request, redirect_uri, **kwargs):
            return RedirectResponse("https://accounts.example/auth")

    async def answer(name, c, request):
        return {"email": OPERATOR, "email_verified": True, "hd": "cloudastra.example",
                "name": "Op", "amr": ["pwd", "mfa"]}, "id-token"

    monkeypatch.setattr(sso, "configured", lambda: ["google"])
    monkeypatch.setattr(sso, "client", lambda provider: Client())
    monkeypatch.setattr(operator, "provider_answer", answer)
    monkeypatch.setenv("OPERATOR_EMAILS", OPERATOR)


def test_operators_issue_claim_codes_in_both_audit_logs(client, operator_idp):
    op = operator_sign_in(client)
    ws, domain = orphan()
    r = client.post(f"/api/operator/workspaces/{ws}/claim-code", headers=op,
                    json={"domain": domain.upper()})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert r.json()["domain"] == domain and join_store.find_code(code)["workspace"] == ws
    (e,) = events(ws, "operator.claim_code")
    assert (e["actor"], e["actor_type"], e["details"]["domain"]) == (OPERATOR, "operator", domain)
    assert code not in json.dumps(events(ws)) + json.dumps(events(audit_store.PLATFORM))
    taken, taken_domain = new_workspace("auto")
    r = client.post(f"/api/operator/workspaces/{taken}/claim-code", headers=op,
                    json={"domain": f"x-{taken_domain}"})
    assert r.status_code == 409 and "verified domain" in r.json()["detail"]
    member = login(client, f"admin@{taken_domain}")
    assert client.post(f"/api/operator/workspaces/{ws}/claim-code", headers=member,
                       json={"domain": domain}).status_code == 401
