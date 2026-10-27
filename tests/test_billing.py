"""
Plans, seats and billing (kb/services/billing.py, kb/routers/billing.py; platform.md
PL-16) and the trial (PL-17), through the real API. Stripe is never called: its
answers are recorded fixtures (tests/fixtures/stripe/) served by an httpx
MockTransport, and webhooks are signed here the way Stripe signs them.

With BILLING off (the default) nothing changes: that's checked here, and a slice of
the existing tests runs with it on as well (test_billing_off_or_on.py).
"""

import json
import pathlib
import time
import uuid

import httpx
import pytest

import audit_store
import billing_store
import lifecycle_store
import question_log_store

PASSWORD = "correct-horse"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "stripe"
# Built here so no secret-shaped string sits in the source.
SECRET_KEY = "sk_" + "test_" + "billingtests0000"
WEBHOOK_SECRET = "whsec_" + "billingtests"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


@pytest.fixture(autouse=True)
def nothing_indexed(monkeypatch):
    from kb.services import indexing
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, **kwargs: None)


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("BILLING", "on")


@pytest.fixture
def stripe(monkeypatch):
    """Stripe in test mode, answering from the recorded fixtures. `calls`: what was sent."""
    from kb.services import stripe_api
    monkeypatch.setenv("STRIPE_SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_BUSINESS", "price_test_business")
    monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_test_starter")
    calls = []

    def answer(request):
        calls.append((request.url.path, dict(httpx.QueryParams(request.content.decode())),
                      request.headers.get("authorization")))
        name = {"/v1/checkout/sessions": "checkout_session.json",
                "/v1/billing_portal/sessions": "portal_session.json"}[request.url.path]
        return httpx.Response(200, json=json.loads((FIXTURES / name).read_text()))

    monkeypatch.setattr(stripe_api, "TRANSPORT", httpx.MockTransport(answer))
    return calls


class Org:
    """A workspace made at sign-up: its founder (the billing owner), another admin,
    and a member, all with passwords."""

    def __init__(self, client):
        import auth
        import members_store
        from kb.services import billing
        self.client = client
        tag = uuid.uuid4().hex[:8]
        self.domain = f"bill-{tag}.example"
        self.ws = members_store.create_workspace(f"Bill {tag}", self.domain,
                                                 f"founder@{self.domain}", "Fran")
        billing.founded(self.ws, f"founder@{self.domain}")
        # As if billing was on when it signed up (fixtures may turn it on after this).
        billing_store.start_trial(self.ws, "trial", time.time() + 14 * 86400)
        for name, role in (("founder", "admin"), ("admin", "admin"), ("staff", "public")):
            members_store.set_password(f"{name}@{self.domain}", name.title(),
                                       auth.hash_password(PASSWORD), self.ws, role)

    def login(self, who="founder"):
        r = self.client.post("/api/auth/login", json={"username": f"{who}@{self.domain}",
                                                      "password": PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}

    def plan(self, plan_id, **fields):
        billing_store.save_subscription(self.ws, plan=plan_id, **{"status": "active", **fields})


@pytest.fixture
def org(client):
    return Org(client)


def event(name, ws, **changes):
    data = json.loads((FIXTURES / name).read_text().replace("__WORKSPACE__", ws))
    data["id"] = f"{data['id']}_{uuid.uuid4().hex[:8]}"
    data.update(changes)
    return json.dumps(data).encode()


def post_webhook(client, payload, secret=WEBHOOK_SECRET, stamp=None):
    from kb.services import stripe_api
    return client.post("/api/billing/webhook", content=payload,
                       headers={"Stripe-Signature": stripe_api.sign(payload, secret, stamp),
                                "Content-Type": "application/json"})


# ---- With BILLING off, nothing changes --------------------------------------------------
def test_with_billing_off_nothing_is_limited_or_shown(client, org, stripe):
    from kb import quotas
    from kb.services import billing
    # Even a workspace whose trial has long ended (from when billing was on).
    billing_store.save_subscription(org.ws, plan="starter", status="trialing",
                                    trial_ends_at=time.time() - 90 * 86400)
    h = org.login()
    assert client.get("/api/billing", headers=h).json() == {"on": False}
    assert client.get("/api/auth/me", headers=h).json()["billing"] is None
    assert client.post("/api/billing/checkout", headers=h, json={"plan": "business"}
                       ).status_code == 404
    assert billing.entitlement(org.ws, "scim") and not billing.read_only(org.ws)
    quotas.check(org.ws, "chat")                                  # no refusal
    assert client.get("/api/admin/audit/export", headers=h).status_code == 200
    assert client.post("/api/admin/sign-in/scim/tokens", headers=h).status_code == 200
    assert client.post("/api/me/api-keys", headers=h,
                       json={"name": "k", "scopes": ["ask"]}).status_code == 200
    assert client.post("/api/docs", headers=h, json={"name": "a.txt", "access": "public",
                                                     "content": "Hello."}).status_code == 200


# ---- Entitlements ------------------------------------------------------------------------
def test_a_trial_includes_what_its_plan_does(client, org, billing_on):
    h = org.login()
    me = client.get("/api/auth/me", headers=h).json()["billing"]
    assert me["plan"] == "Trial" and me["status"] == "trialing" and me["owner"] is True
    assert 13 <= me["trial_days_left"] <= 14 and me["read_only"] is False
    # The trial plan: audit export and the API yes, SCIM no.
    assert client.get("/api/admin/audit/export", headers=h).status_code == 200
    r = client.post("/api/admin/sign-in/scim/tokens", headers=h)
    assert r.status_code == 402
    assert "doesn't include SCIM provisioning" in r.json()["detail"]
    assert "Plan & billing" in r.json()["detail"]


def test_each_gated_feature_follows_the_plan(client, org, billing_on):
    h = org.login()
    org.plan("business")
    key = client.post("/api/me/api-keys", headers=h,
                      json={"name": "k", "scopes": ["docs:read"]}).json()["key"]
    bearer = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/v1/documents", headers=bearer).status_code == 200

    org.plan("starter")             # Slack only
    refused = {
        "audit export": client.get("/api/admin/audit/export", headers=h),
        "scim token": client.post("/api/admin/sign-in/scim/tokens", headers=h),
        "api key": client.post("/api/me/api-keys", headers=h,
                               json={"name": "k2", "scopes": ["ask"]}),
        "service key": client.post("/api/admin/api/service-keys", headers=h,
                                   json={"name": "svc", "scopes": ["search"]}),
        "turn the api on": client.put("/api/admin/api", headers=h, json={"api_enabled": True}),
        "turn mcp on": client.put("/api/admin/api", headers=h, json={"mcp_enabled": True}),
        "teams": client.put("/api/admin/teams/app", headers=h, json={"mode": "central"}),
        "oidc provider": client.post("/api/admin/sign-in/connections", headers=h,
                                     json={"label": "Okta", "issuer": "https://x.okta.com",
                                           "client_id": "a", "client_secret": "b"}),
        "a key already made": client.get("/api/v1/documents", headers=bearer),
    }
    for what, r in refused.items():
        assert r.status_code == 402, (what, r.status_code, r.text)
        assert "Starter" in r.json()["detail"], what
    # Turning a feature OFF is never refused.
    assert client.put("/api/admin/api", headers=h, json={"api_enabled": False}
                      ).status_code == 200
    org.plan("enterprise")
    assert client.post("/api/admin/sign-in/scim/tokens", headers=h).status_code == 200


def test_slack_and_teams_answer_with_the_plans_refusal(org, billing_on):
    from fastapi import HTTPException
    from kb.services import billing
    org.plan("starter")
    billing.require(org.ws, "slack")
    with pytest.raises(HTTPException) as e:
        billing.require(org.ws, "teams")
    assert e.value.status_code == 402 and "Microsoft Teams app" in e.value.detail


def test_a_workspace_without_a_subscription_is_never_limited(client, billing_on):
    """Made before billing (or by the operator): nothing is locked when billing is
    turned on."""
    import auth
    import members_store
    ws = f"old-{uuid.uuid4().hex[:8]}"
    members_store.set_password(f"admin@{ws}.example", "A", auth.hash_password(PASSWORD), ws,
                               "admin")
    r = client.post("/api/auth/login", json={"username": f"admin@{ws}.example",
                                             "password": PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}
    assert client.post("/api/admin/sign-in/scim/tokens", headers=h).status_code == 200
    view = client.get("/api/billing", headers=h).json()
    assert view["on"] is True and view["managed"] is False and view["plan"] is None
    assert view["owners"] == [] and view["is_owner"] is False


# ---- Limits ------------------------------------------------------------------------------
@pytest.fixture
def tiny(org):
    """A plan of 2 questions a month, 1 document and 1 connector."""
    conn = __import__("db").connect()
    with conn:
        conn.execute("INSERT OR IGNORE INTO plans (id, name, seat_price, buyable, sort, limits, "
                     "updated_at) VALUES ('tiny', 'Tiny', 100, 0, 9, ?, ?)",
                     (json.dumps({"questions_month": 2, "docs": 1, "storage_mb": 1,
                                  "connectors": 1}), time.time()))
    conn.close()
    org.plan("tiny")
    return org


def test_limits_are_explained_where_they_block(client, tiny, billing_on):
    from fastapi import HTTPException
    from kb import quotas
    org = tiny
    h = org.login()
    # Documents: one is fine, a second is over the plan.
    files = {"files": ("one.txt", b"First.", "text/plain")}
    assert client.post("/api/docs/upload", headers=h, files=files).status_code == 200
    r = client.post("/api/docs/upload", headers=h,
                    files={"files": ("two.txt", b"Second.", "text/plain")})
    assert r.status_code == 402
    assert r.json()["detail"].startswith("Your plan (Tiny) includes 1 document,")
    # Replacing the one it has isn't a new document.
    assert client.post("/api/docs/upload", headers=h, files=files).status_code == 200

    # Connectors: the plan has room for one.
    conn = __import__("db").connect()
    with conn:
        conn.execute("INSERT INTO integrations (workspace, provider, status) VALUES (?, ?, ?)",
                     (org.ws, "notion", "connected"))
    conn.close()
    with pytest.raises(HTTPException) as e:
        quotas.check(org.ws, "connector", provider="google_drive")
    assert "Your plan (Tiny) includes 1 connector, and 1 is connected" in e.value.detail
    quotas.check(org.ws, "connector", provider="notion")          # reconnecting it is fine

    # Questions: two a month, from any surface (every one goes through user_limit).
    for _ in range(2):
        question_log_store.add(org.ws, f"founder@{org.domain}", "web", "answered",
                               text_mode="metadata")
    r = client.post("/api/chat", headers=h, json={"question": "Anything?"})
    assert r.status_code == 402
    assert "includes 2 questions a month" in r.json()["detail"]


# ---- Seats -------------------------------------------------------------------------------
def test_a_seat_is_someone_active_in_the_last_30_days(org, billing_on):
    import auth
    import db
    import members_store
    from kb.services import billing
    now = time.time()
    for name in ("asker", "dormant", "gone", "off"):
        members_store.set_password(f"{name}@{org.domain}", name, auth.hash_password(PASSWORD),
                                   org.ws, "public")
    conn = db.connect()
    with conn:
        set_login = "UPDATE members SET last_login_at = ? WHERE username = ?"
        conn.execute(set_login, (now - 2 * 86400, f"founder@{org.domain}"))      # a seat
        conn.execute(set_login, (now - 40 * 86400, f"dormant@{org.domain}"))     # too long ago
        conn.execute(set_login, (now - 86400, f"off@{org.domain}"))              # turned off
        conn.execute("UPDATE members SET disabled = 1 WHERE username = ?", (f"off@{org.domain}",))
        conn.execute("UPDATE members SET last_login_at = NULL WHERE workspace = ? AND username "
                     "NOT IN (?, ?, ?)", (org.ws, f"founder@{org.domain}",
                                          f"dormant@{org.domain}", f"off@{org.domain}"))
    conn.close()
    # Never signed in, but asked from Slack last week: a seat.
    question_log_store.add(org.ws, f"asker@{org.domain}", "slack", "answered",
                           text_mode="metadata", at=now - 7 * 86400)
    question_log_store.add(org.ws, f"gone@{org.domain}", "web", "answered",
                           text_mode="metadata", at=now - 45 * 86400)
    assert billing.seats(org.ws, now) == 2


# ---- Owners ------------------------------------------------------------------------------
def test_only_an_owner_changes_billing(client, org, billing_on, stripe):
    founder, admin = org.login(), org.login("admin")
    view = client.get("/api/billing", headers=admin).json()
    assert view["is_owner"] is False and [o["username"] for o in view["owners"]] == [
        f"founder@{org.domain}"]
    assert client.get("/api/billing", headers=org.login("staff")).status_code == 403
    for r in (client.post("/api/billing/checkout", headers=admin, json={"plan": "business"}),
              client.post("/api/billing/portal", headers=admin),
              client.post("/api/billing/owners", headers=admin,
                          json={"username": f"admin@{org.domain}"})):
        assert r.status_code == 403 and "Only an owner" in r.json()["detail"]
    assert stripe == []                                  # Stripe was never called
    # The founder makes the admin an owner too; then the founder can step down.
    assert client.post("/api/billing/owners", headers=founder,
                       json={"username": f"admin@{org.domain}"}).status_code == 200
    assert client.post("/api/billing/checkout", headers=admin,
                       json={"plan": "business"}).status_code == 200
    assert client.delete(f"/api/billing/owners/founder@{org.domain}",
                         headers=admin).status_code == 200
    assert client.delete(f"/api/billing/owners/admin@{org.domain}",
                         headers=admin).status_code == 409          # the last one stays
    # Someone outside the workspace can't be made an owner.
    assert client.post("/api/billing/owners", headers=admin,
                       json={"username": "someone@else.example"}).status_code == 404


# ---- Stripe Checkout and the portal ------------------------------------------------------
def test_checkout_creates_a_stripe_session_for_the_seats_in_use(client, org, billing_on,
                                                                  stripe):
    h = org.login()
    r = client.post("/api/billing/checkout", headers=h, json={"plan": "business"})
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://checkout.stripe.com/")
    path, form, auth_header = stripe[0]
    assert path == "/v1/checkout/sessions" and auth_header == f"Bearer {SECRET_KEY}"
    assert form["mode"] == "subscription" and form["client_reference_id"] == org.ws
    assert form["line_items[0][price]"] == "price_test_business"
    assert form["line_items[0][quantity]"] == "1"           # the founder, signed in just now
    assert form["metadata[plan]"] == "business"
    assert form["subscription_data[metadata][workspace]"] == org.ws
    assert form["customer_email"] == f"founder@{org.domain}"
    assert form["success_url"].endswith("/billing?checkout=done")
    events = audit_store.list_events(org.ws, limit=20)[0]
    assert any(e["action"] == "billing.checkout" for e in events)
    # A plan with no price, or one that isn't sold, isn't offered.
    assert client.post("/api/billing/checkout", headers=h, json={"plan": "enterprise"}
                       ).status_code == 409
    assert client.post("/api/billing/checkout", headers=h, json={"plan": "trial"}
                       ).status_code == 404
    # No subscription at Stripe yet: nothing to manage in the portal.
    assert client.post("/api/billing/portal", headers=h).status_code == 409


def test_a_live_stripe_key_is_refused_unless_allowed(client, org, billing_on, stripe,
                                                       monkeypatch):
    from kb.services import stripe_api
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_" + "live_" + "notallowed")
    assert not stripe_api.configured() and stripe_api.live_key_refused()
    r = client.post("/api/billing/checkout", headers=org.login(), json={"plan": "business"})
    assert r.status_code == 503 and stripe == []


# ---- The webhook -------------------------------------------------------------------------
def test_a_signed_webhook_moves_the_subscription_along(client, org, billing_on, stripe):
    h = org.login()
    r = post_webhook(client, event("event_checkout_completed.json", org.ws))
    assert r.status_code == 200, r.text
    sub = billing_store.subscription(org.ws)
    assert (sub["plan"], sub["status"], sub["stripe_customer"]) == (
        "business", "active", "cus_Rtest0001")
    assert client.get("/api/auth/me", headers=h).json()["billing"]["plan"] == "Business"
    # Now the portal opens, for the customer Stripe made.
    r = client.post("/api/billing/portal", headers=h)
    assert r.status_code == 200 and r.json()["url"].startswith("https://billing.stripe.com/")
    assert stripe[-1][1]["customer"] == "cus_Rtest0001"

    # A failed payment: past due, still working.
    post_webhook(client, event("event_subscription_updated.json", org.ws))
    sub = billing_store.subscription(org.ws)
    assert (sub["status"], sub["seats"], sub["plan"]) == ("past_due", 7, "business")
    assert client.get("/api/billing", headers=h).json()["read_only"] is False
    # Canceled: read-only.
    post_webhook(client, event("event_subscription_deleted.json", org.ws))
    assert billing_store.subscription(org.ws)["status"] == "canceled"
    r = client.post("/api/docs", headers=h, json={"name": "b.txt", "access": "public",
                                                  "content": "x"})
    assert r.status_code == 402 and "canceled" in r.json()["detail"]
    actions = [e for e in audit_store.list_events(org.ws, limit=50)[0]
               if e["action"] == "billing.stripe_event"]
    assert len(actions) == 3 and actions[0]["actor"] == "stripe"


def test_a_webhook_with_a_bad_signature_changes_nothing(client, org, billing_on, stripe):
    before = billing_store.subscription(org.ws)
    payload = event("event_checkout_completed.json", org.ws)
    assert post_webhook(client, payload, secret="whsec_" + "wrong").status_code == 400
    assert post_webhook(client, payload, stamp=time.time() - 3600).status_code == 400
    assert client.post("/api/billing/webhook", content=payload).status_code == 400
    tampered = payload.replace(b'"business"', b'"enterprise"')
    from kb.services import stripe_api
    r = client.post("/api/billing/webhook", content=tampered,
                    headers={"Stripe-Signature": stripe_api.sign(payload, WEBHOOK_SECRET)})
    assert r.status_code == 400
    assert billing_store.subscription(org.ws) == before


def test_each_webhook_event_is_taken_once(client, org, billing_on, stripe):
    payload = event("event_checkout_completed.json", org.ws)
    assert post_webhook(client, payload).json() == {"received": True}
    billing_store.save_subscription(org.ws, plan="starter")     # changed since
    assert post_webhook(client, payload).json()["duplicate"] is True
    assert billing_store.subscription(org.ws)["plan"] == "starter"


# ---- Trial: reminders, read-only, then deletion ------------------------------------------
def test_an_ended_trial_is_read_only_then_deleted_through_the_lifecycle(client, org, billing_on,
                                                                         monkeypatch):
    import mailer
    from kb.services import billing
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, template, **v: sent.append((to, v["title"])))
    # Only this workspace's trial (the database is shared with other tests).
    monkeypatch.setattr(billing_store, "subscriptions", lambda: [billing_store.subscription(org.ws)])
    h = org.login()
    ends = billing_store.subscription(org.ws)["trial_ends_at"]

    # Reminders: once at 7 days, once at 3 and 1 even if the job was late for 3.
    billing.check_trials(now=ends - 6.5 * 86400)
    billing.check_trials(now=ends - 6 * 86400)
    assert {t for _, t in sent} == {f"Bill {org.ws.split('-', 1)[1]}: 7 days left in your trial"}
    billing.check_trials(now=ends - 0.5 * 86400)
    assert len({t for _, t in sent}) == 2 and "1 day left" in sent[-1][1]
    assert {to for to, _ in sent} == {f"founder@{org.domain}", f"admin@{org.domain}"}

    # It ends: read-only, not deleted.
    billing_store.save_subscription(org.ws, trial_ends_at=time.time() - 60)
    billing.check_trials()
    assert "trial has ended" in sent[-1][1]
    me = client.get("/api/auth/me", headers=h).json()
    assert me["billing"]["read_only"] is True and me["workspace_status"] == "active"
    assert client.get("/api/docs", headers=h).status_code == 200           # reading works
    r = client.post("/api/docs", headers=h, json={"name": "c.txt", "access": "public",
                                                  "content": "x"})
    assert r.status_code == 402 and "trial has ended" in r.json()["detail"]
    assert client.post("/api/chat", headers=h, json={"question": "Hi?"}).status_code == 402
    # What stays open: billing itself, and exporting the workspace.
    assert client.post("/api/billing/checkout", headers=h,
                       json={"plan": "business"}).status_code != 402
    billing.check_writable(org.ws, "POST", "/api/admin/workspace/export")
    from kb import quotas
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        quotas.check(org.ws, "chat")                    # the API, Slack, Teams, email too

    # Thirty days after it ended (less the deletion's grace period), deletion starts.
    ended = billing_store.subscription(org.ws)["trial_ends_at"]
    billing.check_trials(now=ended + 10 * 86400)
    assert lifecycle_store.status(org.ws) == lifecycle_store.ACTIVE
    billing.check_trials(now=ended + 30 * 86400)
    st = lifecycle_store.state(org.ws)
    assert st["status"] == lifecycle_store.DELETING and st["delete_requested_by"] == "system:trial"
    billing.check_trials(now=ended + 31 * 86400)        # asked once only
    assert billing_store.subscription(org.ws)["deletion_scheduled_at"] is not None


def test_billing_off_runs_no_trial_job(org):
    from kb.services import billing
    assert billing.check_trials() == {"skipped": "billing is off"}


# ---- The operator console ----------------------------------------------------------------
def test_the_operator_sees_the_plan_and_extends_a_trial(client, org, billing_on, monkeypatch):
    from kb.services import billing
    from tests.test_operator import OPERATOR, operator_sign_in
    import sso
    from kb.routers import operator as operator_router
    from fastapi.responses import RedirectResponse

    class Client:
        async def authorize_redirect(self, request, redirect_uri, **kwargs):
            return RedirectResponse("https://accounts.example/auth")

    async def answer(name, c, request):
        return {"email": OPERATOR, "email_verified": True, "hd": "cloudastra.example",
                "name": "Op", "amr": ["mfa"]}, "id-token"

    monkeypatch.setattr(sso, "configured", lambda: ["google"])
    monkeypatch.setattr(sso, "client", lambda provider: Client())
    monkeypatch.setattr(operator_router, "provider_answer", answer)
    monkeypatch.setenv("OPERATOR_EMAILS", OPERATOR)
    op = operator_sign_in(client)

    # The trial ended and deletion was asked for (only this workspace's trial is checked).
    monkeypatch.setattr(billing_store, "subscriptions", lambda: [billing_store.subscription(org.ws)])
    billing_store.save_subscription(org.ws, trial_ends_at=time.time() - 40 * 86400)
    billing.check_trials()
    assert lifecycle_store.status(org.ws) == lifecycle_store.DELETING
    row = next(w for w in client.get("/api/operator/workspaces", headers=op).json()["workspaces"]
               if w["slug"] == org.ws)
    assert row["plan"]["name"] == "Trial" and row["plan"]["trial_expired"] is True
    assert row["plan"]["owners"] == [f"founder@{org.domain}"] and "seats" in row

    r = client.post(f"/api/operator/workspaces/{org.ws}/extend-trial", headers=op,
                    json={"days": 7})
    assert r.status_code == 200
    assert 6.9 * 86400 < r.json()["trial_ends_at"] - time.time() <= 7 * 86400
    assert not billing.read_only(org.ws)
    assert lifecycle_store.status(org.ws) == lifecycle_store.ACTIVE      # deletion cancelled
    events = audit_store.list_events(org.ws, limit=50)[0]
    extended = next(e for e in events if e["action"] == "operator.extend_trial")
    assert extended["actor"] == OPERATOR and extended["actor_type"] == "operator"
    assert extended["details"]["days"] == 7
    assert client.post(f"/api/operator/workspaces/{org.ws}/extend-trial", headers=op,
                       json={"days": 365}).status_code == 422
    assert client.post(f"/api/operator/workspaces/{org.ws}/extend-trial",
                       json={"days": 7}).status_code == 401
    # And names a billing owner for a workspace that has none.
    r = client.post(f"/api/operator/workspaces/{org.ws}/billing-owner", headers=op,
                    json={"email": f"admin@{org.domain}"})
    assert r.status_code == 200
    assert billing_store.is_owner(org.ws, f"admin@{org.domain}")
