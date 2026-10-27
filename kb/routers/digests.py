"""Digests (platform.md PL-30): everyone's own subscriptions to "what changed",
and the one-click unsubscribe link every digest email carries (no sign-in: the
link's token is the proof). What a digest holds is decided when it's sent, per
recipient (kb/services/digests.py)."""

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import digests_store
import members_store
from kb.audit import audited
from kb.deps import current_user, ws_of
from kb.rate_limit import rate_limit, user_limit
from kb.services import digests

router = APIRouter()

MAX_FOLDERS = 20
SEND_AGAIN_AFTER = 60          # seconds between two "send it now" of one subscription


class SubscriptionIn(BaseModel):
    frequency: str = "weekly"
    channel: str = "email"
    sources: list[str] = Field(default_factory=list, max_length=50)
    folders: list[str] = Field(default_factory=list, max_length=MAX_FOLDERS)


class SubscriptionPatch(BaseModel):
    frequency: str | None = None
    channel: str | None = None
    sources: list[str] | None = Field(default=None, max_length=50)
    folders: list[str] | None = Field(default=None, max_length=MAX_FOLDERS)
    enabled: bool | None = None


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


def _member(user):
    member = members_store.get(user["username"])
    if not member or member["workspace"] != ws_of(user):
        raise HTTPException(403, "Only members of the workspace get digests.")
    return member


def _check(ws, member, frequency=None, channel=None, sources=None, folders=None):
    if frequency is not None and frequency not in digests_store.FREQUENCIES:
        raise HTTPException(400, "How often must be daily or weekly.")
    if channel is not None:
        if channel not in digests_store.CHANNELS:
            raise HTTPException(400, "Send it by email or Slack.")
        if channel == "slack" and not digests.channels_for(ws)["slack"]:
            raise HTTPException(400, "The Slack app isn't installed in this workspace yet.")
        if channel == "email" and "@" not in member["username"]:
            raise HTTPException(400, "Your account has no email address to send it to.")
    if sources:
        allowed = {s["id"] for s in digests.sources_for(ws, member)}
        if set(sources) - allowed:
            raise HTTPException(400, "You can only follow sources you can read.")
    if folders and any(not f or len(f) > 200 for f in folders):
        raise HTTPException(400, "A folder name is missing or too long.")


@router.get("/api/digests")
def list_subscriptions(user=Depends(current_user)):
    ws = ws_of(user)
    member = _member(user)
    return {"subscriptions": digests_store.subscriptions(ws, member["username"]),
            "sources": digests.sources_for(ws, member),
            "channels": digests.channels_for(ws),
            "frequencies": list(digests_store.FREQUENCIES)}


@router.post("/api/digests")
def subscribe(body: SubscriptionIn, user=Depends(current_user),
              audit=audited("digest.subscribe")):
    ws = ws_of(user)
    member = _member(user)
    _check(ws, member, body.frequency, body.channel, body.sources, body.folders)
    if len(digests_store.subscriptions(ws, member["username"])) >= \
            digests_store.MAX_SUBSCRIPTIONS:
        raise HTTPException(400, f"You can have up to {digests_store.MAX_SUBSCRIPTIONS} "
                                 "digests.")
    sub = digests_store.subscribe(ws, member["username"], body.frequency, body.channel,
                                  body.sources, body.folders)
    audit.record("digest", sub["id"], {"frequency": sub["frequency"],
                                       "channel": sub["channel"]}, user=user)
    return sub


def _own(user, sub_id):
    sub = digests_store.subscription(ws_of(user), sub_id)
    if not sub or sub["username"] != user["username"]:
        raise HTTPException(404, "No such digest.")
    return sub


@router.patch("/api/digests/{sub_id}")
def change_subscription(sub_id: int, body: SubscriptionPatch, user=Depends(current_user),
                        audit=audited("digest.update")):
    ws = ws_of(user)
    _own(user, sub_id)
    _check(ws, _member(user), body.frequency, body.channel, body.sources, body.folders)
    sub = digests_store.update(ws, sub_id, **body.model_dump(exclude_unset=True))
    audit.record("digest", sub_id, body.model_dump(exclude_unset=True,
                                                   exclude={"sources", "folders"}), user=user)
    return sub


@router.delete("/api/digests/{sub_id}")
def unsubscribe(sub_id: int, user=Depends(current_user), audit=audited("digest.unsubscribe")):
    _own(user, sub_id)
    digests_store.delete(ws_of(user), sub_id)
    audit.record("digest", sub_id, user=user)
    return {"ok": True}


@router.post("/api/digests/{sub_id}/send")
def send_now(sub_id: int, user=Depends(current_user), audit=audited("digest.send")):
    """Send the digest now (of the changes since the last one), to check it."""
    sub = _own(user, sub_id)
    _member(user)
    user_limit(user, "digest")
    if sub["last_sent_at"] and time.time() - sub["last_sent_at"] < SEND_AGAIN_AFTER:
        raise HTTPException(429, "It was just sent. Try again in a minute.")
    result = digests.send(sub)
    audit.record("digest", sub_id, {k: result[k] for k in ("sent", "entries") if k in result},
                 user=user)
    return {**result, "subscription": digests_store.subscription(ws_of(user), sub_id)}


# ---- The email's link ----------------------------------------------------------------------
def _by_token(request, token):
    rate_limit(request, "unsubscribe")
    sub = digests_store.by_token(token)
    if not sub:
        raise HTTPException(404, "This link doesn't work any more.")
    return sub


def _link_view(sub):
    return {"enabled": sub["enabled"], "frequency": sub["frequency"],
            "channel": sub["channel"],
            "workspace_name": members_store.workspace_name(sub["workspace"])}


@router.post("/api/digests/unsubscribe")
def unsubscribe_link(body: TokenIn, request: Request, audit=audited("digest.unsubscribe")):
    """One click from the email: that digest stops (it can be turned back on)."""
    sub = _by_token(request, body.token)
    if sub["enabled"]:
        sub = digests_store.update(sub["workspace"], sub["id"], enabled=False)
        audit.record("digest", sub["id"], {"via": "email_link"}, workspace=sub["workspace"],
                     actor=sub["username"])
    return _link_view(sub)


@router.post("/api/digests/resubscribe")
def resubscribe_link(body: TokenIn, request: Request, audit=audited("digest.update")):
    """Undo the unsubscribe, from the same page."""
    sub = _by_token(request, body.token)
    if not sub["enabled"]:
        sub = digests_store.update(sub["workspace"], sub["id"], enabled=True)
        audit.record("digest", sub["id"], {"enabled": True, "via": "email_link"},
                     workspace=sub["workspace"], actor=sub["username"])
    return _link_view(sub)
