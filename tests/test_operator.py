"""
The platform operator console (kb/operators.py, kb/routers/operator.py; platform.md
PL-18): operators sign in only through single sign-on with an address in
OPERATOR_EMAILS, their sessions are good nowhere else (and members' are not good
here), support can open a workspace only while its admin allows it, and every
request made as support is in the workspace's audit log as actor_type operator.
The identity provider is faked; everything else is the real API.
"""

import uuid

import pytest
from fastapi.responses import RedirectResponse

import audit_store
import lifecycle_store

PASSWORD = "correct-horse"
OPERATOR = "op@cloudastra.example"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


@pytest.fixture
def idp(monkeypatch):
    """Google, faked: whoever the test says signs in, with MFA if it says so."""
    import sso
    from kb.routers import operator
    who = {"email": OPERATOR, "amr": ["pwd", "mfa"]}

    class Client:
        async def authorize_redirect(self, request, redirect_uri, **kwargs):
            return RedirectResponse("https://accounts.example/auth")

    async def answer(name, c, request):
        email = who["email"]
        return {"email": email, "email_verified": True, "hd": email.split("@")[1],
                "name": "Op", "amr": who["amr"]}, "id-token"

    monkeypatch.setattr(sso, "configured", lambda: ["google"])
    monkeypatch.setattr(sso, "client", lambda provider: Client())
    monkeypatch.setattr(operator, "provider_answer", answer)
    monkeypatch.setenv("OPERATOR_EMAILS", f"{OPERATOR}, other-op@cloudastra.example")
    return who


def operator_sign_in(client):
    """The whole round trip: start, the provider's callback, the code -> a token."""
    assert client.get("/api/operator/sso/google/start").status_code == 307
    r = client.get("/api/operator/sso/google/callback")
    assert r.status_code == 303
    fragment = r.headers["location"].split("#", 1)[1]
    key, _, value = fragment.partition("=")
    if key == "error":
        return value
    r = client.post("/api/operator/auth/exchange", json={"code": value})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def new_workspace():
    import auth
    import members_store
    ws = f"opr-{uuid.uuid4().hex[:8]}"
    for name, role in (("admin", "admin"), ("staff", "public")):
        members_store.set_password(f"{name}@{ws}.example", name.title(),
                                   auth.hash_password(PASSWORD), ws, role)
    return ws


def login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


# ---- Operators sign in -------------------------------------------------------------------
def test_an_operator_signs_in_with_sso_and_sees_every_workspace(client, idp):
    ws = new_workspace()
    h = operator_sign_in(client)
    assert client.get("/api/operator/me", headers=h).json()["email"] == OPERATOR
    r = client.get("/api/operator/workspaces", headers=h)
    assert r.status_code == 200
    row = next(w for w in r.json()["workspaces"] if w["slug"] == ws)
    assert row["members"] == 2 and row["status"] == "active" and row["plan"] is None
    assert {"seats", "usage", "last_activity", "connector_errors", "index"} <= set(row)
    signed_in = [e for e in audit_store.list_events(audit_store.PLATFORM)[0]
                 if e["action"] == "operator.sign_in"]
    assert signed_in[0]["actor"] == OPERATOR and signed_in[0]["actor_type"] == "operator"


def test_someone_not_in_operator_emails_is_refused(client, idp):
    idp["email"] = "someone@cloudastra.example"
    assert operator_sign_in(client) == "not_operator"


def test_mfa_can_be_required(client, idp, monkeypatch):
    monkeypatch.setenv("OPERATOR_REQUIRE_MFA", "on")
    idp["amr"] = ["pwd"]
    assert operator_sign_in(client) == "mfa_required"
    idp["amr"] = ["pwd", "otp"]
    assert isinstance(operator_sign_in(client), dict)


def test_sessions_dont_cross_between_operators_and_members(client, idp, monkeypatch):
    ws = new_workspace()
    member = login(client, f"admin@{ws}.example")
    assert client.get("/api/operator/workspaces", headers=member).status_code == 401
    op = operator_sign_in(client)
    assert client.get("/api/auth/me", headers=op).status_code == 401
    # An operator's one-time code can't be swapped for a member's session.
    client.get("/api/operator/sso/google/start")
    code = client.get("/api/operator/sso/google/callback").headers["location"].split("code=")[1]
    assert client.post("/api/auth/sso/exchange", json={"code": code}).status_code == 401
    # Taken off the list: out at once.
    monkeypatch.setenv("OPERATOR_EMAILS", "other-op@cloudastra.example")
    assert client.get("/api/operator/workspaces", headers=op).status_code == 401


def test_no_password_way_in(client, idp):
    """Operators have no password: the sign-in endpoints are single sign-on only."""
    assert client.post("/api/auth/login", json={"username": OPERATOR,
                                                "password": PASSWORD}).status_code == 401


# ---- Operator actions ------------------------------------------------------------------
def test_suspend_and_unsuspend(client, idp):
    ws = new_workspace()
    member = login(client, f"admin@{ws}.example")
    op = operator_sign_in(client)
    r = client.post(f"/api/operator/workspaces/{ws}/suspend", headers=op,
                    json={"reason": "unpaid invoice"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=member).status_code == 401
    assert lifecycle_store.state(ws)["suspended_reason"] == "unpaid invoice"
    (e,) = [e for e in audit_store.list_events(ws)[0] if e["action"] == "operator.suspend"]
    assert (e["actor"], e["actor_type"]) == (OPERATOR, "operator")
    assert client.post(f"/api/operator/workspaces/{ws}/unsuspend", headers=op).status_code == 200
    login(client, f"admin@{ws}.example")
    assert client.post("/api/operator/workspaces/nope-nope/suspend", headers=op,
                       json={"reason": "x"}).status_code == 404


def test_reindex_reset_rate_limits_and_audit(client, idp, monkeypatch):
    from kb import state
    from kb.services import indexing
    asked = []
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, **kw: asked.append((ws, kw)))
    ws = new_workspace()
    op = operator_sign_in(client)
    assert client.post(f"/api/operator/workspaces/{ws}/reindex", headers=op).status_code == 200
    assert asked == [(ws, {"full": True})]
    state.hits[("chat", "workspace", ws)] = [1.0]
    state.hits[("chat", "user", f"staff@{ws}.example")] = [1.0]
    state.hits[("chat", "workspace", "someone-else")] = [1.0]
    r = client.post(f"/api/operator/workspaces/{ws}/reset-rate-limits", headers=op)
    assert r.json()["counters"] == 2 and ("chat", "workspace", "someone-else") in state.hits
    r = client.get(f"/api/operator/workspaces/{ws}/audit", headers=op)
    assert r.status_code == 200 and r.json()["verify"]["ok"]
    actions = [e["action"] for e in audit_store.list_events(ws)[0]]
    assert actions[:3] == ["operator.audit_view", "operator.reset_rate_limits",
                           "operator.reindex"]
    platform = client.get("/api/operator/audit", headers=op).json()["events"]
    assert {"operator.reindex", "operator.reset_rate_limits"} <= {e["action"] for e in platform}


# ---- Support access ---------------------------------------------------------------------
def support_session(client, op, ws):
    r = client.post(f"/api/operator/workspaces/{ws}/support-session", headers=op)
    if r.status_code != 200:
        return r.status_code
    r = client.post("/api/auth/sso/exchange", json={"code": r.json()["code"]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def test_support_only_with_the_admins_grant_and_every_call_is_audited(client, idp):
    ws = new_workspace()
    admin = login(client, f"admin@{ws}.example")
    op = operator_sign_in(client)
    assert support_session(client, op, ws) == 403                  # not allowed yet
    r = client.put("/api/admin/workspace/support-access", headers=admin, json={"hours": 72})
    assert r.status_code == 200 and r.json()["support_until"]
    assert client.put("/api/admin/workspace/support-access", headers=admin,
                      json={"hours": 1000}).status_code == 400
    support = support_session(client, op, ws)

    before = len(audit_store.list_events(ws, limit=200)[0])
    calls = [("GET", "/api/auth/me"), ("GET", "/api/admin/members"),
             ("GET", "/api/admin/workspace"), ("PATCH", f"/api/admin/members/staff@{ws}.example")]
    for method, path in calls:
        kwargs = {"json": {"role": "hr"}} if method == "PATCH" else {}
        assert client.request(method, path, headers=support, **kwargs).status_code == 200
    new = audit_store.list_events(ws, limit=200)[0][:len(audit_store.list_events(
        ws, limit=200)[0]) - before]
    requests = [e for e in new if e["action"] == "support.request"]
    assert sorted(e["target_id"] for e in requests) == sorted(f"{m} {p}" for m, p in calls)
    assert all((e["actor"], e["actor_type"]) == (OPERATOR, "operator") for e in new)
    (change,) = [e for e in new if e["action"] == "member.update"]
    assert change["actor_type"] == "operator"
    me = client.get("/api/auth/me", headers=support).json()
    assert me["impersonator"] == OPERATOR and me["role"] == "admin"

    # Support can't delete the workspace, grant itself time, export or erase anyone.
    assert client.request("DELETE", "/api/admin/workspace", headers=support,
                          json={"confirm": ws}).status_code == 403
    assert client.put("/api/admin/workspace/support-access", headers=support,
                      json={"hours": 168}).status_code == 403
    assert client.post("/api/admin/workspace/export", headers=support).status_code == 403
    assert client.post(f"/api/admin/members/staff@{ws}.example/erase", headers=support,
                       json={"confirm": f"staff@{ws}.example"}).status_code == 403

    # The admin takes it back: support is out at its next request.
    assert client.request("DELETE", "/api/admin/workspace/support-access",
                          headers=admin).status_code == 200
    assert client.get("/api/admin/members", headers=support).status_code == 401
    assert support_session(client, op, ws) == 403
    assert audit_store.verify(ws)["ok"]


def test_support_ends_when_the_grant_does(client, idp):
    ws = new_workspace()
    admin = login(client, f"admin@{ws}.example")
    op = operator_sign_in(client)
    client.put("/api/admin/workspace/support-access", headers=admin, json={"hours": 24})
    support = support_session(client, op, ws)
    import db
    with db.connect() as conn:
        conn.execute("UPDATE workspaces SET support_until = 1 WHERE slug = ?", (ws,))
    assert client.get("/api/auth/me", headers=support).status_code == 401
