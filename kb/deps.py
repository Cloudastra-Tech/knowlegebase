"""
Who is asking: the FastAPI dependencies every guarded endpoint uses.

ROLES come from the account (members_store.py): public, hr or admin. Admin
counts as HR everywhere here. An HR / Admin account can send "X-Role: public" to
see the app as a public user would; the header can never raise anyone TO HR.
"""

import logging
import os
import sqlite3

from fastapi import Depends, Header, HTTPException, Request

import auth
import db
import groups_store
import lifecycle_store
import members_store
import sessions_store
import sso
import workspaces
from kb import logs, operators

log = logging.getLogger("uvicorn.error")   # shows in the uvicorn console
# Kept with the session (for signing out at the provider), never sent to the browser.
PRIVATE_USER_KEYS = ("id_token",)


# While a workspace is being deleted its admins may still sign in, but only to
# export it, cancel the deletion, or sign out (lifecycle_store.py).
WHILE_DELETING = ("/api/auth/", "/api/admin/workspace", "/api/index/status",
                  "/api/question-log/settings", "/api/chat-settings")


def current_user(request: Request, authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    user = sessions_store.get(token)
    if not user or user.get("operator"):          # an operator's session isn't a member's
        raise HTTPException(401, "Please sign in.")
    # Workspace and role are looked up again on EVERY request, so removing someone
    # (or their HR role) takes effect now, not when their 12-hour session ends.
    try:
        if user.get("impersonator"):
            access = operators.support_access(user)
        else:
            access = (sso.access_for(user["username"], user.get("org"), user.get("connection"))
                      if user.get("provider")
                      else auth.access_for(user["username"]))
    except (OSError, sqlite3.Error):         # e.g. the database can't be read
        log.exception("Couldn't read the sign-in settings")
        raise HTTPException(503, "Sign-in settings can't be read. Please tell your admin.")
    if not access:
        sessions_store.delete(token)
        raise HTTPException(401, "Please sign in.")
    user = {**user, **access}
    logs.bind_user(user)               # the request's log lines say whose it is (not the email)
    db.set_tenant(ws_of(user))         # its connections from now on: this workspace's rows
    if lifecycle_store.status(user["workspace"]) == lifecycle_store.DELETING and not \
            request.url.path.startswith(WHILE_DELETING):
        raise HTTPException(423, "This workspace is being deleted. Export it or cancel the "
                                 "deletion under Access > Data.")
    if user.get("impersonator"):
        operators.audit_request(user, request)
    return user


def public_user(user):
    return {k: v for k, v in user.items() if k not in PRIVATE_USER_KEYS}


def current_role(user=Depends(current_user), x_role: str = Header(default="hr")):
    """HR only if the ACCOUNT is HR or admin; X-Role can only lower it ("view as public")."""
    return "hr" if user["role"] in members_store.STAFF_ROLES and x_role == "hr" else "public"


def current_viewer(user=Depends(current_user), role=Depends(current_role)):
    """Who is asking, to the CONTENT (access.py): their principals now (themselves,
    their groups, the built-in hr group while viewing the app as HR / Admin),
    with the role. Roles gate screens; this gates what they may read."""
    return groups_store.principals_for(ws_of(user), user["username"], role)


def require_hr(user=Depends(current_user), role=Depends(current_role)):
    if role != "hr":
        raise HTTPException(403, "Switch to HR / Admin to manage documents and integrations."
                            if user["role"] in members_store.STAFF_ROLES else
                            "Only HR / Admin accounts can manage documents and integrations.")
    return user


def require_admin(user=Depends(current_user), role=Depends(current_role)):
    """Admins only, and not while viewing the app as a public user."""
    if user["role"] != "admin" or role != "hr":
        raise HTTPException(403, "Switch to HR / Admin to change sign-in settings."
                            if user["role"] == "admin" else
                            "Only admins can change sign-in settings.")
    return user


def require_tenant_admin(user=Depends(require_admin)):
    """An admin of the workspace itself, not Cloudastra support opened as one: for
    what support must never do (delete the workspace, grant itself access, erase
    someone's data)."""
    if user.get("impersonator"):
        raise HTTPException(403, "Support can't do this: ask one of the workspace's admins.")
    return user


def ws_of(user):
    ws = user.get("workspace") or workspaces.DEFAULT
    if not workspaces.valid(ws):
        raise HTTPException(400, "Invalid workspace on this account.")
    return ws


def public_url(request: Request, path):
    """Absolute URL of one of our pages, for providers to redirect back to."""
    base = os.getenv("PUBLIC_URL") or str(request.base_url)
    return base.rstrip("/") + path
