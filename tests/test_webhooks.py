"""
Outbound webhooks (platform.md PL-20; kb/services/webhooks.py, webhooks_store.py):
admins add, test, turn off and delete them through the API; each event the
workspace emits reaches the webhooks that want it, signed (HMAC-SHA256 over the
timestamp and body), retried with a growing wait for 24 hours, never to a private
address. Each event is emitted where it happens. The HTTP calls are faked (the
receiver is a list); jobs are run by hand.
"""

import json
import time
import uuid

import pytest

import audit_store
import db
import secret_box
import url_guard
import webhooks_store
from kb.services import webhooks

PASSWORD = "correct-horse"
HOOK_URL = "https://hooks.example/kb"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app)


@pytest.fixture
def receiver(monkeypatch, tmp_path, jobs_paused):
    """What the fake receiver got: (url, body bytes, headers), answering `status`.
    hooks.example resolves to a public address."""
    got = Got()
    state = {"status": 200}

    class Response:
        def __init__(self, status):
            self.status_code = status

    def post(url, body, headers):
        got.append((url, body, headers))
        return Response(state["status"])
    monkeypatch.setattr(webhooks, "post", post)
    real = url_guard._resolve
    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: (
        [(2, 1, 6, "", ("93.184.216.34", port))] if host == "hooks.example" else real(host, port)))
    monkeypatch.chdir(tmp_path)
    got.state = state
    return got


class Got(list):
    pass


def new_workspace():
    import auth
    import members_store
    w = f"wh-{uuid.uuid4().hex[:8]}"
    for name, role in (("admin", "admin"), ("hr", "hr"), ("staff", "public")):
        members_store.set_password(f"{name}@{w}.example", name.title(),
                                   auth.hash_password(PASSWORD), w, role)
    return w


def login(client, w, name="admin"):
    r = client.post("/api/auth/login", json={"username": f"{name}@{w}.example",
                                             "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def add(client, h, events, url=HOOK_URL):
    r = client.post("/api/admin/webhooks", headers=h, json={"url": url, "events": events})
    assert r.status_code == 200, r.text
    return r.json()


def deliver(w, now=None):
    return webhooks.deliver_due(w, now)


def bodies(receiver, event=None):
    out = [json.loads(body) for _, body, _ in receiver]
    return [b for b in out if event is None or b["type"] == event]


# ---- Managing them --------------------------------------------------------------------------
def test_an_admin_adds_one_and_sees_its_secret_once(client, receiver):
    w = new_workspace()
    h = login(client, w)
    made = add(client, h, ["member.added", "audit.event"])
    secret = made["secret"]
    assert secret.startswith("whsec_") and made["webhook"]["secret_hint"] == secret[-4:]
    listed = client.get("/api/admin/webhooks", headers=h).json()
    assert secret not in json.dumps(listed)
    assert {e["id"] for e in listed["events"]} == set(webhooks.EVENTS)
    # Sealed under the workspace's key, not stored as it is.
    with db.connect() as conn:
        (blob,) = conn.execute("SELECT secret FROM webhooks WHERE id = ?",
                               (made["webhook"]["id"],)).fetchone()
    assert secret.encode() not in bytes(blob)
    assert secret_box.unseal(blob, w) == secret
    # Members and HR can't see or add them.
    for name in ("hr", "staff"):
        other = login(client, w, name)
        assert client.get("/api/admin/webhooks", headers=other).status_code == 403
    # The audit log keeps the host, never the whole address (it can hold a token).
    (event,) = [e for e in audit_store.list_events(w, limit=20)[0]
                if e["action"] == "webhook.create"]
    assert event["details"]["host"] == "hooks.example" and "url" not in event["details"]


@pytest.mark.parametrize("url", ["http://hooks.example/kb", "https://127.0.0.1/x",
                                 "https://10.0.0.8/x", "https://169.254.169.254/latest",
                                 "https://user:pw@hooks.example/", "ftp://hooks.example/"])
def test_private_and_plain_http_addresses_are_refused(client, receiver, url):
    h = login(client, new_workspace())
    r = client.post("/api/admin/webhooks", headers=h, json={"url": url, "events": ["audit.event"]})
    assert r.status_code == 400


def test_an_unknown_event_is_refused(client, receiver):
    h = login(client, new_workspace())
    assert client.post("/api/admin/webhooks", headers=h,
                       json={"url": HOOK_URL, "events": ["doc.stolen"]}).status_code == 400
    assert client.post("/api/admin/webhooks", headers=h,
                       json={"url": HOOK_URL, "events": []}).status_code == 422


def test_a_test_event_is_sent_now_and_signed(client, receiver):
    w = new_workspace()
    h = login(client, w)
    made = add(client, h, ["member.added"])
    hook_id = made["webhook"]["id"]
    r = client.post(f"/api/admin/webhooks/{hook_id}/test", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "delivered"
    assert r.json()["response_code"] == 200
    url, body, headers = receiver[-1]
    assert url == HOOK_URL and json.loads(body)["type"] == "webhook.test"
    assert webhooks.verify(made["secret"], headers["X-KB-Timestamp"], body,
                           headers["X-KB-Signature"])
    deliveries = client.get(f"/api/admin/webhooks/{hook_id}/deliveries", headers=h).json()
    assert [d["event"] for d in deliveries["deliveries"]] == ["webhook.test"]
    one = client.get(f"/api/admin/webhooks/deliveries/{r.json()['id']}", headers=h).json()
    assert one["payload"]["data"] == {"webhook_id": hook_id}


# ---- Signing ---------------------------------------------------------------------------------
def test_the_signature_covers_the_timestamp_and_body():
    body = b'{"a":1}'
    sig = webhooks.signature("whsec_x", "1700000000", body)
    assert sig.startswith("v1=") and len(sig) == 3 + 64
    assert webhooks.verify("whsec_x", "1700000000", body, sig, now=1700000100)
    assert not webhooks.verify("whsec_x", "1700000000", b'{"a":2}', sig, now=1700000100)
    assert not webhooks.verify("whsec_y", "1700000000", body, sig, now=1700000100)
    assert not webhooks.verify("whsec_x", "1700000001", body, sig, now=1700000100)
    assert not webhooks.verify("whsec_x", "1700000000", body, sig, now=1700000000 + 301)


def test_deliveries_carry_the_event_id_and_headers(client, receiver):
    w = new_workspace()
    made = add(client, login(client, w), ["member.added"])
    event_id = webhooks.emit(w, "member.added", {"username": "x@y", "role": "public",
                                                 "via": "test"})
    deliver(w)
    url, body, headers = receiver[-1]
    payload = json.loads(body)
    assert payload["id"] == event_id and payload["workspace"] == w
    assert payload["data"] == {"username": "x@y", "role": "public", "via": "test"}
    assert headers["X-KB-Event"] == "member.added" and headers["X-KB-Delivery"]
    assert headers["Content-Type"] == "application/json"
    assert webhooks.verify(made["secret"], headers["X-KB-Timestamp"], body,
                           headers["X-KB-Signature"])


# ---- Retries ---------------------------------------------------------------------------------
def test_a_failing_endpoint_is_retried_with_backoff_for_24_hours(client, receiver):
    w = new_workspace()
    hook = add(client, login(client, w), ["member.added"])["webhook"]
    receiver.state["status"] = 503
    start = time.time()
    webhooks.emit(w, "member.added", {"username": "x"}, now=start)
    now, waits = start, []
    while True:
        done = deliver(w, now)
        (d,) = webhooks_store.deliveries(w, hook["id"])
        if d["status"] != "pending":
            break
        waits.append(round(d["next_at"] - now))
        now = d["next_at"]
    assert waits[:5] == [30, 60, 120, 240, 480]
    assert max(waits) == 3600                             # at most an hour apart
    assert d["status"] == "failed" and d["response_code"] == 503 and done["failed"] == 1
    assert start + 23 * 3600 <= now <= start + 24 * 3600     # tried until 24 h, no longer
    assert d["attempts"] == len(waits) + 1


def test_a_retry_that_succeeds_is_delivered(client, receiver):
    w = new_workspace()
    hook = add(client, login(client, w), ["member.added"])["webhook"]
    receiver.state["status"] = 500
    webhooks.emit(w, "member.added", {"username": "x"})
    deliver(w)
    receiver.state["status"] = 204
    deliver(w, time.time() + 31)
    (d,) = webhooks_store.deliveries(w, hook["id"])
    assert (d["status"], d["attempts"], d["response_code"]) == ("delivered", 2, 204)
    assert len(receiver) == 2


def test_the_job_comes_back_for_retries(client, receiver):
    w = new_workspace()
    add(client, login(client, w), ["member.added"])
    receiver.state["status"] = 500
    webhooks.emit(w, "member.added", {"username": "x"})
    with db.connect() as conn:                 # the job emit() queued, as if it had started
        conn.execute("DELETE FROM jobs WHERE kind = 'webhooks' AND workspace = ?", (w,))

    class Job:
        workspace = w
    assert webhooks.run_webhooks(Job()) == {"delivered": 0, "pending": 1, "failed": 0}
    with db.connect() as conn:
        queued = conn.execute("SELECT run_after FROM jobs WHERE kind = 'webhooks' AND "
                              "workspace = ? AND status = 'queued'", (w,)).fetchall()
    assert len(queued) == 1 and queued[0][0] > time.time() + 20      # in about 30 s


def test_an_address_that_turns_private_fails_at_once(client, receiver, monkeypatch):
    w = new_workspace()
    hook = add(client, login(client, w), ["member.added"])["webhook"]
    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: [(2, 1, 6, "",
                                                                   ("10.1.2.3", port))])
    webhooks.emit(w, "member.added", {"username": "x"})
    assert deliver(w)["failed"] == 1
    (d,) = webhooks_store.deliveries(w, hook["id"])
    assert d["status"] == "failed" and "private" in d["error"] and receiver == []


def test_a_turned_off_webhook_gets_nothing(client, receiver):
    w = new_workspace()
    h = login(client, w)
    hook = add(client, h, ["member.added"])["webhook"]
    receiver.state["status"] = 500
    webhooks.emit(w, "member.added", {"username": "x"})
    deliver(w)
    r = client.patch(f"/api/admin/webhooks/{hook['id']}", headers=h, json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    (d,) = webhooks_store.deliveries(w, hook["id"])
    assert d["status"] == "failed"                        # what waited isn't sent
    assert webhooks.emit(w, "member.added", {"username": "y"}) is None
    assert client.delete(f"/api/admin/webhooks/{hook['id']}", headers=h).status_code == 200
    assert webhooks_store.deliveries(w, hook["id"]) == []


def test_another_workspaces_events_never_arrive(client, receiver):
    w, other = new_workspace(), new_workspace()
    add(client, login(client, w), ["member.added"])
    webhooks.emit(other, "member.added", {"username": "x"})
    deliver(w)
    deliver(other)
    assert receiver == []


# ---- Every event, where it happens ------------------------------------------------------------
def test_audit_events_go_to_a_siem_without_their_details(client, receiver):
    w = new_workspace()
    h = login(client, w)
    add(client, h, ["audit.event"])
    client.put("/api/admin/question-log/settings", headers=h, json={"mode": "metadata",
                                                                   "days": 30})
    deliver(w)
    (event,) = [b for b in bodies(receiver, "audit.event")
                if b["data"]["action"] == "question_log.settings"]
    assert event["data"]["actor"] == f"admin@{w}.example"
    assert set(event["data"]) == {"id", "at", "action", "actor", "actor_type", "target_type",
                                  "target_id", "hash"}


def test_member_added_and_disabled(client, receiver, monkeypatch):
    import mailer
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, template, **v: sent.append(v))
    w = new_workspace()
    h = login(client, w)
    add(client, h, ["member.added", "member.disabled"])
    r = client.post("/api/admin/members/invite", headers=h,
                    json={"email": f"new@{w}.example", "role": "public"})
    assert r.status_code == 200, r.text
    token = sent[-1]["link"].rsplit("/", 1)[1]
    r = client.post("/api/auth/invite/accept", json={"token": token, "name": "New",
                                                     "password": "a-long-password"})
    assert r.status_code == 200, r.text
    client.patch(f"/api/admin/members/staff@{w}.example", headers=h, json={"disabled": True})
    deliver(w)
    assert [b["data"] for b in bodies(receiver, "member.added")] == [
        {"username": f"new@{w}.example", "role": "public", "via": "invite"}]
    assert [b["data"] for b in bodies(receiver, "member.disabled")] == [
        {"username": f"staff@{w}.example", "via": "admin"}]


def test_a_member_added_by_single_sign_on(client, receiver):
    import members_store
    import sso
    w = new_workspace()
    add(client, login(client, w), ["member.added"])
    member = members_store.record_sso_login(f"jit@{w}.example", "Jit", w)
    sso._added(member, "google")
    again = members_store.record_sso_login(f"jit@{w}.example", "Jit", w)
    sso._added(again, "google")                           # not new any more
    deliver(w)
    assert [b["data"]["username"] for b in bodies(receiver, "member.added")] == [
        f"jit@{w}.example"]


def test_question_unanswered_keeps_the_text_only_if_the_log_does(client, receiver):
    import question_log_store
    from kb.services import question_log
    w = new_workspace()
    add(client, login(client, w), ["question.unanswered"])
    user = {"username": f"staff@{w}.example"}
    question_log.record(w, user, "public", "web", "Where is the bike shed?", "not_found")
    question_log.record(w, user, "public", "web", "What is the leave policy?", "answered")
    question_log_store.set_settings(w, "metadata", 30)
    question_log.record(w, user, "public", "web", "Call me on +44 7700 900123?", "not_found")
    deliver(w)
    got = [b["data"] for b in bodies(receiver, "question.unanswered")]
    assert [g.get("question") for g in got] == ["Where is the bike shed?", None]
    assert all(g["surface"] == "web" and g["question_id"] for g in got)


def test_document_indexed(client, receiver, monkeypatch):
    from kb.services import indexing
    w = new_workspace()
    add(client, login(client, w), ["document.indexed"])
    monkeypatch.setattr(indexing, "index", lambda ws, **kw: {"mode": "incremental", "added": 1,
                                                             "changed": 0, "removed": 0,
                                                             "chunks": 3})

    class Job:
        workspace, payload = w, {"docs": ["public/a.txt"]}

        def progress(self, *a):
            pass
    indexing.run_index(Job())
    deliver(w)
    (b,) = bodies(receiver, "document.indexed")
    assert b["data"] == {"mode": "incremental", "documents": ["public/a.txt"],
                         "counts": {"added": 1, "changed": 0, "removed": 0, "chunks": 3}}


def test_integration_sync_failed(client, receiver, monkeypatch):
    import integrations_store
    from connectors import registry
    from kb.services import sync
    from test_connector_sync import Fake
    w = new_workspace()
    add(client, login(client, w), ["integration.sync_failed"])
    connector = Fake()
    monkeypatch.setitem(registry.CONNECTORS, "fake", connector)
    monkeypatch.setattr(sync.indexing, "schedule_index", lambda *a, **k: None)
    integrations_store.connect(w, "fake", "Fake account", {"token": "t"}, "admin")
    connector.add("a", "Alpha", "v1", "Alpha text")
    sync.sync(w, "fake")
    connector.fail_after = base_error()
    with pytest.raises(Exception):
        sync.sync(w, "fake")
    deliver(w)
    (b,) = bodies(receiver, "integration.sync_failed")
    assert b["data"]["provider"] == "fake" and b["data"]["error_code"] == "auth_expired"
    assert set(b["data"]) == {"provider", "run_id", "error_code"}


def base_error():
    from connectors import base
    return base.AuthExpired("token revoked")


def test_sso_metadata_changed(client, receiver, monkeypatch):
    import members_store
    from kb.services import saml_refresh
    w = new_workspace()
    add(client, login(client, w), ["sso.metadata_changed"])
    monkeypatch.setattr(members_store, "set_metadata_status", lambda cid, **f: None)
    monkeypatch.setattr(members_store, "log_event", lambda *a, **k: None)
    conn = {"id": "c1", "workspace": w, "metadata_error": None}
    saml_refresh._metadata_attention(conn, "metadata_error", "couldn't fetch it.",
                                     "Metadata refresh failed: couldn't fetch it.")
    deliver(w)
    (b,) = bodies(receiver, "sso.metadata_changed")
    assert b["data"] == {"connection_id": "c1", "outcome": "attention",
                         "reason": "Metadata refresh failed: couldn't fetch it."}


def test_old_deliveries_and_question_text_are_pruned(client, receiver):
    import question_log_store
    w = new_workspace()
    hook = add(client, login(client, w), ["question.unanswered", "member.added"])["webhook"]
    old = time.time() - 40 * 86400
    webhooks.emit(w, "member.added", {"username": "x"}, now=old)
    webhooks.emit(w, "question.unanswered", {"question": "q"}, now=time.time() - 10 * 86400)
    deliver(w)
    question_log_store.set_settings(w, "redacted", 7)
    webhooks.prune()
    assert webhooks_store.deliveries(w, hook["id"]) == []


# ---- Rotating the secret, sending again, turned off when it keeps failing ------------------
def _signed_by(secret, sent):
    _, body, headers = sent           # received when it was sent (some are sent "later")
    return webhooks.verify(secret, headers["X-KB-Timestamp"], body, headers["X-KB-Signature"],
                           now=int(headers["X-KB-Timestamp"]))


def test_a_rotated_secret_signs_beside_the_old_one_until_its_grace_ends(client, receiver):
    w = new_workspace()
    h = login(client, w)
    made = add(client, h, ["member.added"])
    old, hook_id = made["secret"], made["webhook"]["id"]
    r = client.post(f"/api/admin/webhooks/{hook_id}/secret", headers=h,
                    json={"grace_hours": 2})
    assert r.status_code == 200, r.text
    new = r.json()["secret"]
    assert new != old and r.json()["webhook"]["secret_hint"] == new[-4:]
    assert old not in json.dumps(client.get("/api/admin/webhooks", headers=h).json())
    with db.connect() as conn:                  # both sealed, neither stored as it is
        row = conn.execute("SELECT secret, previous_secret FROM webhooks WHERE id = ?",
                           (hook_id,)).fetchone()
    assert old.encode() not in bytes(row[1]) and new.encode() not in bytes(row[0])

    webhooks.emit(w, "member.added", {"username": "x"})
    deliver(w)
    assert _signed_by(new, receiver[-1]) and _signed_by(old, receiver[-1])     # both, a while
    assert receiver[-1][2]["X-KB-Signature"].count("v1=") == 2
    webhooks.emit(w, "member.added", {"username": "y"}, now=time.time() + 3 * 3600)
    deliver(w, time.time() + 3 * 3600)
    assert _signed_by(new, receiver[-1]) and not _signed_by(old, receiver[-1])  # then new only
    assert [e["action"] for e in audit_store.list_events(w)[0]][0] == "webhook.rotate_secret"

    # No grace: the old one stops at once. A week at most; admins only.
    r = client.post(f"/api/admin/webhooks/{hook_id}/secret", headers=h, json={"grace_hours": 0})
    newest = r.json()["secret"]
    webhooks.emit(w, "member.added", {"username": "z"})
    deliver(w)
    assert _signed_by(newest, receiver[-1]) and not _signed_by(new, receiver[-1])
    assert client.post(f"/api/admin/webhooks/{hook_id}/secret", headers=h,
                       json={"grace_hours": 24 * 8}).status_code == 422
    assert client.post(f"/api/admin/webhooks/{hook_id}/secret",
                       headers=login(client, w, "hr")).status_code == 403


def test_a_delivery_can_be_sent_again(client, receiver):
    w = new_workspace()
    h = login(client, w)
    hook = add(client, h, ["member.added"])["webhook"]
    receiver.state["status"] = 503
    start = time.time()
    webhooks.emit(w, "member.added", {"username": "x"}, now=start)
    while deliver(w, start + 25 * 3600)["pending"]:      # retried until it failed
        start += 3600
    (failed,) = webhooks_store.deliveries(w, hook["id"])
    assert failed["status"] == "failed"

    receiver.state["status"] = 200
    r = client.post(f"/api/admin/webhooks/deliveries/{failed['id']}/redeliver", headers=h)
    assert r.status_code == 200, r.text
    again = r.json()
    assert again["id"] != failed["id"] and again["status"] == "pending"
    assert again["event_id"] == failed["event_id"] and "payload" in again
    assert deliver(w)["delivered"] == 1
    assert json.loads(receiver[-1][1])["id"] == failed["event_id"]   # the same event
    assert receiver[-1][2]["X-KB-Delivery"] == again["id"]
    assert client.post("/api/admin/webhooks/deliveries/nope/redeliver",
                       headers=h).status_code == 404
    other = new_workspace()                                 # another workspace's: not found
    assert client.post(f"/api/admin/webhooks/deliveries/{failed['id']}/redeliver",
                       headers=login(client, other)).status_code == 404


def test_a_webhook_that_keeps_failing_is_turned_off_and_admins_told(client, receiver,
                                                                    monkeypatch):
    import mailer
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, template, **values: sent.append(
        (to, values["title"])))
    monkeypatch.setenv("WEBHOOK_DISABLE_AFTER", "3")
    w = new_workspace()
    h = login(client, w)
    hook = add(client, h, ["member.added"])["webhook"]
    receiver.state["status"] = 500
    webhooks.emit(w, "member.added", {"username": "x"})
    webhooks.emit(w, "member.added", {"username": "y"})
    deliver(w)                                          # 2 failed attempts
    assert webhooks_store.get(w, hook["id"])["failures"] == 2
    webhooks.send_test(w, webhooks_store.get(w, hook["id"]))       # a test doesn't count
    assert webhooks_store.get(w, hook["id"])["failures"] == 2
    deliver(w, time.time() + 31)                        # the third: off
    got = webhooks_store.get(w, hook["id"])
    assert not got["enabled"] and got["disabled_reason"] == "failing" and got["disabled_at"]
    assert {d["status"] for d in webhooks_store.deliveries(w, hook["id"])} == {"failed"}
    assert sent == [(f"admin@{w}.example", f"A webhook was turned off in {w}")]
    (event,) = [e for e in audit_store.list_events(w)[0] if e["action"] == "webhook.auto_disabled"]
    assert event["actor_type"] == "system" and event["details"]["host"] == "hooks.example"
    assert webhooks.emit(w, "member.added", {"username": "z"}) is None
    r = client.post(f"/api/admin/webhooks/deliveries/{webhooks_store.deliveries(w, hook['id'])[0]['id']}"
                    "/redeliver", headers=h)
    assert r.status_code == 400                        # off: turn it on first

    # An admin turns it on again: a fresh start.
    r = client.patch(f"/api/admin/webhooks/{hook['id']}", headers=h, json={"enabled": True})
    assert r.json()["enabled"] and r.json()["failures"] == 0 and r.json()["disabled_reason"] is None
    receiver.state["status"] = 200
    webhooks.emit(w, "member.added", {"username": "z"})
    assert deliver(w)["delivered"] == 1


def test_a_success_starts_the_count_of_failures_again(client, receiver, monkeypatch):
    monkeypatch.setenv("WEBHOOK_DISABLE_AFTER", "3")
    w = new_workspace()
    hook = add(client, login(client, w), ["member.added"])["webhook"]
    for status in (500, 500, 200, 500, 500):
        receiver.state["status"] = status
        webhooks.emit(w, "member.added", {"username": "x"})
        deliver(w, time.time() + 10 * 3600)
    got = webhooks_store.get(w, hook["id"])
    assert got["enabled"] and got["failures"] == 2
