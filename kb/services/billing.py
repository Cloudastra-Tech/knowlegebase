"""
Plans, seats and billing (platform.md PL-16), and the trial (PL-17), all behind
BILLING=on. With it off (the default) nothing here limits anything: entitlement()
is always True, no quota refuses, nothing is read-only, and the billing screen
isn't shown. Whether the product is sold as hosted SaaS is still the owner's
decision; this is the plumbing for when it is.

WHO IT APPLIES TO: a workspace with a row in `subscriptions` (billing_store.py).
One that signs up while billing is on gets a TRIAL_DAYS trial of the "trial"
plan; one without a row (made before billing, or by the operator) is never
limited until the operator gives it a plan. Turning billing on never locks an
existing workspace.

WHAT A PLAN GIVES (plans.limits):
    questions_month, docs, storage_mb, connectors   limits (null: none)
    sso_oidc_saml, scim, audit_export, api, slack, mcp, teams   features
Features are checked with entitlement(ws, feature) / require(ws, feature), one
line at each feature's router; limits with kb/quotas.py, where this registers
plan_limits. A refusal is a 402 that says what the plan includes.

SEATS: a member who isn't turned off and signed in, or asked something, in the
last SEAT_DAYS days. Dormant accounts (e.g. provisioned by SCIM) aren't seats.

STATUS: trialing (until trial_ends_at), active, past_due (Stripe couldn't take a
payment: still works, with a warning), canceled. READ-ONLY: a trial that has
ended, or a canceled subscription. People still sign in and read what's there,
export the workspace and pick a plan; nothing new is asked, uploaded, connected
or changed (kb/deps.current_user turns away every other change). An expired
trial is handed to the workspace's deletion (kb/services/lifecycle.py) so that
it's purged DELETE_AFTER_DAYS after the trial ended; its admins are told, and
can still export it or cancel the deletion.

BILLING OWNERS (billing_owners): the founder, to start with, and whoever an
owner adds. Only an owner starts a checkout, opens the Stripe portal or changes
the owners; an admin who isn't one sees the screen but can't change it, and
Cloudastra support never can.
"""

import logging
import math
import os
import time

from fastapi import HTTPException

import billing_store as store
import db
import docs_store
import lifecycle_store
import mailer
import members_store
import workspaces
from kb import jobs, quotas
from kb.settings import ON, env_int

log = logging.getLogger("uvicorn.error")

TRIAL_DAYS = 14
DELETE_AFTER_DAYS = 30            # an expired trial is purged this long after it ended
SEAT_DAYS = 30
REMINDER_DAYS = (7, 3, 1)         # trial reminders, days before it ends
TRIAL_PLAN = "trial"
TRIAL_ACTOR = "system:trial"      # who asks for an expired trial's deletion
FEATURES = ("sso_oidc_saml", "scim", "audit_export", "api", "slack", "mcp", "teams")
FEATURE_NAMES = {
    "sso_oidc_saml": "your own sign-in provider (OpenID Connect or SAML)",
    "scim": "SCIM provisioning",
    "audit_export": "exporting the audit log",
    "api": "the API and API keys",
    "slack": "the Slack app",
    "mcp": "the MCP server",
    "teams": "the Microsoft Teams app",
}
LIMITS = ("questions_month", "docs", "storage_mb", "connectors")
UPGRADE = "An owner of the workspace can change its plan under Plan & billing."


def billing_on():
    return os.getenv("BILLING", "off").strip().lower() in ON


def trial_days():
    return env_int("TRIAL_DAYS", TRIAL_DAYS)


# ---- Where a workspace stands ------------------------------------------------------------
def state(ws, now=None):
    """None when billing doesn't apply to the workspace (off, or no subscription).
    Otherwise its subscription with the plan, and what follows from it:
    trial_days_left, expired (a trial that has ended) and read_only."""
    if not billing_on():
        return None
    sub = store.subscription(ws)
    if not sub:
        return None
    now = now or time.time()
    plan = store.plan(sub["plan"]) or store.plan(TRIAL_PLAN)
    trialing = sub["status"] == "trialing"
    ends = sub["trial_ends_at"]
    expired = bool(trialing and ends is not None and ends <= now)
    return {**sub, "plan": plan, "plan_id": sub["plan"],
            "trial_days_left": (max(0, math.ceil((ends - now) / 86400))
                                if trialing and ends is not None else None),
            "expired": expired,
            "read_only": expired or sub["status"] == "canceled"}


def read_only(ws):
    st = state(ws)
    return bool(st and st["read_only"])


def _read_only_message(st):
    if st["expired"]:
        return ("Your workspace's trial has ended, so it's read-only: you can read and export "
                "what's there, but not ask, upload or change anything. " + UPGRADE)
    return ("Your workspace's subscription was canceled, so it's read-only. " + UPGRADE)


def entitlement(ws, feature):
    """May the workspace use `feature` (FEATURES) now? Always, with billing off
    or no subscription; otherwise if its plan includes it and it isn't read-only."""
    st = state(ws)
    if st is None:
        return True
    return not st["read_only"] and bool(st["plan"]["limits"].get(feature))


def refusal(ws, feature):
    """Why entitlement(ws, feature) is False, in words for the person."""
    st = state(ws)
    if st and st["read_only"]:
        return _read_only_message(st)
    plan = st["plan"]["name"] if st else "current"
    return (f"Your workspace's plan ({plan}) doesn't include {FEATURE_NAMES[feature]}. "
            + UPGRADE)


def require(ws, feature):
    """One line at a gated feature's router: a 402 unless entitlement(ws, feature)."""
    if not entitlement(ws, feature):
        raise HTTPException(402, refusal(ws, feature))


# Paths that still take changes while a workspace is read-only: signing in and
# out, billing itself, and exporting or deleting the workspace.
WHILE_READ_ONLY = ("/api/auth/", "/api/billing/", "/api/admin/workspace")


def check_writable(ws, method, path):
    """kb/deps.current_user: a 402 for a change to a read-only workspace."""
    if method in ("GET", "HEAD", "OPTIONS") or not billing_on() or \
            path.startswith(WHILE_READ_ONLY):
        return
    st = state(ws)
    if st and st["read_only"]:
        raise HTTPException(402, _read_only_message(st))


# ---- Seats and usage ---------------------------------------------------------------------
def seats(ws, now=None):
    """Members not turned off who signed in or asked something in the last SEAT_DAYS."""
    since = (now or time.time()) - SEAT_DAYS * 86400
    conn = db.connect()
    try:
        return conn.execute(
            """SELECT COUNT(*) FROM members m
                WHERE m.workspace = ? AND m.disabled = 0
                  AND (m.last_login_at >= ?
                       OR EXISTS (SELECT 1 FROM question_log q WHERE q.workspace = m.workspace
                                     AND q.user = m.username AND q.at >= ?))""",
            (ws, since, since)).fetchone()[0]
    finally:
        conn.close()


def month_start(now=None):
    t = time.gmtime(now or time.time())
    return time.strftime("%Y-%m-01", t)


def next_month(now=None):
    t = time.gmtime(now or time.time())
    year, month = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    return f"{year}-{month:02d}-01"


def questions_this_month(ws, now=None):
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM question_log WHERE workspace = ? AND day >= ?",
                            (ws, month_start(now))).fetchone()[0]
    finally:
        conn.close()


def _sizes(root, skip=()):
    total = 0
    for dirpath, dirs, files in os.walk(root):
        if dirpath == root:
            dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def connectors(ws):
    conn = db.connect()
    try:
        # (Members' own Gmail and Calendar connections don't count: "~" keys.)
        return [r[0] for r in conn.execute("SELECT provider FROM integrations WHERE workspace = ?",
                                           (ws,)) if "~" not in r[0]]
    finally:
        conn.close()


def usage(ws, now=None):
    """What the workspace uses of each limit. Documents: the ones uploaded on the
    Documents screen. Storage: those and the copies connectors sync. The sample
    documents (kb/services/onboarding.py) count for neither."""
    from kb.services import onboarding
    root = workspaces.docs_root(ws)
    return {"questions_month": questions_this_month(ws, now),
            "docs": len(docs_store.list_docs(root)) if os.path.isdir(root) else 0,
            "storage_mb": round(_sizes(root, skip=(onboarding.SAMPLE_FOLDER,)) / 1024 / 1024, 1)
            if os.path.isdir(root) else 0,
            "connectors": len(connectors(ws))}


def _n(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


@quotas.register
def plan_limits(ws, what, user=None, files=0, bytes=0, provider=None, **_):
    """The plan's limits (kb/quotas.py). A read-only workspace asks nothing more."""
    st = state(ws)
    if st is None or what not in ("chat", "docs", "connector"):
        return
    if st["read_only"]:
        raise HTTPException(402, _read_only_message(st))
    limits, plan = st["plan"]["limits"], st["plan"]["name"]
    if what == "chat":
        limit = limits.get("questions_month")
        if limit is not None and questions_this_month(ws) >= limit:
            raise HTTPException(402, f"Your plan ({plan}) includes "
                                     f"{_n(limit, 'question')} a month, and your workspace has "
                                     f"asked them all. It starts again on {next_month()}. "
                                     + UPGRADE)
    elif what == "docs":
        used = usage(ws)
        limit = limits.get("docs")
        if limit is not None and used["docs"] + files > limit:
            raise HTTPException(402, f"Your plan ({plan}) includes {_n(limit, 'document')}, "
                                     f"and the workspace has {used['docs']:,}. Remove some, or "
                                     "ask an owner to upgrade. Nothing was uploaded.")
        limit = limits.get("storage_mb")
        if limit is not None and used["storage_mb"] + bytes / 1024 / 1024 > limit:
            raise HTTPException(402, f"Your plan ({plan}) includes {limit:,} MB of documents, "
                                     f"and the workspace uses {used['storage_mb']:,} MB. "
                                     "Nothing was uploaded. " + UPGRADE)
    elif what == "connector":
        limit = limits.get("connectors")
        have = connectors(ws)
        if limit is not None and provider not in have and len(have) >= limit:
            raise HTTPException(402, f"Your plan ({plan}) includes {_n(limit, 'connector')}, "
                                     f"and {len(have)} {'is' if len(have) == 1 else 'are'} "
                                     "connected. Disconnect one first, or ask an owner to "
                                     "upgrade.")


# ---- Owners ------------------------------------------------------------------------------
def is_owner(user):
    """Does this person own the workspace's billing? Never Cloudastra support."""
    return not user.get("impersonator") and store.is_owner(user["workspace"], user["username"])


def require_owner(user):
    if not is_owner(user):
        raise HTTPException(403, "Only an owner of the workspace's billing can change it."
                            + (" Ask " + ", ".join(o["username"] for o in
                                                   store.owners(user["workspace"])) + "."
                               if store.owners(user["workspace"]) else ""))


def founded(ws, username):
    """A new workspace: its founder owns its billing, and with billing on, it starts
    its trial."""
    store.add_owner(ws, username)
    if billing_on():
        store.start_trial(ws, TRIAL_PLAN, time.time() + trial_days() * 86400)


# ---- Trial -------------------------------------------------------------------------------
def extend_trial(ws, days, now=None):
    """The operator gives more trial time: from its end, or from now if it has
    ended. A deletion an expired trial asked for is cancelled. Returns the new end."""
    now = now or time.time()
    sub = store.subscription(ws)
    start = max(now, (sub or {}).get("trial_ends_at") or now)
    ends = start + days * 86400
    fields = {"trial_ends_at": ends, "reminders": [], "deletion_scheduled_at": None}
    if not sub or sub["status"] in ("trialing", "canceled"):
        fields["status"] = "trialing"
        if not sub:
            fields["plan"] = TRIAL_PLAN
    store.save_subscription(ws, **fields)
    _undo_trial_deletion(ws)
    return ends


def _undo_trial_deletion(ws):
    st = lifecycle_store.state(ws)
    if st["deleting_at"] and st["delete_requested_by"] == TRIAL_ACTOR:
        from kb.services import lifecycle
        lifecycle.cancel_deletion(ws, TRIAL_ACTOR)


def _people_to_tell(ws):
    """The billing owners and the admins, once each."""
    out = []
    for name in [o["username"] for o in store.owners(ws)] + members_store.enabled_admins(ws):
        if "@" in name and name not in out:
            out.append(name)
    return out


def _tell(ws, subject, body, link):
    for to in _people_to_tell(ws):
        try:
            mailer.send(to, "billing_notice", title=subject, body=body, link=link)
        except mailer.MailError:
            log.warning("Billing: couldn't email %s about workspace %s", to, ws)


def _when(t):
    return time.strftime("%d %B %Y", time.gmtime(t))


def _link():
    return (os.getenv("PUBLIC_URL") or "").rstrip("/") + "/billing"


@jobs.periodic("billing_trials", lambda: 60 * 60)
def check_trials(job=None, now=None):
    """Hourly: remind before a trial ends, tell when it has ended (read-only), and
    hand it to deletion so it's purged DELETE_AFTER_DAYS after it ended."""
    if not billing_on():
        return {"skipped": "billing is off"}
    now = now or time.time()
    done = {"reminded": 0, "ended": 0, "deleting": 0}
    for sub in store.subscriptions():
        ws, ends = sub["workspace"], sub["trial_ends_at"]
        if sub["status"] != "trialing" or ends is None:
            continue
        name = members_store.workspace_name(ws)
        sent = set(sub["reminders"])
        left = ends - now
        if left > 0:
            due = [d for d in REMINDER_DAYS if left <= d * 86400 and d not in sent]
            if due:
                days = math.ceil(left / 86400)
                _tell(ws, f"{name}: {_n(days, 'day')} left in your trial",
                      f"The trial of the {name} workspace ends on {_when(ends)}. Pick a plan "
                      "before then to keep asking questions; after it, the workspace is "
                      "read-only.", _link())
                store.save_subscription(ws, reminders=sorted(sent | set(due)))
                done["reminded"] += 1
            continue
        if 0 not in sent:
            _tell(ws, f"{name}: your trial has ended",
                  f"The trial of the {name} workspace ended on {_when(ends)}. It's read-only "
                  "now: everyone can still sign in, read and export it. Pick a plan to use it "
                  f"again. It will be deleted on {_when(ends + DELETE_AFTER_DAYS * 86400)} "
                  "unless a plan is picked.", _link())
            store.save_subscription(ws, reminders=sorted(sent | {0}))
            done["ended"] += 1
        grace = lifecycle_store.grace_days()
        if (not sub["deletion_scheduled_at"]
                and now >= ends + max(0, DELETE_AFTER_DAYS - grace) * 86400
                and lifecycle_store.status(ws) == lifecycle_store.ACTIVE):
            from kb.services import lifecycle
            lifecycle.schedule_deletion(ws, TRIAL_ACTOR)
            store.save_subscription(ws, deletion_scheduled_at=now)
            done["deleting"] += 1
    return done


# ---- Stripe ------------------------------------------------------------------------------
STATUS_FROM_STRIPE = {"active": "active", "trialing": "active", "past_due": "past_due",
                      "unpaid": "past_due", "incomplete": "past_due", "canceled": "canceled",
                      "incomplete_expired": "canceled", "paused": "canceled"}


def plan_for_price(price):
    from kb.services import stripe_api
    for p in store.plans():
        if price and stripe_api.price_for(p) == price:
            return p
    return None


def _workspace_of(obj):
    """The workspace a Stripe object is for: our metadata, else its customer's."""
    ws = (obj.get("metadata") or {}).get("workspace") or obj.get("client_reference_id")
    if not ws and obj.get("customer"):
        sub = store.subscription_for_customer(obj["customer"])
        ws = sub and sub["workspace"]
    if not ws or not workspaces.valid(ws):
        return None
    conn = db.connect()
    try:
        found = conn.execute("SELECT 1 FROM workspaces WHERE slug = ?", (ws,)).fetchone()
    finally:
        conn.close()
    return ws if found else None


def handle_event(event):
    """Apply a verified webhook event. Returns (workspace, what changed) or (None, None)
    for an event that isn't ours to act on."""
    kind, obj = event["type"], (event.get("data") or {}).get("object") or {}
    ws = _workspace_of(obj)
    if not ws:
        return None, None
    if kind == "checkout.session.completed":
        if obj.get("mode") != "subscription":
            return None, None
        plan = (obj.get("metadata") or {}).get("plan")
        if not store.plan(plan):
            plan = (store.subscription(ws) or {}).get("plan") or TRIAL_PLAN
        fields = {"plan": plan, "status": "active", "stripe_customer": obj.get("customer"),
                  "stripe_subscription": obj.get("subscription"), "reminders": [],
                  "deletion_scheduled_at": None}
        store.save_subscription(ws, **fields)
        _undo_trial_deletion(ws)
        return ws, {"plan": plan, "status": "active"}
    if kind in ("customer.subscription.created", "customer.subscription.updated",
                "customer.subscription.deleted"):
        items = ((obj.get("items") or {}).get("data") or [{}])
        price = ((items[0].get("price") or {}).get("id")) if items else None
        plan = plan_for_price(price)
        status = ("canceled" if kind == "customer.subscription.deleted"
                  else STATUS_FROM_STRIPE.get(obj.get("status"), "past_due"))
        fields = {"status": status, "stripe_customer": obj.get("customer"),
                  "stripe_subscription": obj.get("id"),
                  "seats": int(items[0].get("quantity") or 0) if items else 0,
                  "current_period_end": obj.get("current_period_end")}
        if plan:
            fields["plan"] = plan["id"]
        elif not store.subscription(ws):
            fields["plan"] = (obj.get("metadata") or {}).get("plan") or TRIAL_PLAN
        if status == "active":
            fields.update(reminders=[], deletion_scheduled_at=None)
        store.save_subscription(ws, **fields)
        if status == "active":
            _undo_trial_deletion(ws)
        return ws, {k: fields[k] for k in ("status", "seats") if k in fields} | (
            {"plan": plan["id"]} if plan else {})
    if kind == "invoice.payment_failed":
        if store.subscription(ws):
            store.save_subscription(ws, status="past_due")
            return ws, {"status": "past_due"}
    return None, None
