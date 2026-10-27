"""
Invites (routers/invites.py, invites_store.py): an admin invites someone by
email; the link works once, until it expires or is withdrawn; an address on a
verified domain that requires single sign-on joins through the provider.
Through the real API, with the emails caught instead of sent.
"""

import time
import uuid

import pytest

import audit_store

PASSWORD = "correct-horse"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


class Outbox(list):
    def token(self, i=-1):
        """The token in the i-th email's link."""
        return self[i][2]["link"].rsplit("/invite/", 1)[1]


@pytest.fixture
def outbox(monkeypatch):
    """Every email "sent": (to, template, values)."""
    import mailer
    sent = Outbox()
    monkeypatch.setattr(mailer, "send", lambda to, template, **values:
                        sent.append((to, template, values)))
    return sent


def new_workspace():
    import auth
    import members_store
    ws = f"inv-{uuid.uuid4().hex[:8]}"
    for name, role in (("admin", "admin"), ("hr", "hr")):
        members_store.set_password(f"{name}@{ws}.example", name.title(),
                                   auth.hash_password(PASSWORD), ws, role)
    return ws


def login(client, username, password=PASSWORD):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


@pytest.fixture
def ws(client):
    ws = new_workspace()
    return ws, login(client, f"admin@{ws}.example")


def invite(client, h, email, role="public"):
    return client.post("/api/admin/members/invite", headers=h, json={"email": email,
                                                                     "role": role})


def test_an_invite_is_emailed_accepted_once_and_signs_them_in(client, ws, outbox):
    ws, h = ws
    r = invite(client, h, "Pat@Contractor.example", "hr")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending" and r.json()["email"] == "pat@contractor.example"
    (to, template, values), = outbox
    assert (to, template) == ("pat@contractor.example", "invite")
    assert values["inviter"] == "Admin" and values["role_label"] == "HR / Admin"
    assert values["link"].startswith("http://testserver/invite/")
    token = outbox.token()
    pending = client.get("/api/admin/members/invites", headers=h).json()["invites"]
    assert [i["email"] for i in pending] == ["pat@contractor.example"]
    assert "token" not in str(pending)

    info = client.post("/api/auth/invite/lookup", json={"token": token}).json()
    assert info == {"email": "pat@contractor.example", "role": "hr", "workspace_name": ws,
                    "method": "password", "expires_at": pending[0]["expires_at"]}

    assert client.post("/api/auth/invite/accept",
                       json={"token": token, "password": "short"}).status_code == 400
    r = client.post("/api/auth/invite/accept",
                    json={"token": token, "name": " Pat  Lee ", "password": "a-good-password"})
    assert r.status_code == 200, r.text
    assert r.json()["user"] == {"username": "pat@contractor.example", "name": "Pat Lee",
                                "workspace": ws, "role": "hr"}
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.json()["workspace"] == ws
    # Accepting signed them in with a password: their last sign-in is now (seats,
    # onboarding, the Members screen), as /api/auth/login's is.
    import members_store
    assert members_store.get("pat@contractor.example")["last_login_at"] > time.time() - 60
    login(client, "pat@contractor.example", "a-good-password")      # and with the password

    # Used: the link doesn't work again, and the invite has left the pending list.
    again = client.post("/api/auth/invite/accept", json={"token": token, "password": "x" * 9})
    assert again.status_code == 410 and "already been used" in again.json()["detail"]
    assert client.get("/api/admin/members/invites", headers=h).json()["invites"] == []
    actions = [(e["action"], e["actor"]) for e in reversed(audit_store.list_events(ws)[0])]
    assert actions == [("invite.create", f"admin@{ws}.example"),
                       ("invite.accept", "pat@contractor.example")]


def test_an_expired_or_revoked_invite_fails_cleanly(client, ws, outbox, monkeypatch):
    import invites_store
    ws, h = ws
    invite(client, h, "late@contractor.example")
    late = outbox.token()
    with monkeypatch.context() as later:
        later.setattr(time, "time", lambda real=time.time: real() + 8 * 86400)
        r = client.post("/api/auth/invite/lookup", json={"token": late})
        assert r.status_code == 410 and "expired" in r.json()["detail"]
        assert client.post("/api/auth/invite/accept",
                           json={"token": late, "password": "x" * 9}).status_code == 410
    assert client.get("/api/admin/members/invites",
                      headers=h).json()["invites"][0]["status"] == "pending"
    # Resend: a new link; the old one stops working.
    pending = client.get("/api/admin/members/invites", headers=h).json()["invites"]
    assert client.post(f"/api/admin/members/invites/{pending[0]['id']}/resend",
                       headers=h).status_code == 200
    assert client.post("/api/auth/invite/lookup", json={"token": late}).status_code == 404
    assert client.post("/api/auth/invite/lookup", json={"token": outbox.token()}).status_code == 200

    invite(client, h, "gone@contractor.example")
    gone = outbox.token()
    iid = invites_store.find(gone)["id"]
    assert client.delete(f"/api/admin/members/invites/{iid}", headers=h).status_code == 200
    r = client.post("/api/auth/invite/accept", json={"token": gone, "password": "x" * 9})
    assert r.status_code == 410 and "withdrawn" in r.json()["detail"]
    assert client.delete(f"/api/admin/members/invites/{iid}", headers=h).status_code == 404
    assert client.post("/api/auth/invite/accept",
                       json={"token": "made-up", "password": "x" * 9}).status_code == 404
    import members_store
    assert members_store.get("gone@contractor.example") is None


def test_an_invite_cant_be_accepted_twice_at_once(ws, outbox, client):
    import invites_store
    ws, h = ws
    invite(client, h, "twice@contractor.example")
    token = outbox.token()
    invites_store.accept(token, "Twice", "hash")
    with pytest.raises(invites_store.InviteError) as e:
        invites_store.accept(token, "Twice", "hash")
    assert e.value.reason == "accepted"


def test_a_verified_domain_that_requires_sso_joins_through_the_provider(client, ws, outbox):
    import members_store
    ws, h = ws
    domain = f"{ws}.example"
    members_store.add_verified_domain(ws, domain)
    members_store.set_requires_sso(ws, True)
    assert invite(client, h, f"sam@{domain}", "hr").status_code == 200
    token = outbox.token()
    assert client.post("/api/auth/invite/lookup",
                       json={"token": token}).json()["method"] == "sso"
    # No password is set, even if one is sent.
    r = client.post("/api/auth/invite/accept", json={"token": token, "password": "x" * 12})
    assert r.json() == {"sso": True, "email": f"sam@{domain}"}
    sam = members_store.get(f"sam@{domain}")
    assert (sam["workspace"], sam["role"], sam["password_hash"]) == (ws, "hr", None)
    # The provider's sign-in finds them with the invited role.
    import sso
    assert sso.access_for(f"sam@{domain}") == {"workspace": ws, "role": "hr"}
    # Without "require SSO", someone on the domain may set a password instead.
    members_store.set_requires_sso(ws, False)
    invite(client, h, f"kim@{domain}")
    assert client.post("/api/auth/invite/lookup",
                       json={"token": outbox.token()}).json()["method"] == "password"


def test_who_can_be_invited(client, ws, outbox, monkeypatch):
    import auth
    import members_store
    ws, h = ws
    other = new_workspace()
    members_store.add_verified_domain(other, f"{other}.example")
    assert invite(client, h, "not-an-email").status_code == 400
    assert invite(client, h, f"admin@{ws}.example").status_code == 409        # a member here
    r = invite(client, h, f"admin@{other}.example")                         # a member there
    assert r.status_code == 409 and "another workspace" in r.json()["detail"]
    r = invite(client, h, f"new@{other}.example")                 # their verified domain
    assert r.status_code == 409 and "another workspace" in r.json()["detail"]
    assert invite(client, h, "dup@contractor.example").status_code == 200
    r = invite(client, h, "dup@contractor.example")
    assert r.status_code == 409 and "Resend" in r.json()["detail"]
    assert invite(client, h, "bad@contractor.example", "owner").status_code == 422
    assert len(outbox) == 1
    # With password sign-in off (PASSWORD_LOGIN=off), only SSO-required domains.
    from kb.routers import invites
    members_store.add_verified_domain(ws, f"{ws}.example")
    members_store.set_requires_sso(ws, True)
    monkeypatch.setattr(auth, "password_login_on", lambda: False)
    assert invites.method_for(ws, "nopw@contractor.example") is None
    assert invites.method_for(ws, f"yes@{ws}.example") == "sso"
    monkeypatch.setattr(invites, "method_for", lambda ws, email: None)
    monkeypatch.setattr(auth, "password_login_on", lambda: True)   # for the admin's session
    assert invite(client, h, "nopw@contractor.example").status_code == 400


def test_only_admins_invite_and_only_their_own_workspaces_invites(client, ws, outbox):
    import invites_store
    ws, h = ws
    hr = login(client, f"hr@{ws}.example")
    assert invite(client, hr, "x@contractor.example").status_code == 403
    assert client.get("/api/admin/members/invites", headers=hr).status_code == 403
    invite(client, h, "mine@contractor.example")
    iid = invites_store.find(outbox.token())["id"]
    other = new_workspace()
    them = login(client, f"admin@{other}.example")
    assert client.get("/api/admin/members/invites", headers=them).json()["invites"] == []
    assert client.delete(f"/api/admin/members/invites/{iid}", headers=them).status_code == 404
    assert client.post(f"/api/admin/members/invites/{iid}/resend",
                       headers=them).status_code == 404
    assert invites_store.find(outbox.token())["status"] == "pending"


def test_an_invite_whose_email_fails_isnt_kept(client, ws, monkeypatch):
    import mailer
    ws, h = ws

    def fail(*args, **kwargs):
        raise mailer.MailError("The email couldn't be sent (smtp: ConnectionRefusedError).")
    monkeypatch.setattr(mailer, "send", fail)
    r = invite(client, h, "lost@contractor.example")
    assert r.status_code == 502 and "MAIL_BACKEND" in r.json()["detail"]
    assert client.get("/api/admin/members/invites", headers=h).json()["invites"] == []
    assert audit_store.list_events(ws, action="invite.create")[0] == []


def test_sending_invites_is_rate_limited(client, ws, outbox, monkeypatch):
    from kb import state
    ws, h = ws
    monkeypatch.setenv("RATE_LIMITS", "on")
    monkeypatch.setenv("RATE_LIMIT_INVITE_USER", "2")
    monkeypatch.setattr(state, "hits", {})
    assert invite(client, h, "a@contractor.example").status_code == 200
    assert invite(client, h, "b@contractor.example").status_code == 200
    r = invite(client, h, "c@contractor.example")
    assert r.status_code == 429 and "sent 2 invites" in r.json()["detail"]
