"""
Invites: an admin invites someone by email (invites_store.py, mailer.py), and
they accept from the emailed link (/invite/<token>, the site's accept page).

An invite to an address on a domain this workspace has verified, when it
requires single sign-on, is accepted by signing in through the workspace's
provider: accepting adds them with the invited role and no password. Anyone
else sets a password when they accept, and is signed in. Sending is admin only
and rate-limited (it emails anyone); every invite, resend, revoke and
acceptance goes in the audit log.
"""

import re
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import auth
import invites_store
import mailer
import members_store
import sessions_store
from kb.audit import audited
from kb.deps import public_url, require_admin, ws_of
from kb.rate_limit import rate_limit, user_limit
from kb.settings import SESSION_SECONDS

router = APIRouter()

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
ROLE_LABEL = {"public": "a member", "hr": "HR / Admin", "admin": "an admin"}
REFUSED = {
    "member": (409, "{email} is already a member of this workspace."),
    "taken": (409, "{email} can't be invited: the address belongs to another workspace."),
    "pending": (409, "{email} already has an invite on its way. Resend it from the list."),
}
CANT_ACCEPT = {
    "invalid": (404, "This invite link isn't valid. Check you copied all of it."),
    "expired": (410, "This invite has expired. Ask your admin to send a new one."),
    "revoked": (410, "This invite was withdrawn. Ask your admin if you still need access."),
    "accepted": (410, "This invite has already been used. Sign in instead."),
    "member": (409, "You're already a member of this workspace. Sign in instead."),
    "taken": (409, "Your address belongs to another workspace, so it can't join this one."),
}


class InviteIn(BaseModel):
    email: str = Field(max_length=254)
    role: str = Field(default="public", pattern=r"^(public|hr|admin)$")


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


class AcceptIn(TokenIn):
    name: str | None = Field(default=None, max_length=100)
    password: str | None = Field(default=None, max_length=200)


def method_for(workspace, email):
    """How the invitee will sign in: "sso" (their domain is verified here and
    single sign-on is required), "password", or None if neither is possible
    (passwords are off on this server)."""
    domain = email.rpartition("@")[2]
    if (members_store.workspace_for_domain(domain) == workspace
            and members_store.requires_sso(workspace)):
        return "sso"
    return "password" if auth.password_login_on() else None


def _invite_out(i):
    return {"id": i["id"], "email": i["email"], "role": i["role"], "status": i["status"],
            "invited_by": i["invited_by"], "created_at": i["created_at"],
            "sent_at": i["sent_at"], "expires_at": i["expires_at"]}


def _send(request, user, invite, token):
    """Email the invite's link. Raises mailer.MailError."""
    mailer.send(invite["email"], "invite",
                inviter=user.get("name") or user["username"],
                workspace_name=members_store.workspace_name(invite["workspace"]),
                role_label=ROLE_LABEL[invite["role"]],
                link=public_url(request, f"/invite/{token}"),
                expires=time.strftime("%d %B %Y", time.gmtime(invite["expires_at"])) + " (UTC)")
    invites_store.mark_sent(invite["id"])


def _mail_failed(e):
    return HTTPException(502, f"{e} Check the mail settings (MAIL_BACKEND), then try again.")


@router.get("/api/admin/members/invites")
def list_invites(user=Depends(require_admin)):
    return {"invites": [_invite_out(i) for i in invites_store.list_open(ws_of(user))]}


@router.post("/api/admin/members/invite")
def create_invite(body: InviteIn, request: Request, user=Depends(require_admin),
                  audit=audited("invite.create")):
    user_limit(user, "invite")
    ws, email = ws_of(user), body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(400, "Enter an email address, like name@company.com.")
    if not method_for(ws, email):
        raise HTTPException(400, "Password sign-in is off on this server, so only people on "
                                 "a domain you've verified, with single sign-on required, "
                                 "can be invited.")
    try:
        invite, token = invites_store.create(ws, email, body.role, user["username"])
    except invites_store.InviteError as e:
        status, message = REFUSED[e.reason]
        raise HTTPException(status, message.format(email=email))
    try:
        _send(request, user, invite, token)
    except mailer.MailError as e:
        invites_store.delete(ws, invite["id"])         # never went out: as if not made
        raise _mail_failed(e)
    audit.record("invite", invite["id"], {"email": email, "role": body.role,
                                          "method": method_for(ws, email)}, user=user)
    return _invite_out({**invite, "sent_at": time.time()})


@router.post("/api/admin/members/invites/{invite_id}/resend")
def resend_invite(invite_id: int, request: Request, user=Depends(require_admin),
                  audit=audited("invite.resend")):
    """A new link (the last one stops working) with a new expiry, emailed again."""
    user_limit(user, "invite")
    renewed = invites_store.renew(ws_of(user), invite_id)
    if not renewed:
        raise HTTPException(404, "This workspace has no such invite still open.")
    invite, token = renewed
    try:
        _send(request, user, invite, token)
    except mailer.MailError as e:
        raise _mail_failed(e)
    audit.record("invite", invite_id, {"email": invite["email"]}, user=user)
    return _invite_out({**invite, "sent_at": time.time()})


@router.delete("/api/admin/members/invites/{invite_id}")
def revoke_invite(invite_id: int, user=Depends(require_admin), audit=audited("invite.revoke")):
    invite = invites_store.revoke(ws_of(user), invite_id)
    if not invite:
        raise HTTPException(404, "This workspace has no such invite still open.")
    audit.record("invite", invite_id, {"email": invite["email"]}, user=user)
    return {"ok": True}


def _usable(token):
    """The pending invite for a link's token, else HTTPException."""
    invite = invites_store.find(token)
    reason = "invalid" if not invite else invite["status"]
    if reason != "pending":
        status, message = CANT_ACCEPT[reason]
        raise HTTPException(status, message)
    return invite


@router.post("/api/auth/invite/lookup")
def lookup_invite(body: TokenIn, request: Request):
    """What the accept page shows: whose invite, to which workspace, and how
    they'll sign in. (A POST, so the token stays out of URLs and logs.)"""
    rate_limit(request, "invite")
    invite = _usable(body.token)
    return {"email": invite["email"], "role": invite["role"],
            "workspace_name": members_store.workspace_name(invite["workspace"]),
            "method": method_for(invite["workspace"], invite["email"]),
            "expires_at": invite["expires_at"]}


@router.post("/api/auth/invite/accept")
def accept_invite(body: AcceptIn, request: Request, audit=audited("invite.accept")):
    """Join the workspace. With a password: signed in now ({token, user}). With
    single sign-on: {sso: true, email}; they sign in through the provider next."""
    rate_limit(request, "invite")
    invite = _usable(body.token)
    method = method_for(invite["workspace"], invite["email"])
    if not method:
        raise HTTPException(400, "Password sign-in is off on this server. Ask your admin.")
    password_hash = None
    if method == "password":
        if len(body.password or "") < 8:
            raise HTTPException(400, "Choose a password of at least 8 characters.")
        password_hash = auth.hash_password(body.password)
    name = " ".join((body.name or "").split()) or None
    try:
        invite = invites_store.accept(body.token, name, password_hash)
    except invites_store.InviteError as e:
        status, message = CANT_ACCEPT[e.reason]
        raise HTTPException(status, message)
    ws, email = invite["workspace"], invite["email"]
    audit.record("invite", invite["id"], {"email": email, "role": invite["role"],
                                          "method": method},
                 workspace=ws, actor=email)
    if method == "sso":
        return {"sso": True, "email": email}
    access = auth.access_for(email)
    if not access:                      # e.g. turned off between the two lines above
        raise HTTPException(403, "You joined, but can't sign in yet. Ask your admin.")
    member = members_store.get(email)
    user = {"username": email, "name": member["name"], **access}
    token = secrets.token_urlsafe(32)
    sessions_store.create(token, user, SESSION_SECONDS)
    members_store.log_event(ws, "password", email, "signed_in")
    return {"token": token, "user": user}
