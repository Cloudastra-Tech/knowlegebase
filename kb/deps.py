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
import members_store
import sessions_store
import sso
import workspaces
from kb import logs

log = logging.getLogger("uvicorn.error")   # shows in the uvicorn console
# Kept with the session (for signing out at the provider), never sent to the browser.
PRIVATE_USER_KEYS = ("id_token",)


def current_user(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    user = sessions_store.get(token)
    if not user:
        raise HTTPException(401, "Please sign in.")
    # Workspace and role are looked up again on EVERY request, so removing someone
    # (or their HR role) takes effect now, not when their 12-hour session ends.
    try:
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
    return user


def public_user(user):
    return {k: v for k, v in user.items() if k not in PRIVATE_USER_KEYS}


def current_role(user=Depends(current_user), x_role: str = Header(default="hr")):
    """HR only if the ACCOUNT is HR or admin; X-Role can only lower it ("view as public")."""
    return "hr" if user["role"] in members_store.STAFF_ROLES and x_role == "hr" else "public"


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


def ws_of(user):
    ws = user.get("workspace") or workspaces.DEFAULT
    if not workspaces.valid(ws):
        raise HTTPException(400, "Invalid workspace on this account.")
    return ws


def public_url(request: Request, path):
    """Absolute URL of one of our pages, for providers to redirect back to."""
    base = os.getenv("PUBLIC_URL") or str(request.base_url)
    return base.rstrip("/") + path
