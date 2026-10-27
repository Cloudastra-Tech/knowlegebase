"""Members (admin): everyone in the workspace, their role and access, and the
requests to join it (join_store.py, kb/services/joining.py). Every change goes
in the audit log (kb/audit.py). Invites: routers/invites.py."""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import join_store
import mailer
import members_store
import scim_store
import sessions_store
from kb.audit import audited
from kb.deps import require_admin, ws_of
from kb.services import joining, sync, webhooks

router = APIRouter()
log = logging.getLogger("uvicorn.error")


class MemberPatch(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(public|hr|admin)$")  # null: from groups
    disabled: bool | None = None


SCIM_OWNS = ("Your identity provider manages this member through SCIM, and would undo "
             "it. Change it there.")


def _scim_on(ws):
    """The workspace's identity provider can provision it now (an active SCIM token)."""
    return any(t["revoked_at"] is None for t in scim_store.list_tokens(ws))


def _scim_managed(m, scim_on):
    """SCIM provisioned or changed them, and still can: their access (active) is
    the provider's to set, and removing them here would only last until its
    next push. Their role picked here still wins over their groups."""
    return bool(scim_on and m.get("scim_updated_at") and not m.get("anonymised_at"))


def _member_out(m, admin_days=None, scim_on=None):
    source = ("override" if m["override_role"] else
              "groups" if m["group_role"] else "default")
    if admin_days is None:
        admin_days = members_store.group_admin_days(m["workspace"])
    if scim_on is None:
        scim_on = _scim_on(m["workspace"])
    return {"username": m["username"], "name": m["name"], "role": m["role"],
            "role_source": source, "override_role": m["override_role"],
            "group_role": m["group_role"], "groups": m["groups"],
            "groups_updated_at": m["groups_updated_at"],
            # admin from groups older than the limit: kept only as the last admin
            "admin_kept": members_store.group_admin_stale(m, admin_days),
            "disabled": m["disabled"], "scim_managed": _scim_managed(m, scim_on),
            "has_password": m["has_password"], "last_login_at": m["last_login_at"],
            "created_at": m["created_at"]}


def _workspace_member(user, username):
    member = members_store.get(username)
    if not member or member["workspace"] != ws_of(user):
        raise HTTPException(404, "This workspace has no such member.")
    if member["username"] == user["username"]:
        # Also keeps at least one admin: the one asking.
        raise HTTPException(400, "You can't change your own access. Ask another admin.")
    return member


@router.get("/api/admin/members")
def list_members(user=Depends(require_admin)):
    ws = ws_of(user)
    members_store.expire_group_admins(ws)            # so the list shows who's admin now
    days, scim_on = members_store.group_admin_days(ws), _scim_on(ws)
    return {"members": [_member_out(m, days, scim_on) for m in members_store.list_members(ws)]}


@router.patch("/api/admin/members/{username}")
def edit_member(username: str, body: MemberPatch, user=Depends(require_admin),
                audit=audited("member.update")):
    member = _workspace_member(user, username)
    ws = member["workspace"]
    # Turning a SCIM member's access OFF stays possible (an emergency lockout);
    # back on is the provider's call.
    if body.disabled is False and _scim_managed(member, _scim_on(ws)):
        raise HTTPException(409, SCIM_OWNS)
    if "role" in body.model_fields_set:
        members_store.set_role(member["username"], body.role, ws)
    if body.disabled is not None:
        members_store.set_disabled(member["username"], body.disabled, ws)
        if body.disabled:          # out now, on every device, not at their next click
            sessions_store.delete_user(member["username"], ws)
    audit.record("member", member["username"], body.model_dump(exclude_unset=True), user=user)
    if body.disabled and not member["disabled"]:
        webhooks.member_disabled(ws, member["username"], "admin")
    m = next(x for x in members_store.list_members(ws) if x["username"] == member["username"])
    return _member_out(m)


@router.delete("/api/admin/members/{username}")
def delete_member(username: str, user=Depends(require_admin), audit=audited("member.remove")):
    member = _workspace_member(user, username)
    if _scim_managed(member, _scim_on(member["workspace"])):
        raise HTTPException(409, SCIM_OWNS)
    members_store.remove(member["username"], member["workspace"])
    sessions_store.delete_user(member["username"], member["workspace"])
    # Their own Gmail / Calendar go with them (kb/services/sync.py disconnect_member).
    sync.disconnect_member(member["workspace"], member["username"])
    audit.record("member", member["username"], {"role": member["role"]}, user=user)
    _tell_removed(member)
    return {"ok": True}


def _tell_removed(member):
    """MAIL_MEMBER_REMOVED=on: email someone an admin removed that they were.
    Best effort: the removal stands whether or not it's sent."""
    if (os.getenv("MAIL_MEMBER_REMOVED", "off").strip().lower() not in ("on", "true", "1", "yes")
            or "@" not in member["username"]):
        return
    try:
        mailer.send(member["username"], "member_removed",
                    workspace_name=members_store.workspace_name(member["workspace"]))
    except mailer.MailError:
        log.warning("Couldn't email %s that they were removed", member["username"])


@router.post("/api/admin/members/{username}/sign-out")
def sign_out_member(username: str, user=Depends(require_admin),
                    audit=audited("member.sign_out")):
    """End every session a member has. They can sign in again (unless their access
    is off): for a lost laptop, say."""
    member = _workspace_member(user, username)
    ended = sessions_store.delete_user(member["username"], member["workspace"])
    audit.record("member", member["username"], {"sessions_ended": ended}, user=user)
    return {"ok": True, "ended": ended}


# ---- Requests to join (the joining policy's 'request', Access > Sign-in) ------------------
class ApproveIn(BaseModel):
    role: str = Field(default="public", pattern=r"^(public|hr|admin)$")


def _request_out(r):
    return {"id": r["id"], "email": r["email"], "name": r["name"], "provider": r["provider"],
            "status": r["status"], "created_at": r["created_at"],
            "last_seen_at": r["last_seen_at"], "expires_at": r["expires_at"],
            "notified": r["notified_at"] is not None}


@router.get("/api/admin/members/requests")
def list_join_requests(user=Depends(require_admin)):
    """Pending requests, oldest first (overdue ones are expired first)."""
    ws = ws_of(user)
    joining.expire(ws)
    return {"requests": [_request_out(r) for r in join_store.pending(ws)],
            "policy": join_store.workspace_policy(ws),
            "request_days": join_store.request_days(), "deny_days": join_store.deny_days()}


_CLOSED = {"gone": (404, "This request was already answered, or it expired."),
           "taken": (409, "That address joined another workspace meanwhile.")}


@router.post("/api/admin/members/requests/{request_id}/approve")
def approve_join_request(request_id: int, body: ApproveIn, user=Depends(require_admin),
                         audit=audited("join_request.approve")):
    """Let them in with the role picked; their next sign-in works."""
    ws = ws_of(user)
    try:
        r = join_store.approve(ws, request_id, body.role, user["username"])
    except join_store.RequestClosed as e:
        raise HTTPException(*_CLOSED[e.reason])
    audit.record("join_request", request_id, {"email": r["email"], "role": body.role},
                 user=user)
    joining.approved(ws, r)
    return _request_out(r)


@router.post("/api/admin/members/requests/{request_id}/deny")
def deny_join_request(request_id: int, user=Depends(require_admin),
                      audit=audited("join_request.deny")):
    """Turn it down; they can't ask again for JOIN_DENY_DAYS."""
    ws = ws_of(user)
    try:
        r = join_store.deny(ws, request_id, user["username"])
    except join_store.RequestClosed as e:
        raise HTTPException(*_CLOSED[e.reason])
    audit.record("join_request", request_id, {"email": r["email"]}, user=user)
    return _request_out(r)
