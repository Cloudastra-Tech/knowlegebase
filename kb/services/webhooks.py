"""
Outbound webhooks (platform.md PL-20): the workspace's own systems (ITSM, SIEM,
chat ops) are told when something happens, with no polling.

EVENTS (EVENTS below) are emitted with emit(ws, event, data) where they happen:
indexing (document.indexed), a failed sync (integration.sync_failed), the
question log (question.unanswered: the question's text only when the
workspace's question log keeps text, and as it keeps it), invites, SCIM and
single sign-on (member.added), the Members screen and SCIM (member.disabled),
the SAML metadata refresh (sso.metadata_changed), the abuse and cost monitor
(abuse.alert: the alerts the workspace's admins are told about, see
kb/services/abuse.py) and every audit event of the
workspace (audit.event, for a SIEM: audit_store's listener). Payloads carry IDs
and a few fields, never document text: the receiver fetches more with an API
key. emit() never fails what called it.

DELIVERY. Each event becomes one row per subscribed webhook
(webhook_deliveries), sent by a `webhooks` job (one lane per workspace) as a
POST of the JSON body with

    X-KB-Event: member.added          X-KB-Delivery: <delivery id>
    X-KB-Timestamp: <unix seconds>    X-KB-Signature: v1=<hex HMAC-SHA256>

where the HMAC, keyed by the webhook's secret, is of "<timestamp>.<body>": the
receiver recomputes it and refuses a timestamp more than 5 minutes off. A 2xx
answer is delivered. Anything else is tried again after 30 s, 1, 2, 4 ... minutes
(at most an hour apart) until 24 h after the event, then it's failed. Redirects
aren't followed.

ADDRESSES go through url_guard like a connector's (https only, and only public
IP addresses, checked when saved and again on every request; a test server can
be listed in CONNECTOR_ALLOW_PRIVATE_HOSTS). A refused address fails the
delivery at once.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time

import httpx

import audit_store
import question_log_store
import url_guard
import webhooks_store
from kb import jobs

log = logging.getLogger("uvicorn.error")

EVENTS = {
    "document.indexed": "Documents were added, changed or removed in the search index",
    "integration.sync_failed": "A connector's sync failed",
    "question.unanswered": "A question the documents couldn't answer",
    "member.added": "Someone joined the workspace",
    "member.disabled": "A member's access was turned off",
    "sso.metadata_changed": "A SAML provider's metadata changed or needs attention",
    "abuse.alert": "Unusual usage or spend: a daily spend limit passed, a spike",
    "audit.event": "Every audit log event (for a SIEM)",
}
TEST_EVENT = "webhook.test"
TIMEOUT = 10                       # seconds per request
RETRY_FOR = 24 * 60 * 60           # seconds after the event it's still tried
BACKOFF = 30                       # the first retry after this many seconds ...
BACKOFF_MAX = 60 * 60              # ... doubling, up to this
USER_AGENT = "KnowledgeAssistant-Webhooks/1"


# ---- Signing ---------------------------------------------------------------------------
def signature(secret, timestamp, body):
    """'v1=' + hex HMAC-SHA256 of f"{timestamp}." + body (bytes), keyed by the secret."""
    mac = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


def verify(secret, timestamp, body, header, now=None, tolerance=300):
    """What a receiver does (and the tests): True if `header` signs this body and
    the timestamp is within `tolerance` seconds."""
    now = time.time() if now is None else now
    try:
        if abs(now - int(timestamp)) > tolerance:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), header or "")


def backoff(attempts):
    """Seconds before the next try, after `attempts` failed ones."""
    return min(BACKOFF * 2 ** max(0, attempts - 1), BACKOFF_MAX)


# ---- Addresses -------------------------------------------------------------------------
def check_url(url):
    """Raise url_guard.UnsafeURL unless deliveries may go to `url`."""
    url_guard.check_connector(url)


def _guard(request):
    check_url(str(request.url))


def post(url, body, headers):
    """One POST (tests replace this). Every request's address is checked first."""
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False,
                      event_hooks={"request": [_guard]}) as client:
        return client.post(url, content=body, headers=headers)


# ---- Emitting --------------------------------------------------------------------------
def _body(event, event_id, ws, data, now):
    return json.dumps({"id": event_id, "type": event, "created_at": int(now), "workspace": ws,
                       "data": data}, sort_keys=True, separators=(",", ":"))


def emit(ws, event, data, now=None):
    """Queue `event` for every enabled webhook of the workspace that wants it.
    Returns the event's id, or None if no webhook wants it. Never raises."""
    if not ws or ws == audit_store.PLATFORM:
        return None
    try:
        hooks = webhooks_store.subscribed(ws, event)
        if not hooks:
            return None
        now = time.time() if now is None else now
        event_id = "evt_" + secrets.token_hex(12)
        body = _body(event, event_id, ws, data, now)
        for hook in hooks:
            webhooks_store.add_delivery(ws, hook["id"], event, event_id, body, now)
        schedule(ws)
        return event_id
    except Exception:
        log.exception("Webhooks: couldn't queue %s for workspace %s", event, ws)
        return None


def schedule(ws, delay=0):
    jobs.enqueue("webhooks", ws, {}, lane=f"webhooks:{ws}", dedupe_key=f"webhooks:{ws}",
                 delay=delay)


# ---- Delivering ------------------------------------------------------------------------
def deliver(ws, d, hook, secret, now=None):
    """Send one delivery and record how it went. Returns its new status."""
    now = time.time() if now is None else now
    body = d["payload"].encode()
    timestamp = str(int(now))
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
               "X-KB-Event": d["event"], "X-KB-Delivery": d["id"],
               "X-KB-Timestamp": timestamp,
               "X-KB-Signature": signature(secret, timestamp, body)}
    code = error = None
    permanent = False
    try:
        check_url(hook["url"])
        code = post(hook["url"], body, headers).status_code
    except url_guard.UnsafeURL as e:          # here, or from the request's own check
        error, permanent = str(e), True
    except httpx.HTTPError as e:
        error = type(e).__name__
    if code is not None and 200 <= code < 300:
        webhooks_store.attempted(d["id"], "delivered", code, now=now)
        return "delivered"
    if error is None:
        error = f"HTTP {code}"
    wait = backoff(d["attempts"] + 1)
    if permanent or d["event"] == TEST_EVENT or now + wait > d["created_at"] + RETRY_FOR:
        webhooks_store.attempted(d["id"], "failed", code, error, now=now)
        return "failed"
    webhooks_store.attempted(d["id"], "pending", code, error, next_at=now + wait, now=now)
    return "pending"


def deliver_due(ws, now=None):
    """Send every delivery of the workspace that's due. Returns counts by status."""
    now = time.time() if now is None else now
    done = {"delivered": 0, "pending": 0, "failed": 0}
    hooks, secrets_ = {}, {}
    for d in webhooks_store.due(ws, now):
        hook = hooks.get(d["webhook_id"]) or webhooks_store.get(ws, d["webhook_id"])
        if not hook or not hook["enabled"]:
            webhooks_store.attempted(d["id"], "failed", error="The webhook is off.", now=now)
            done["failed"] += 1
            continue
        hooks[hook["id"]] = hook
        if hook["id"] not in secrets_:
            secrets_[hook["id"]] = webhooks_store.secret(ws, hook["id"])
        done[deliver(ws, d, hook, secrets_[hook["id"]], now)] += 1
    return done


@jobs.handler("webhooks", max_attempts=3)
def run_webhooks(job):
    ws = job.workspace
    done = deliver_due(ws)
    later = webhooks_store.next_due(ws)
    if later is not None:                     # retries waiting: come back for them
        schedule(ws, delay=max(1, later - time.time()))
    return done


def send_test(ws, hook):
    """A webhook.test event to one webhook, sent now. Returns the delivery."""
    now = time.time()
    event_id = "evt_" + secrets.token_hex(12)
    body = _body(TEST_EVENT, event_id, ws, {"webhook_id": hook["id"]}, now)
    delivery_id = webhooks_store.add_delivery(ws, hook["id"], TEST_EVENT, event_id, body, now)
    d = webhooks_store.delivery(ws, delivery_id)
    deliver(ws, d, hook, webhooks_store.secret(ws, hook["id"]), now)
    return webhooks_store.delivery(ws, delivery_id)


@jobs.periodic("webhooks_prune", lambda: 24 * 60 * 60)
def prune(job=None, now=None):
    """Old deliveries go after KEEP_DAYS; a question's text sooner, with the
    question log's own retention."""
    now = time.time() if now is None else now
    gone = webhooks_store.prune(now)
    for ws in webhooks_store.workspaces_with("question.unanswered"):
        days = question_log_store.settings(ws)["days"]
        if days:
            gone += webhooks_store.prune_event(ws, "question.unanswered", now - days * 86400)
    return {"deleted": gone}


# ---- The events ------------------------------------------------------------------------
def on_audit(event):
    """audit_store's listener: every audit event of a workspace, minus its details
    (the receiver can read them from the audit API)."""
    emit(event["workspace"], "audit.event",
         {k: event.get(k) for k in ("id", "at", "action", "actor", "actor_type", "target_type",
                                    "target_id", "hash")})


audit_store.listen(on_audit)


def member_added(ws, username, role, via):
    emit(ws, "member.added", {"username": username, "role": role, "via": via})


def member_disabled(ws, username, via):
    emit(ws, "member.disabled", {"username": username, "via": via})


def abuse_alert(ws, alert):
    """An alert the workspace's admins are told about (kb/services/abuse.py), with
    its numbers; a member it names is in `subject`, as the admins' email says."""
    emit(ws, "abuse.alert", {"alert_id": alert["id"], "kind": alert["kind"],
                             "subject": alert.get("subject"), "details": alert["details"]})


def question_unanswered(ws, question_id, surface, question=None):
    data = {"question_id": question_id, "surface": surface}
    if question:
        data["question"] = question
    emit(ws, "question.unanswered", data)
