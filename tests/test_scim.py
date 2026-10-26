"""
SCIM 2.0 provisioning (kb/routers/scim.py, kb/services/scim.py, scim_store.py),
through the real API, driven by the requests Okta and Entra ID actually send:
tests/fixtures/scim/okta.json and entra.json are copied from their published
SCIM docs (the URLs are in the files). Each test has a workspace of its own,
with a verified domain, a password admin and a SCIM token made the way an admin
makes one (the Sign-in screen's API).
"""

import json
import pathlib
import time
import uuid

import pytest

import audit_store
import members_store
import scim_store
import sessions_store

PASSWORD = "correct-horse"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scim"
SCIM_JSON = "application/scim+json"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


class Tenant:
    """A workspace, its verified domain, its admin's headers and a SCIM token."""

    def __init__(self, client, prefix="scim"):
        import auth
        self.client = client
        self.ws = f"{prefix}-{uuid.uuid4().hex[:8]}"
        self.domain = f"{self.ws}.example"
        self.admin = f"admin@{self.domain}"
        members_store.set_password(self.admin, "Admin", auth.hash_password(PASSWORD), self.ws,
                                   "admin")
        members_store.add_verified_domain(self.ws, self.domain)
        self.h = login(client, self.admin)
        r = client.post("/api/admin/sign-in/scim/tokens", headers=self.h)
        assert r.status_code == 200, r.text
        self.token = r.json()["token"]
        self.ids = {}                    # the provider's example id -> ours

    def call(self, method, path, body=None, token=None):
        for theirs, ours in self.ids.items():
            path = path.replace(theirs, ours)
        headers = {"Authorization": f"Bearer {token or self.token}"}
        content = None
        if body is not None:
            text = json.dumps(body)
            for theirs, ours in self.ids.items():
                text = text.replace(theirs, ours)
            content, headers["Content-Type"] = self.on_our_domain(text), SCIM_JSON
        r = self.client.request(method, self.on_our_domain(path), content=content,
                                headers=headers)
        if r.status_code != 204:
            assert r.headers["content-type"].startswith(SCIM_JSON), r.headers
        return r

    def send(self, fixture, name):
        req = fixture["requests"][name]
        return self.call(req["method"], req["path"], req.get("body"))

    def on_our_domain(self, text):
        for theirs in ("@test.microsoft.com", "@microsoft.com", "@testuser.com", "@okta.local",
                       "%40okta.local"):
            text = text.replace(theirs, ("%40" if theirs.startswith("%") else "@") + self.domain)
        return text

    def member(self, username):
        return members_store.get(username)

    def events(self):
        return [(e["email"], e["reason"]) for e in
                members_store.recent_events(self.ws, limit=200, scim=True)][::-1]

    def audit(self):
        return [e for e in audit_store.list_events(self.ws, limit=200)[0]][::-1]


@pytest.fixture
def tenant(client):
    return Tenant(client)


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def scim_rules(t, rules, make_role_source=False):
    r = t.client.put("/api/admin/sign-in/scim/roles", headers=t.h,
                     json={"rules": [{"group": g, "role": role} for g, role in rules],
                           "make_role_source": make_role_source})
    return r


def a_session(username, ws):
    token = uuid.uuid4().hex
    sessions_store.create(token, {"username": username, "workspace": ws, "provider": "oidc"},
                          3600)
    return token


# ---- Okta ---------------------------------------------------------------------------------
def test_okta_lifecycle(tenant):
    t, okta = tenant, load("okta")
    email = f"test.user@{t.domain}"

    r = t.send(okta, "find_user")
    assert r.status_code == 200 and r.json()["totalResults"] == 0
    assert r.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]

    r = t.send(okta, "create_user")
    assert r.status_code == 201, r.text
    user = r.json()
    t.ids["23a35c27-23d3-4c03-b4c5-6443c09e7173"] = user["id"]
    assert user["userName"] == email and user["active"] is True
    assert user["externalId"] == "00ujl29u0le5T6Aj10h7"
    assert user["name"] == {"givenName": "Test", "familyName": "User"}
    assert r.headers["location"].endswith(f"/scim/v2/Users/{user['id']}")
    m = t.member(email)
    assert m["workspace"] == t.ws and m["name"] == "Test User" and m["role"] == "public"
    assert m["password_hash"] is None                  # Okta's password is never kept

    r = t.send(okta, "find_user")
    assert r.json()["totalResults"] == 1 and r.json()["Resources"][0]["id"] == user["id"]
    assert t.send(okta, "create_user").status_code == 409          # Okta looks first; if not

    r = t.send(okta, "replace_user")
    assert r.status_code == 200 and r.json()["name"]["middleName"] == "Excited"
    assert r.json()["externalId"] == "00ujl29u0le5T6Aj10h7"        # PUT left it out: kept

    session = a_session(email, t.ws)
    r = t.send(okta, "deactivate_user")
    assert r.status_code == 200 and r.json()["active"] is False
    assert t.member(email)["disabled"] and sessions_store.get(session) is None
    r = t.send(okta, "reactivate_user")
    assert r.json()["active"] is True and not t.member(email)["disabled"]

    # Groups: pushed, found, members changed, renamed, replaced, removed.
    r = t.send(okta, "create_group")
    assert r.status_code == 201 and r.json()["members"] == []
    group = r.json()
    t.ids["abf4dd94-a4c0-4f67-89c9-76b03340cb9b"] = group["id"]
    r = t.send(okta, "find_group")
    assert [g["id"] for g in r.json()["Resources"]] == [group["id"]]

    assert scim_rules(t, [("Test SCIMv2", "hr")]).status_code == 200   # SCIM sets roles
    r = t.send(okta, "group_members")        # removes someone it never had; adds the user
    assert r.status_code == 200
    assert [x["value"] for x in r.json()["members"]] == [user["id"]]
    m = t.member(email)
    assert m["groups"] == ["Test SCIMv2"] and m["role"] == "hr" and m["group_source"] == "scim"
    got = t.call("GET", f"/scim/v2/Users/{user['id']}").json()
    assert got["groups"][0]["display"] == "Test SCIMv2"

    r = t.send(okta, "rename_group")         # Okta's rename: a path-less value object
    assert r.status_code == 200 and r.json()["displayName"] == "Test SCIMv2"
    r = t.send(okta, "replace_members")      # one of the two ids is nobody here: left out
    assert [x["value"] for x in r.json()["members"]] == [user["id"]]
    r = t.send(okta, "replace_group")
    assert r.status_code == 200 and len(r.json()["members"]) == 1

    assert t.send(okta, "delete_group").status_code == 204
    m = t.member(email)
    assert m["groups"] == [] and m["role"] == "public"
    assert t.send(okta, "find_group").json()["totalResults"] == 0

    r = t.call("DELETE", f"/scim/v2/Users/{user['id']}")
    assert r.status_code == 204
    assert t.call("GET", f"/scim/v2/Users/{user['id']}").status_code == 404
    m = t.member(email)
    assert m["disabled"] and m["scim_deleted_at"] and m["anonymised_at"] is None

    assert [reason for _, reason in t.events()] == [
        "scim_user_created", "scim_exists", "scim_user_updated", "scim_user_deactivated",
        "scim_user_reactivated", "scim_group_created", "scim_group_updated",
        "scim_group_updated", "scim_group_updated", "scim_group_updated",
        "scim_group_deleted", "scim_user_deleted"]
    scim_rows = [e for e in t.audit() if e["actor_type"] == "scim"]
    assert [e["action"] for e in scim_rows] == [
        "scim.user.create", "scim.user.update", "scim.user.deactivate", "scim.user.reactivate",
        "scim.group.create", "scim.group.update", "scim.group.update", "scim.group.update",
        "scim.group.update", "scim.group.delete", "scim.user.delete"]
    assert {e["actor"] for e in scim_rows} == {f"scim:{scim_store.list_tokens(t.ws)[0]['id']}"}
    assert audit_store.verify(t.ws)["ok"]


# ---- Entra ID -----------------------------------------------------------------------------
def test_entra_lifecycle(tenant):
    t, entra = tenant, load("entra")
    first = f"test_user_11bb11bb-cc22-dd33-ee44-55ff55ff55ff@{t.domain}"

    r = t.send(entra, "find_user")
    assert r.json()["totalResults"] == 0
    r = t.send(entra, "create_user")
    assert r.status_code == 201, r.text
    user = r.json()
    for theirs in ("6764549bef60420686bc", "5171a35d82074e068ce2", "f648f8d5ea4e4cd38e9c"):
        t.ids[theirs] = user["id"]
    # userName isn't an email: they sign in with their work email, which is what's checked.
    assert user["userName"] == "Test_User_00aa00aa-bb11-cc22-dd33-44ee44ee44ee"
    assert user["emails"] == [{"value": first, "type": "work", "primary": True}]
    assert t.member(first)["name"] == "givenName familyName"
    assert t.send(entra, "find_user").json()["Resources"][0]["id"] == user["id"]
    assert t.send(entra, "find_user_by_external_id").json()["totalResults"] == 1

    r = t.send(entra, "update_user")         # a new work email: their account follows it
    assert r.status_code == 200, r.text
    second = f"updatedemail@{t.domain}"
    assert r.json()["emails"][0]["value"] == second
    assert r.json()["name"]["familyName"] == "updatedFamilyName"
    assert t.member(first) is None and t.member(second)["workspace"] == t.ws

    r = t.send(entra, "update_user_name")
    assert r.json()["userName"] == f"5b50642d-79fc-4410-9e90-4c077cdd1a59@{t.domain}"
    assert r.json()["emails"][0]["value"] == second      # userName isn't their email

    r = t.send(entra, "update_many")          # capitalised ops; the enterprise extension
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["displayName"], body["name"]["givenName"], body["name"]["familyName"],
            body["externalId"]) == ("Pvlo", "Gtfd", "Pkqf", "Eqpj")
    third = f"testbcwqnm@{t.domain}"
    assert body["emails"][0]["value"] == third

    r = t.send(entra, "update_many_pathless")
    body = r.json()
    assert (body["displayName"], body["name"]["givenName"], body["name"]["familyName"]) == (
        "Bjfe", "Kkom", "Unua")
    email = f"testmhvaes@{t.domain}"
    assert body["emails"][0]["value"] == email and t.member(email)["name"] == "Bjfe"

    session = a_session(email, t.ws)
    assert t.send(entra, "disable_user_string").json()["active"] is False
    assert sessions_store.get(session) is None and t.member(email)["disabled"]
    assert t.send(entra, "enable_user_string").json()["active"] is True
    assert t.send(entra, "disable_user").json()["active"] is False
    assert t.send(entra, "enable_user_string").json()["active"] is True

    r = t.send(entra, "create_group")
    assert r.status_code == 201, r.text
    group = r.json()
    t.ids["927fa2c08dcb4a7fae9e"] = group["id"]
    assert group["externalId"] == "8aa1a0c0-c4c3-4bc0-b4a5-2ef676900159"
    r = t.send(entra, "find_group")
    assert r.json()["totalResults"] == 1 and "members" not in r.json()["Resources"][0]

    assert t.send(entra, "rename_group").json()["displayName"].endswith("updatedDisplayName")
    assert scim_rules(t, [(group["displayName"], "admin"),
                          ("1879db59-3bdf-4490-ad68-ab880a269474updatedDisplayName",
                           "hr")]).status_code == 200
    r = t.send(entra, "add_member")
    assert [x["value"] for x in r.json()["members"]] == [user["id"]]
    assert t.member(email)["role"] == "hr"   # by the group's new name
    r = t.send(entra, "remove_member")       # Entra's remove: path members, value list
    assert r.json()["members"] == [] and t.member(email)["role"] == "public"
    t.send(entra, "add_member")
    r = t.send(entra, "remove_member_filter")    # someone not in it: nothing changes
    assert len(r.json()["members"]) == 1
    assert t.send(entra, "delete_group").status_code == 204
    assert t.member(email)["role"] == "public"

    session = a_session(email, t.ws)
    assert t.send(entra, "delete_user").status_code == 204
    assert sessions_store.get(session) is None and t.member(email)["disabled"]
    assert t.call("GET", f"/scim/v2/Users/{user['id']}").status_code == 404
    assert "scim.user.delete" in [e["action"] for e in t.audit()]


# ---- Auth -----------------------------------------------------------------------------------
def test_requests_without_a_valid_token_are_refused(client, tenant):
    t = tenant
    for headers in ({}, {"Authorization": t.token}, {"Authorization": "Basic " + t.token},
                    {"Authorization": "Bearer kbscim_not-a-token"},
                    {"Authorization": "Bearer " + t.h["Authorization"].split()[1]}):
        r = client.get("/scim/v2/Users", headers=headers)
        assert r.status_code == 401
        assert r.headers["content-type"].startswith(SCIM_JSON)
        assert r.headers["www-authenticate"].startswith("Bearer")
        assert r.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        assert r.json()["status"] == "401"
    assert client.post("/scim/v2/Users", json={"userName": "x@y.z"}).status_code == 401

    token_id = scim_store.list_tokens(t.ws)[0]["id"]
    assert t.call("GET", "/scim/v2/Users").status_code == 200
    r = client.delete(f"/api/admin/sign-in/scim/tokens/{token_id}", headers=t.h)
    assert r.status_code == 200 and r.json()["revoked_at"]
    assert t.call("GET", "/scim/v2/Users").status_code == 401        # revoked: at once


def test_only_admins_manage_tokens_and_a_token_is_shown_once(client, tenant):
    import auth
    t = tenant
    members_store.set_password(f"hr@{t.domain}", "Hr", auth.hash_password(PASSWORD), t.ws, "hr")
    hr = login(client, f"hr@{t.domain}")
    assert client.get("/api/admin/sign-in/scim", headers=hr).status_code == 403
    assert client.post("/api/admin/sign-in/scim/tokens", headers=hr).status_code == 403

    t.call("GET", "/scim/v2/ServiceProviderConfig")
    s = client.get("/api/admin/sign-in/scim", headers=t.h).json()
    assert s["base_url"].endswith("/scim/v2")
    assert s["last_sync_at"] and abs(s["last_sync_at"] - time.time()) < 60
    assert len(s["tokens"]) == 1 and "token" not in s["tokens"][0] and "hash" not in s["tokens"][0]
    assert s["tokens"][0]["hint"] == t.token[-4:]
    assert t.token not in json.dumps(s)

    for _ in range(scim_store.MAX_TOKENS - 1):
        assert client.post("/api/admin/sign-in/scim/tokens", headers=t.h).status_code == 200
    r = client.post("/api/admin/sign-in/scim/tokens", headers=t.h)
    assert r.status_code == 400 and "Revoke" in r.json()["detail"]
    assert {e["action"] for e in t.audit()} >= {"scim.token.create"}


def test_discovery(tenant):
    t = tenant
    config = t.call("GET", "/scim/v2/ServiceProviderConfig").json()
    assert config["patch"]["supported"] and config["filter"]["maxResults"] == 200
    assert config["authenticationSchemes"][0]["type"] == "oauthbearertoken"
    schemas = t.call("GET", "/scim/v2/Schemas").json()
    ids = [s["id"] for s in schemas["Resources"]]
    assert ids == ["urn:ietf:params:scim:schemas:core:2.0:User",
                   "urn:ietf:params:scim:schemas:core:2.0:Group"]
    assert t.call("GET", f"/scim/v2/Schemas/{ids[0]}").json()["name"] == "User"
    types = t.call("GET", "/scim/v2/ResourceTypes").json()["Resources"]
    assert [(x["name"], x["endpoint"]) for x in types] == [("User", "/Users"), ("Group", "/Groups")]
    assert t.call("GET", "/scim/v2/ResourceTypes/Nope").status_code == 404


# ---- Tenancy and the rules ------------------------------------------------------------------
def _user(t, local, **extra):
    r = t.call("POST", "/scim/v2/Users", {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": f"{local}@{t.domain}", "active": True,
        "name": {"givenName": local.title(), "familyName": "Test"}, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _group(t, name, *user_ids):
    r = t.call("POST", "/scim/v2/Groups", {"displayName": name,
                                           "members": [{"value": i} for i in user_ids]})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_token_only_reaches_its_own_workspace(client):
    a, b = Tenant(client), Tenant(client)
    alice = _user(a, "alice")
    group = _group(a, "Staff", alice["id"])

    assert a.call("GET", f"/scim/v2/Users/{alice['id']}").status_code == 200
    for method, path, body in (
            ("GET", f"/scim/v2/Users/{alice['id']}", None),
            ("PUT", f"/scim/v2/Users/{alice['id']}", {"userName": f"alice@{b.domain}"}),
            ("PATCH", f"/scim/v2/Users/{alice['id']}",
             {"Operations": [{"op": "replace", "value": {"active": False}}]}),
            ("DELETE", f"/scim/v2/Users/{alice['id']}", None),
            ("GET", f"/scim/v2/Groups/{group['id']}", None),
            ("PATCH", f"/scim/v2/Groups/{group['id']}",
             {"Operations": [{"op": "remove", "path": "members"}]}),
            ("DELETE", f"/scim/v2/Groups/{group['id']}", None)):
        assert b.call(method, path, body).status_code == 404, (method, path)
    listed = b.call("GET", "/scim/v2/Users?count=200").json()["Resources"]
    assert alice["id"] not in {u["id"] for u in listed}
    assert b.call("GET", f"/scim/v2/Users?filter=userName eq \"alice@{a.domain}\"").json()[
        "totalResults"] == 0
    assert b.call("GET", "/scim/v2/Groups").json()["totalResults"] == 0

    # B's group can't take in A's user, nor can B provision someone on A's domain.
    r = b.call("POST", "/scim/v2/Groups", {"displayName": "Staff",
                                           "members": [{"value": alice["id"]}]})
    assert r.status_code == 201 and r.json()["members"] == []
    r = b.call("POST", "/scim/v2/Users", {"userName": f"alice@{a.domain}"})
    assert r.status_code == 403 and r.json()["status"] == "403"
    alice_now = a.member(f"alice@{a.domain}")
    assert alice_now["workspace"] == a.ws and not alice_now["disabled"]
    assert len(a.call("GET", f"/scim/v2/Groups/{group['id']}").json()["members"]) == 1


def test_only_verified_domains_or_invited_addresses_are_provisioned(client, tenant):
    t = tenant
    r = t.call("POST", "/scim/v2/Users", {"userName": "someone@elsewhere.example"})
    assert r.status_code == 403
    assert t.events()[-1] == ("someone@elsewhere.example", "scim_domain")
    assert members_store.get("someone@elsewhere.example") is None

    r = client.post("/api/admin/members/invite", headers=t.h,
                    json={"email": "contractor@elsewhere.example", "role": "hr"})
    assert r.status_code == 200, r.text
    r = t.call("POST", "/scim/v2/Users", {"userName": "contractor@elsewhere.example"})
    assert r.status_code == 201, r.text
    m = members_store.get("contractor@elsewhere.example")
    assert m["workspace"] == t.ws and m["role"] == "hr"            # the invited role
    invites = client.get("/api/admin/members/invites", headers=t.h).json()
    assert "contractor@elsewhere.example" not in json.dumps(invites)  # used up

    other = Tenant(client)
    _user(other, "bob")
    r = t.call("POST", "/scim/v2/Users", {"userName": "x", "emails": [
        {"value": f"bob@{other.domain}", "primary": True}]})
    assert r.status_code == 403                    # not this workspace's domain
    r = t.call("POST", "/scim/v2/Users", {"userName": "no-email"})
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue"
    r = t.call("POST", "/scim/v2/Users", {"emails": []})
    assert r.status_code == 400


def test_someone_who_signed_in_before_scim_is_taken_over_not_duplicated(tenant):
    t = tenant
    pat = f"pat@{t.domain}"
    members_store.record_sso_login(pat, "Pat", t.ws)
    found = t.call("GET", f"/scim/v2/Users?filter=userName eq \"{pat.upper()}\"").json()
    assert found["totalResults"] == 1
    r = t.call("POST", "/scim/v2/Users", {"userName": pat, "displayName": "Pat Lee"})
    assert r.status_code == 201 and r.json()["id"] == found["Resources"][0]["id"]
    assert t.member(pat)["name"] == "Pat Lee"


def test_scim_cant_remove_the_last_admin(tenant):
    t = tenant
    alice = _user(t, "alice")
    assert scim_rules(t, [("Admins", "admin")]).status_code == 200
    admins = _group(t, "Admins", alice["id"])
    email = f"alice@{t.domain}"
    assert t.member(email)["role"] == "admin"
    members_store.set_role(t.admin, None, t.ws)      # alice is now the only admin
    assert members_store.enabled_admins(t.ws) == [email]

    patch = lambda ops: {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                         "Operations": ops}
    for method, path, body in (
            ("PATCH", f"/scim/v2/Users/{alice['id']}",
             patch([{"op": "replace", "value": {"active": False}}])),
            ("PUT", f"/scim/v2/Users/{alice['id']}", {"userName": email, "active": False}),
            ("DELETE", f"/scim/v2/Users/{alice['id']}", None),
            ("PATCH", f"/scim/v2/Groups/{admins['id']}",
             patch([{"op": "remove", "path": f"members[value eq \"{alice['id']}\"]"}])),
            ("PUT", f"/scim/v2/Groups/{admins['id']}", {"displayName": "Admins", "members": []}),
            ("PATCH", f"/scim/v2/Groups/{admins['id']}",
             patch([{"op": "replace", "path": "displayName", "value": "Former admins"}])),
            ("DELETE", f"/scim/v2/Groups/{admins['id']}", None)):
        r = t.call(method, path, body)
        assert r.status_code == 400, (method, path, r.text)
        assert r.json()["scimType"] == "mutability" and "no admin" in r.json()["detail"]
    m = t.member(email)
    assert m["role"] == "admin" and not m["disabled"] and m["scim_deleted_at"] is None
    assert t.events()[-1][1] == "scim_last_admin"
    # With another admin it goes through.
    members_store.set_role(t.admin, "admin", t.ws)
    assert t.call("DELETE", f"/scim/v2/Users/{alice['id']}").status_code == 204


def test_scim_is_one_role_source_and_its_admin_doesnt_expire(client, tenant):
    t = tenant
    alice = _user(t, "alice")
    _group(t, "Admins", alice["id"])
    email = f"alice@{t.domain}"
    assert t.member(email)["role"] == "public"       # SCIM isn't the role source yet
    r = scim_rules(t, [("admins", "admin")])
    assert r.status_code == 200 and r.json()["role_source"] == "scim"
    assert t.member(email)["role"] == "admin" and t.member(email)["groups"] == ["Admins"]

    # Long since its groups were "sent": SCIM's admin doesn't expire as a sign-in's would.
    with members_store._lock, members_store._db() as conn:
        conn.execute("UPDATE members SET groups_updated_at = 1 WHERE username = ?", (email,))
    members_store.expire_group_admins(t.ws)
    assert t.member(email)["role"] == "admin"
    assert client.get("/api/admin/members", headers=t.h).json()["members"]

    # A provider wanting rules has to take over from SCIM, which drops SCIM's roles.
    cid = members_store.create_connection(t.ws, "oidc", "Okta", "https://idp.example", "kb",
                                          {"client_secret": "s"}, {}, t.admin)["id"]
    with pytest.raises(members_store.RoleSourceTaken):
        members_store.set_group_rules(t.ws, cid, None, [("x", "hr")])
    members_store.set_group_rules(t.ws, cid, None, [("x", "hr")], make_role_source=True)
    assert members_store.role_source(t.ws) == cid
    assert t.member(email)["role"] == "public"
    # ... and SCIM taking it back needs make_role_source too.
    assert scim_rules(t, [("Admins", "admin")]).status_code == 409
    assert scim_rules(t, [("Admins", "admin")], make_role_source=True).status_code == 200
    assert t.member(email)["role"] == "admin"
    # Clearing SCIM's rules: no role source, and the roles it gave are gone.
    assert scim_rules(t, []).json()["role_source"] is None
    assert t.member(email)["role"] == "public"


def test_another_workspaces_role_source_is_untouched(client):
    """Switching role source in one workspace leaves another's SCIM roles alone."""
    a, b = Tenant(client), Tenant(client)
    for t in (a, b):
        alice = _user(t, "alice")
        _group(t, "Admins", alice["id"])
        scim_rules(t, [("Admins", "hr")])
    cid = members_store.create_connection(a.ws, "oidc", "Okta", "https://idp.example", "kb",
                                          {"client_secret": "s"}, {}, a.admin)["id"]
    members_store.set_group_rules(a.ws, cid, None, [("x", "hr")], make_role_source=True)
    assert a.member(f"alice@{a.domain}")["role"] == "public"
    assert b.member(f"alice@{b.domain}")["role"] == "hr"


# ---- Filters and pages ------------------------------------------------------------------------
def test_filters_and_pagination(tenant):
    t = tenant
    made = [_user(t, f"user{i}", externalId=f"ext-{i}") for i in range(5)]
    everyone = t.call("GET", "/scim/v2/Users").json()
    total = everyone["totalResults"]
    assert total == 6                                    # and the admin, on the same domain

    page1 = t.call("GET", "/scim/v2/Users?startIndex=1&count=2").json()
    page3 = t.call("GET", "/scim/v2/Users?startIndex=5&count=2").json()
    assert (page1["totalResults"], page1["itemsPerPage"], page1["startIndex"]) == (6, 2, 1)
    assert (page3["itemsPerPage"], page3["startIndex"]) == (2, 5)
    beyond = t.call("GET", "/scim/v2/Users?startIndex=7&count=2").json()
    assert beyond["Resources"] == [] and beyond["totalResults"] == 6
    seen = [u["id"] for start in range(1, 7, 2) for u in t.call(
        "GET", f"/scim/v2/Users?startIndex={start}&count=2").json()["Resources"]]
    assert len(seen) == len(set(seen)) == 6
    zero = t.call("GET", "/scim/v2/Users?count=0").json()
    assert zero["Resources"] == [] and zero["totalResults"] == 6
    assert t.call("GET", "/scim/v2/Users?startIndex=0&count=1").json()["startIndex"] == 1
    assert t.call("GET", "/scim/v2/Users?count=abc").status_code == 400

    r = t.call("GET", f"/scim/v2/Users?filter=userName eq \"USER3@{t.domain.upper()}\"")
    assert [u["id"] for u in r.json()["Resources"]] == [made[3]["id"]]
    r = t.call("GET", "/scim/v2/Users?filter=externalId eq \"ext-2\"")
    assert [u["id"] for u in r.json()["Resources"]] == [made[2]["id"]]
    r = t.call("GET", f"/scim/v2/Users?filter=emails[type eq \"work\"].value eq "
                      f"\"user4@{t.domain}\"")
    assert [u["id"] for u in r.json()["Resources"]] == [made[4]["id"]]
    r = t.call("GET", f"/scim/v2/Users?filter=id eq \"{made[1]['id']}\"&attributes=userName")
    assert set(r.json()["Resources"][0]) == {"id", "schemas", "meta", "userName"}
    for bad in ('userName co "user"', 'title eq "x"', 'userName eq "a" and active eq true'):
        r = t.call("GET", f"/scim/v2/Users?filter={bad}")
        assert r.status_code == 400 and r.json()["scimType"] == "invalidFilter", bad

    for i in range(3):
        _group(t, f"Team {i}", made[i]["id"])
    r = t.call("GET", "/scim/v2/Groups?startIndex=2&count=1").json()
    assert r["totalResults"] == 3 and r["Resources"][0]["displayName"] == "Team 1"
    r = t.call("GET", "/scim/v2/Groups?filter=displayName eq \"team 2\"").json()
    assert [g["displayName"] for g in r["Resources"]] == ["Team 2"]
    assert _group_dupe(t).status_code == 409


def _group_dupe(t):
    return t.call("POST", "/scim/v2/Groups", {"displayName": "TEAM 0"})


def test_bad_requests_are_scim_errors(tenant):
    t = tenant
    alice = _user(t, "alice")
    r = t.client.post("/scim/v2/Users", content=b"{not json",
                      headers={"Authorization": f"Bearer {t.token}", "Content-Type": SCIM_JSON})
    assert r.status_code == 400 and r.json()["scimType"] == "invalidSyntax"
    for body in ({"Operations": []}, {"Operations": [{"op": "move", "path": "active"}]},
                 {"Operations": [{"op": "replace", "path": "active", "value": "maybe"}]},
                 {"Operations": [{"op": "add", "path": "groups", "value": [{"value": "x"}]}]}):
        r = t.call("PATCH", f"/scim/v2/Users/{alice['id']}", body)
        assert r.status_code == 400 and r.json()["schemas"][0].endswith(":Error"), body
    assert t.call("GET", "/scim/v2/Users/nope").status_code == 404
    assert t.call("PUT", "/scim/v2/Users/nope", {"userName": "x"}).status_code == 404
    # Attributes we don't keep are taken and ignored, so a provider's sync never fails.
    r = t.call("PATCH", f"/scim/v2/Users/{alice['id']}", {"Operations": [
        {"op": "add", "path": "nickName", "value": "Babs"},
        {"op": "replace", "path": "title", "value": "Boss"}]})
    assert r.status_code == 200


# ---- Deleted, then anonymised ----------------------------------------------------------------
def test_a_deleted_user_is_anonymised_after_the_retention_window(tenant, monkeypatch):
    import conversations_store
    from kb.services import scim as service
    t = tenant
    alice = _user(t, "alice")
    email = f"alice@{t.domain}"
    chat = conversations_store.create(t.ws, email, "public", "Leave policy")
    assert t.call("DELETE", f"/scim/v2/Users/{alice['id']}").status_code == 204

    monkeypatch.setenv("SCIM_RETENTION_DAYS", "30")
    assert service.anonymise() == {"anonymised": 0}     # still inside the window
    assert t.member(email)["name"] == "Alice Test"

    monkeypatch.setenv("SCIM_RETENTION_DAYS", "0")
    assert service.anonymise()["anonymised"] >= 1
    assert t.member(email) is None
    gone = members_store.get(f"deleted-{alice['id']}")
    assert gone["workspace"] == t.ws and gone["name"] == "Deleted user" and gone["disabled"]
    assert gone["anonymised_at"] and gone["scim_username"] is None
    assert conversations_store.get(t.ws, gone["username"], chat["id"]) is not None
    assert conversations_store.get(t.ws, email, chat["id"]) is None
    assert t.events()[-1] == (gone["username"], "scim_user_anonymised")
    rows = t.audit()
    assert rows[-1]["action"] == "scim.user.anonymise" and rows[-1]["actor_type"] == "system"
    assert audit_store.verify(t.ws)["ok"]                # nothing in the log was changed

    # Provisioned again: a new user, with a new id.
    again = _user(t, "alice")
    assert again["id"] != alice["id"] and not t.member(email)["disabled"]


def test_a_deleted_user_provisioned_again_inside_the_window_is_a_new_user(tenant):
    t = tenant
    alice = _user(t, "alice")
    t.call("DELETE", f"/scim/v2/Users/{alice['id']}")
    again = _user(t, "alice")
    assert again["id"] != alice["id"]
    m = t.member(f"alice@{t.domain}")
    assert not m["disabled"] and m["scim_deleted_at"] is None
    assert t.call("GET", f"/scim/v2/Users/{alice['id']}").status_code == 404


def test_scim_events_dont_push_out_sign_in_events(tenant, monkeypatch):
    t = tenant
    monkeypatch.setattr(members_store, "EVENTS_KEPT", 3)
    members_store.log_event(t.ws, "password", t.admin, "signed_in")
    sign_ins = members_store.recent_events(t.ws)             # the admin's, too
    for i in range(5):
        _user(t, f"u{i}")
    assert members_store.recent_events(t.ws) == sign_ins
    assert len(members_store.recent_events(t.ws, scim=True)) == 3
    settings = t.client.get("/api/admin/sign-in", headers=t.h).json()
    assert {e["outcome"] for e in settings["events"]} == {"signed_in"}
    scim = t.client.get("/api/admin/sign-in/scim", headers=t.h).json()
    assert scim["events"][0]["reason_text"] == "Provisioned" and scim["users"] == 5
