"""Outbound webhooks (platform.md PL-20; kb/services/webhooks.py), admins only:
add one (its signing secret is shown once), choose its events, turn it off or
on, send a test event, see what was delivered and send a delivery again, and
rotate its secret (the new one shown once, the old one signing for a while)."""

import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import url_guard
import webhooks_store
from kb.audit import audited
from kb.deps import require_admin, ws_of
from kb.services import webhooks

router = APIRouter()


class WebhookIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    events: list[str] = Field(min_length=1, max_length=len(webhooks.EVENTS))
    description: str | None = Field(default=None, max_length=200)


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    events: list[str] | None = Field(default=None, max_length=len(webhooks.EVENTS))
    description: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class RotateIn(BaseModel):
    grace_hours: int = Field(default=webhooks.ROTATE_GRACE_HOURS, ge=0,
                             le=webhooks.ROTATE_GRACE_MAX_HOURS)


def _events(events):
    unknown = set(events) - set(webhooks.EVENTS)
    if unknown or not events:
        raise HTTPException(400, "Pick at least one of the events listed.")
    return events


def _url(url):
    url = url.strip()
    try:
        webhooks.check_url(url)
    except url_guard.UnsafeURL as e:
        raise HTTPException(400, f"{e} Webhooks go only to public https:// addresses.")
    return url


def _host(url):
    """What the audit log keeps of an address: its host (a URL can hold a token)."""
    return urlsplit(url).hostname


def _hook(user, hook_id):
    hook = webhooks_store.get(ws_of(user), hook_id)
    if not hook:
        raise HTTPException(404, "No such webhook.")
    return hook


@router.get("/api/admin/webhooks")
def list_webhooks(user=Depends(require_admin)):
    return {"webhooks": webhooks_store.list_hooks(ws_of(user)),
            "events": [{"id": k, "label": v} for k, v in webhooks.EVENTS.items()],
            "retry_hours": webhooks.RETRY_FOR // 3600}


@router.post("/api/admin/webhooks")
def add_webhook(body: WebhookIn, user=Depends(require_admin), audit=audited("webhook.create")):
    ws = ws_of(user)
    if webhooks_store.count(ws) >= webhooks_store.MAX_PER_WORKSPACE:
        raise HTTPException(400, f"A workspace can have up to "
                                 f"{webhooks_store.MAX_PER_WORKSPACE} webhooks.")
    secret = webhooks_store.new_secret()
    hook = webhooks_store.create(ws, _url(body.url), _events(body.events),
                                 (body.description or "").strip() or None, user["username"],
                                 secret)
    audit.record("webhook", hook["id"], {"host": _host(hook["url"]), "events": hook["events"]},
                 user=user)
    return {"webhook": hook, "secret": secret}


@router.patch("/api/admin/webhooks/{hook_id}")
def change_webhook(hook_id: str, body: WebhookPatch, user=Depends(require_admin),
                   audit=audited("webhook.update")):
    hook = _hook(user, hook_id)
    fields = {}
    if body.url is not None:
        fields["url"] = _url(body.url)
    if body.events is not None:
        fields["events"] = _events(body.events)
    if body.description is not None:
        fields["description"] = body.description.strip() or None
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    ws = ws_of(user)
    hook = webhooks_store.update(ws, hook_id, **fields) if fields else hook
    if body.enabled is False:
        webhooks_store.fail_pending(ws, hook_id, "The webhook was turned off.")
    details = {k: v for k, v in fields.items() if k not in ("description", "url")}
    if "url" in fields:
        details["host"] = _host(fields["url"])
    audit.record("webhook", hook_id, details, user=user)
    return hook


@router.delete("/api/admin/webhooks/{hook_id}")
def delete_webhook(hook_id: str, user=Depends(require_admin), audit=audited("webhook.delete")):
    hook = _hook(user, hook_id)
    webhooks_store.delete(ws_of(user), hook_id)
    audit.record("webhook", hook_id, {"host": _host(hook["url"])}, user=user)
    return {"ok": True}


@router.post("/api/admin/webhooks/{hook_id}/test")
def test_webhook(hook_id: str, user=Depends(require_admin), audit=audited("webhook.test")):
    """Send a webhook.test event now, whatever the events it wants; returns the delivery."""
    hook = _hook(user, hook_id)
    delivery = webhooks.send_test(ws_of(user), hook)
    audit.record("webhook", hook_id, {"status": delivery["status"],
                                      "response_code": delivery["response_code"]}, user=user)
    return {k: v for k, v in delivery.items() if k != "payload"}


@router.post("/api/admin/webhooks/{hook_id}/secret")
def rotate_secret(hook_id: str, body: RotateIn | None = None, user=Depends(require_admin),
                  audit=audited("webhook.rotate_secret")):
    """A new signing secret, shown once. The old one keeps signing alongside it for
    `grace_hours` (default 24, 0: it stops now), so the receiver can switch over."""
    hook = _hook(user, hook_id)
    grace = (body or RotateIn()).grace_hours
    hook, secret = webhooks.rotate_secret(ws_of(user), hook, grace)
    audit.record("webhook", hook_id, {"grace_hours": grace}, user=user)
    return {"webhook": hook, "secret": secret}


@router.post("/api/admin/webhooks/deliveries/{delivery_id}/redeliver")
def redeliver(delivery_id: str, user=Depends(require_admin),
              audit=audited("webhook.redeliver")):
    """Send a delivery again: a new delivery of the same event and body, queued now."""
    ws = ws_of(user)
    old = webhooks_store.delivery(ws, delivery_id)
    if not old:
        raise HTTPException(404, "No such delivery.")
    hook = _hook(user, old["webhook_id"])
    if not hook["enabled"]:
        raise HTTPException(400, "The webhook is off. Turn it on first.")
    new = webhooks.redeliver(ws, delivery_id)
    audit.record("webhook", hook["id"], {"delivery": delivery_id, "new_delivery": new["id"],
                                         "event": new["event"]}, user=user)
    return new


@router.get("/api/admin/webhooks/{hook_id}/deliveries")
def list_deliveries(hook_id: str, user=Depends(require_admin)):
    _hook(user, hook_id)
    return {"deliveries": webhooks_store.deliveries(ws_of(user), hook_id)}


@router.get("/api/admin/webhooks/deliveries/{delivery_id}")
def read_delivery(delivery_id: str, user=Depends(require_admin)):
    """One delivery with the body that was sent."""
    d = webhooks_store.delivery(ws_of(user), delivery_id)
    if not d:
        raise HTTPException(404, "No such delivery.")
    return {**d, "payload": json.loads(d["payload"])}
