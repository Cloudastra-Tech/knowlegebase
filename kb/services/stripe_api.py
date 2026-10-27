"""
Stripe, as far as billing needs it (kb/services/billing.py): Checkout to start a
subscription, the Customer Portal to change or cancel one, and checking that a
webhook really came from Stripe. Plain HTTPS calls (no SDK).

Settings (.env), TEST MODE ONLY unless whoever runs the server says otherwise:

    STRIPE_SECRET_KEY        sk_test_... (or a restricted rk_test_... key). A live
                             key (sk_live_ / rk_live_) is refused unless
                             STRIPE_ALLOW_LIVE=on.
    STRIPE_WEBHOOK_SECRET    whsec_... of the endpoint /api/billing/webhook
    STRIPE_PRICE_<PLAN>      the monthly per-seat price of each plan, e.g.
                             STRIPE_PRICE_BUSINESS=price_... (or plans.stripe_price)
    STRIPE_TAX=on            Stripe Tax works out VAT / sales tax at checkout

deploy.md, "Billing (Stripe)", has the owner's steps.
"""

import hashlib
import hmac
import json
import os
import time
import urllib.parse

import httpx

API = "https://api.stripe.com/v1"
TIMEOUT = 15
TOLERANCE = 5 * 60            # a webhook signed longer ago than this is refused
TRANSPORT = None              # tests put an httpx.MockTransport here


class StripeError(Exception):
    """A call to Stripe failed. The message is safe to show (no key)."""


class BadSignature(Exception):
    """A webhook whose Stripe-Signature doesn't check out."""


def _on(name):
    return os.getenv(name, "").strip().lower() in ("1", "on", "true", "yes")


def secret_key():
    """The API key, or None if Stripe isn't set up (or it's a live key that isn't allowed)."""
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        return None
    if "_live_" in key and not _on("STRIPE_ALLOW_LIVE"):
        return None
    return key


def configured():
    return secret_key() is not None


def live_key_refused():
    """A live key is set but not allowed: the billing screen says why checkout is off."""
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    return bool(key) and "_live_" in key and not _on("STRIPE_ALLOW_LIVE")


def price_for(plan):
    return (os.getenv(f"STRIPE_PRICE_{plan['id'].upper()}", "").strip()
            or plan.get("stripe_price") or None)


def _form(data, prefix=""):
    """Stripe's form encoding of nested values: a[b][0][c]=..."""
    out = []
    items = data.items() if isinstance(data, dict) else enumerate(data)
    for k, v in items:
        name = f"{prefix}[{k}]" if prefix else str(k)
        if v is None:
            continue
        if isinstance(v, (dict, list, tuple)):
            out.extend(_form(v, name))
        elif isinstance(v, bool):
            out.append((name, "true" if v else "false"))
        else:
            out.append((name, str(v)))
    return out


def _post(path, data, idempotency_key=None):
    key = secret_key()
    if not key:
        raise StripeError("Stripe isn't set up on this server (STRIPE_SECRET_KEY).")
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/x-www-form-urlencoded"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT) as client:
            r = client.post(API + path, content=urllib.parse.urlencode(_form(data)),
                            headers=headers)
    except httpx.HTTPError as e:
        raise StripeError(f"Couldn't reach Stripe ({type(e).__name__}).") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        message = (body.get("error") or {}).get("message") or f"HTTP {r.status_code}"
        raise StripeError(f"Stripe refused: {message}")
    return body


def checkout_session(*, workspace, plan, price, seats, success_url, cancel_url, customer=None,
                     email=None):
    """A Checkout Session for a subscription to `price`, `seats` of it. Returns
    {"id", "url"}: the browser goes to url."""
    data = {"mode": "subscription", "client_reference_id": workspace,
            "line_items": [{"price": price, "quantity": max(1, seats)}],
            "success_url": success_url, "cancel_url": cancel_url,
            "metadata": {"workspace": workspace, "plan": plan},
            "subscription_data": {"metadata": {"workspace": workspace, "plan": plan}}}
    if customer:
        data["customer"] = customer
    elif email:
        data["customer_email"] = email
    if _on("STRIPE_TAX"):
        data["automatic_tax"] = {"enabled": True}
    body = _post("/checkout/sessions", data)
    return {"id": body.get("id"), "url": body.get("url")}


def portal_session(*, customer, return_url):
    body = _post("/billing_portal/sessions", {"customer": customer, "return_url": return_url})
    return {"id": body.get("id"), "url": body.get("url")}


def verify_webhook(payload, header, secret=None, now=None):
    """The event a webhook carries, if its Stripe-Signature header is a valid
    signature of `payload` (bytes) made with the endpoint's secret in the last
    TOLERANCE seconds; else BadSignature. Stripe signs "<t>.<payload>" with
    HMAC-SHA256 and may send several v1 signatures (while a secret is rolled)."""
    secret = secret if secret is not None else os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise BadSignature("STRIPE_WEBHOOK_SECRET isn't set")
    parts = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    stamps = [v for k, v in parts if k.strip() == "t"]
    signatures = [v.strip() for k, v in parts if k.strip() == "v1"]
    if len(stamps) != 1 or not signatures:
        raise BadSignature("no timestamp or v1 signature")
    try:
        stamp = int(stamps[0])
    except ValueError:
        raise BadSignature("bad timestamp") from None
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload,
                        hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise BadSignature("signature doesn't match")
    if abs((now or time.time()) - stamp) > TOLERANCE:
        raise BadSignature("too old")
    try:
        event = json.loads(payload)
    except ValueError:
        raise BadSignature("not JSON") from None
    if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
        raise BadSignature("not an event")
    return event


def sign(payload, secret, stamp=None):
    """A Stripe-Signature header for `payload` (tests, and checking a setup by hand)."""
    stamp = int(stamp or time.time())
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={sig}"
