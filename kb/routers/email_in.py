"""
Email-in (kb/services/email_in.py has how it answers):

  Set up, from the Integrations screen
    GET    /api/admin/email-in        on or off, the workspace's address, whether this
                                      server has email-in set up             (HR / Admin)
    PUT    /api/admin/email-in        {enabled}                               (admin)

  Called by the inbound-mail provider
    POST   /api/email-in/postmark     Postmark's inbound webhook, with the HTTP Basic
                                      credentials in its URL (EMAIL_IN_WEBHOOK_USER,
                                      EMAIL_IN_WEBHOOK_PASSWORD). Wrong or missing: 403,
                                      which also stops Postmark retrying. Every email it
                                      was right to send answers 200, taken in or dropped,
                                      so it isn't sent again; the answer follows from a job.
"""

import base64
import binascii
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

import email_in_store
from kb.audit import audited
from kb.deps import public_url, require_admin, require_hr, ws_of
from kb.services import email_in

router = APIRouter()
log = logging.getLogger("uvicorn.error")

WEBHOOK_PATH = "/api/email-in/postmark"


# ---- Set up ---------------------------------------------------------------------------------
def _view(request, ws, user):
    s = email_in_store.settings(ws)
    return {"enabled": s["enabled"], "configured": email_in.configured(),
            "address": email_in.address(ws), "provider": "postmark",
            "webhook_url": public_url(request, WEBHOOK_PATH),
            "can_manage": user["role"] == "admin",
            "updated_by": s["updated_by"], "updated_at": s["updated_at"]}


@router.get("/api/admin/email-in")
def email_in_settings(request: Request, user=Depends(require_hr)):
    return _view(request, ws_of(user), user)


class SettingsIn(BaseModel):
    enabled: bool


@router.put("/api/admin/email-in")
def set_email_in(body: SettingsIn, request: Request, user=Depends(require_admin),
                 audit=audited("email_in.settings")):
    """Turn email-in on or off for the workspace (off by default)."""
    ws = ws_of(user)
    if body.enabled and not email_in.configured():
        raise HTTPException(400, "Email-in isn't set up on this server yet (EMAIL_IN_DOMAIN, "
                                 "EMAIL_IN_WEBHOOK_USER, EMAIL_IN_WEBHOOK_PASSWORD).")
    before = email_in_store.enabled(ws)
    email_in_store.set_enabled(ws, body.enabled, user["username"])
    audit.record("workspace", ws, {"enabled": [before, body.enabled]}, user=user)
    return _view(request, ws, user)


# ---- The provider's webhook --------------------------------------------------------------------
def email_in_webhook(request: Request):
    """The webhook's HTTP Basic credentials, checked in constant time. Postmark has
    no request signature (https://postmarkapp.com/developer/webhooks/webhooks-overview);
    a 403 makes it stop retrying (https://postmarkapp.com/developer/webhooks/inbound-webhook)."""
    if not email_in.configured():
        raise HTTPException(403, "Email-in isn't set up on this server.")
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    user = password = None
    if scheme.lower() == "basic":
        try:
            user, _, password = base64.b64decode(value.strip(), validate=True).decode(
                "utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError):
            pass
    if not email_in.credentials_ok(user, password):
        raise HTTPException(403, "Not allowed.")


@router.post(WEBHOOK_PATH, dependencies=[Depends(email_in_webhook)])
def postmark_inbound(body: dict = Body(...), audit=audited("email_in.receive")):
    try:
        result = email_in.take("postmark", body, audit)
    except ValueError:
        raise HTTPException(400, "Not an inbound email.")
    log.info("Email-in: an email from Postmark: %s", result)
    return {"ok": True}
