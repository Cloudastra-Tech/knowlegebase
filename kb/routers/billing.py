"""Plan & billing (kb/services/billing.py), only with BILLING=on: with it off,
GET /api/billing says so and everything else is a 404.

    GET    /api/billing                  the plan, usage against its limits, seats, the
                                         trial, and the plans on offer (admins and owners)
    POST   /api/billing/checkout {plan}  a Stripe Checkout for that plan -> {url}   (owners)
    POST   /api/billing/portal           the Stripe Customer Portal -> {url}        (owners)
    POST   /api/billing/owners {username}, DELETE /api/billing/owners/{username}   (owners)
    POST   /api/billing/webhook          Stripe's events, signature checked
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import billing_store
import members_store
from kb.audit import audited
from kb.deps import current_user, public_url, ws_of
from kb.services import billing, stripe_api

router = APIRouter()
log = logging.getLogger("uvicorn.error")

FEATURE_LABELS = {"sso_oidc_saml": "Your own sign-in provider (OIDC / SAML)",
                  "scim": "SCIM provisioning", "audit_export": "Audit log export",
                  "api": "API keys and the API", "slack": "Slack app", "mcp": "MCP server",
                  "teams": "Microsoft Teams app"}
LIMIT_LABELS = {"questions_month": "Questions this month", "docs": "Documents",
                "storage_mb": "Storage (MB)", "connectors": "Connectors"}


def _on():
    if not billing.billing_on():
        raise HTTPException(404, "Billing isn't turned on on this server.")


def _viewer(user):
    """Admins see the billing screen; owners see and change it."""
    if user["role"] != "admin" and not billing.is_owner(user):
        raise HTTPException(403, "Only admins and the owners of the workspace's billing can "
                                 "see it.")


def _plan_out(p, current=None):
    limits = p["limits"]
    return {"id": p["id"], "name": p["name"], "seat_price": p["seat_price"],
            "currency": p["currency"], "buyable": p["buyable"],
            "limits": {k: limits.get(k) for k in billing.LIMITS},
            "features": [{"id": f, "label": FEATURE_LABELS[f], "included": bool(limits.get(f))}
                         for f in billing.FEATURES],
            "available": bool(stripe_api.configured() and stripe_api.price_for(p)),
            "current": p["id"] == current}


@router.get("/api/billing")
def billing_view(user=Depends(current_user)):
    if not billing.billing_on():
        return {"on": False}
    _viewer(user)
    ws = ws_of(user)
    st = billing.state(ws)
    used = billing.usage(ws)
    limits = st["plan"]["limits"] if st else {}
    sub = billing_store.subscription(ws) or {}
    return {
        "on": True, "managed": st is not None,
        "plan": _plan_out(st["plan"], st["plan_id"]) if st else None,
        "status": st["status"] if st else None,
        "trial_ends_at": st["trial_ends_at"] if st else None,
        "trial_days_left": st["trial_days_left"] if st else None,
        "expired": bool(st and st["expired"]), "read_only": bool(st and st["read_only"]),
        "current_period_end": sub.get("current_period_end"),
        "seats": {"used": billing.seats(ws), "paid": sub.get("seats") or 0,
                  "days": billing.SEAT_DAYS},
        "usage": [{"id": k, "label": LIMIT_LABELS[k], "used": used[k], "limit": limits.get(k)}
                  for k in billing.LIMITS],
        "resets_on": billing.next_month(),
        "plans": [_plan_out(p, st and st["plan_id"]) for p in billing_store.plans()
                  if p["buyable"]],
        "owners": billing_store.owners(ws), "is_owner": billing.is_owner(user),
        "stripe": {"configured": stripe_api.configured(),
                   "live_key_refused": stripe_api.live_key_refused()},
        "portal": bool(sub.get("stripe_customer")),
    }


class CheckoutIn(BaseModel):
    plan: str = Field(max_length=40)


@router.post("/api/billing/checkout")
def billing_checkout(body: CheckoutIn, request: Request, user=Depends(current_user),
                     audit=audited("billing.checkout")):
    _on()
    billing.require_owner(user)
    ws = ws_of(user)
    plan = billing_store.plan(body.plan)
    if not plan or not plan["buyable"]:
        raise HTTPException(404, "No such plan.")
    if not stripe_api.configured():
        raise HTTPException(503, "Payments aren't set up on this server yet "
                                 "(STRIPE_SECRET_KEY).")
    price = stripe_api.price_for(plan)
    if not price:
        raise HTTPException(409, f"The {plan['name']} plan has no Stripe price yet "
                                 f"(STRIPE_PRICE_{plan['id'].upper()}).")
    sub = billing_store.subscription(ws) or {}
    seats = max(1, billing.seats(ws))
    try:
        session = stripe_api.checkout_session(
            workspace=ws, plan=plan["id"], price=price, seats=seats,
            success_url=public_url(request, "/billing?checkout=done"),
            cancel_url=public_url(request, "/billing?checkout=cancelled"),
            customer=sub.get("stripe_customer"),
            email=user["username"] if "@" in user["username"] else None)
    except stripe_api.StripeError as e:
        log.warning("Billing: checkout for workspace %s failed: %s", ws, e)
        raise HTTPException(502, str(e))
    audit.record("billing", ws, {"plan": plan["id"], "seats": seats, "session": session["id"]},
                 user=user)
    return {"url": session["url"]}


@router.post("/api/billing/portal")
def billing_portal(request: Request, user=Depends(current_user),
                   audit=audited("billing.portal")):
    _on()
    billing.require_owner(user)
    ws = ws_of(user)
    customer = (billing_store.subscription(ws) or {}).get("stripe_customer")
    if not customer:
        raise HTTPException(409, "There's no subscription to manage yet: pick a plan first.")
    try:
        session = stripe_api.portal_session(customer=customer,
                                            return_url=public_url(request, "/billing"))
    except stripe_api.StripeError as e:
        raise HTTPException(502, str(e))
    audit.record("billing", ws, user=user)
    return {"url": session["url"]}


class OwnerIn(BaseModel):
    username: str = Field(max_length=320)


@router.post("/api/billing/owners")
def add_billing_owner(body: OwnerIn, user=Depends(current_user),
                      audit=audited("billing.owner_add")):
    _on()
    billing.require_owner(user)
    ws = ws_of(user)
    member = members_store.get(body.username)
    if not member or member["workspace"] != ws or member["disabled"]:
        raise HTTPException(404, "That person isn't a member of this workspace.")
    billing_store.add_owner(ws, member["username"], user["username"])
    audit.record("member", member["username"], user=user)
    return {"owners": billing_store.owners(ws)}


@router.delete("/api/billing/owners/{username}")
def remove_billing_owner(username: str, user=Depends(current_user),
                         audit=audited("billing.owner_remove")):
    _on()
    billing.require_owner(user)
    ws = ws_of(user)
    owners = [o["username"] for o in billing_store.owners(ws)]
    username = username.strip().lower()
    if username not in owners:
        raise HTTPException(404, "They don't own the workspace's billing.")
    if len(owners) == 1:
        raise HTTPException(409, "The workspace's billing needs an owner: add another first.")
    billing_store.remove_owner(ws, username)
    audit.record("member", username, user=user)
    return {"owners": billing_store.owners(ws)}


@router.post("/api/billing/webhook")
async def billing_webhook(request: Request, audit=audited("billing.stripe_event")):
    """Stripe's events: taken only with a valid signature, and each one once."""
    _on()
    payload = await request.body()
    try:
        event = stripe_api.verify_webhook(payload, request.headers.get("stripe-signature"))
    except stripe_api.BadSignature as e:
        log.warning("Billing: refused a webhook (%s)", e)
        raise HTTPException(400, "Invalid signature.")
    if not billing_store.first_time(event["id"], event["type"]):
        return {"received": True, "duplicate": True}
    try:
        ws, changed = billing.handle_event(event)
    except Exception:
        billing_store.forget_event(event["id"])        # Stripe tries again
        log.exception("Billing: couldn't handle Stripe event %s (%s)", event["id"],
                      event["type"])
        raise HTTPException(500, "Couldn't handle the event.")
    if ws:
        audit.record("subscription", ws, {"event": event["type"], "id": event["id"], **changed},
                     workspace=ws, actor="stripe", actor_type="system")
    return {"received": True}
