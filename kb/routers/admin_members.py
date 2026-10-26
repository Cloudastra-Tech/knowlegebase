"""Members (admin): everyone in the workspace, their role and access."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import members_store
import sessions_store
from kb.deps import require_admin, ws_of

router = APIRouter()
log = logging.getLogger("uvicorn.error")


class MemberPatch(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(public|hr|admin)$")  # null: from groups
    disabled: bool | None = None


def _member_out(m, admin_days=None):
    source = ("override" if m["override_role"] else
              "groups" if m["group_role"] else "default")
    if admin_days is None:
        admin_days = members_store.group_admin_days(m["workspace"])
    return {"username": m["username"], "name": m["name"], "role": m["role"],
            "role_source": source, "override_role": m["override_role"],
            "group_role": m["group_role"], "groups": m["groups"],
            "groups_updated_at": m["groups_updated_at"],
            # admin from groups older than the limit: kept only as the last admin
            "admin_kept": members_store.group_admin_stale(m, admin_days),
            "disabled": m["disabled"],
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
    days = members_store.group_admin_days(ws)
    return {"members": [_member_out(m, days) for m in members_store.list_members(ws)]}


@router.patch("/api/admin/members/{username}")
def edit_member(username: str, body: MemberPatch, user=Depends(require_admin)):
    member = _workspace_member(user, username)
    if "role" in body.model_fields_set:
        members_store.set_role(member["username"], body.role, member["workspace"])
    if body.disabled is not None:
        members_store.set_disabled(member["username"], body.disabled)
        if body.disabled:          # out now, on every device, not at their next click
            sessions_store.delete_user(member["username"], member["workspace"])
    log.warning("%s changed %s's access: %s", user["username"], member["username"],
                body.model_dump(exclude_unset=True))
    m = next(x for x in members_store.list_members(member["workspace"])
             if x["username"] == member["username"])
    return _member_out(m)


@router.delete("/api/admin/members/{username}")
def delete_member(username: str, user=Depends(require_admin)):
    member = _workspace_member(user, username)
    members_store.remove(member["username"])
    sessions_store.delete_user(member["username"], member["workspace"])
    log.warning("%s removed %s", user["username"], member["username"])
    return {"ok": True}


@router.post("/api/admin/members/{username}/sign-out")
def sign_out_member(username: str, user=Depends(require_admin)):
    """End every session a member has. They can sign in again (unless their access
    is off): for a lost laptop, say."""
    member = _workspace_member(user, username)
    ended = sessions_store.delete_user(member["username"], member["workspace"])
    log.warning("%s signed %s out everywhere (%d sessions)", user["username"],
                member["username"], ended)
    return {"ok": True, "ended": ended}
