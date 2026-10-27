"""
The browser extension (platform.md PL-24, extension/): a Chrome / Edge side panel
that asks through the public API, POST /api/v1/ask, with an API key of its own.

    GET    /api/extension            is it set up on this server, and my connected
                                     extensions (their keys)
    POST   /api/extension/connect    {redirect_uri} -> {code}: "Connect" on the web
                                     app's /extension/connect screen (signed in)
    POST   /api/v1/extension/exchange  {code} -> {key, expires_at, name, workspace}:
                                     the extension swaps the code for its key, once
    DELETE /api/v1/extension/key     the extension's "Disconnect": its own key is
                                     revoked (Authorization: Bearer <that key>)

HOW IT SIGNS IN. The extension opens the web app's /extension/connect screen
with chrome.identity.launchWebAuthFlow, whose redirect URL is
https://<extension id>.chromiumapp.org/. The person, signed in as usual (SSO,
password), clicks Connect: the server hands back a one-time code (the
sso.issue_code pattern: 60 seconds, once, kept in memory) and the screen sends the
browser to that redirect URL with it. The extension swaps the code for a key, so
the key itself is never in a URL (history, logs).

THE KEY. A personal API key (api_keys_store.py) with the `ask` scope only, for 90
days, marked client "extension": it acts as the person, with their role as it is
at each call (kb/api_auth.py), it shows in their API keys (where they can revoke
it), and its questions are logged and audited with surface "extension". It's
made when the code is swapped, not before, so a code nobody redeems leaves no key.

ONLY OUR EXTENSION. EXTENSION_ORIGINS lists the extension's origins
(chrome-extension://<id>, comma-separated): a redirect to any other extension id
is refused, so another extension can't be handed a key, and the same list is the
CORS allowlist for /api/v1 (kb/middleware.ExtensionCors). Not set: the extension
can't be connected.
"""

import os
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import api_keys_store
import members_store
import workspaces
from kb.api_auth import api_key
from kb.audit import audited
from kb.deps import current_user, ws_of
from kb.rate_limit import rate_limit

router = APIRouter()

CODE_SECONDS = 60
KEY_DAYS = 90
KEY_NAME = "Browser extension"
SCOPES = ["ask"]
REDIRECT = re.compile(r"^https://([a-p]{32})\.chromiumapp\.org/$")
ORIGIN = re.compile(r"^(?:chrome-extension|extension)://([a-p]{32})$")

_codes = {}                     # code -> (workspace, username, extension id, expires)
_codes_lock = threading.Lock()


def origins():
    """The extension origins EXTENSION_ORIGINS allows (CORS, and who may connect)."""
    return [o.strip().rstrip("/") for o in os.getenv("EXTENSION_ORIGINS", "").split(",")
            if ORIGIN.match(o.strip().rstrip("/"))]


def extension_ids():
    return {ORIGIN.match(o).group(1) for o in origins()}


def _issue(ws, username, extension_id):
    code = secrets.token_urlsafe(24)
    now = time.time()
    with _codes_lock:
        for c in [c for c, v in _codes.items() if v[3] < now]:
            del _codes[c]
        _codes[code] = (ws, username, extension_id, now + CODE_SECONDS)
    return code


def _redeem(code):
    with _codes_lock:
        found = _codes.pop(code or "", None)
    if not found or found[3] < time.time():
        return None
    return found[:3]


def _connected(ws, username):
    return [{k: key[k] for k in ("id", "name", "hint", "created_at", "last_used_at",
                                 "expires_at")}
            for key in api_keys_store.list_keys(ws, created_by=username, kind="personal")
            if key["client"] == "extension" and key["status"] == "active"]


# ---- The web app (a signed-in session) --------------------------------------------------
@router.get("/api/extension")
def extension_status(user=Depends(current_user)):
    ws = ws_of(user)
    return {"configured": bool(origins()),
            "api_enabled": api_keys_store.settings(ws)["api_enabled"],
            "connected": _connected(ws, user["username"]), "key_days": KEY_DAYS}


class ConnectIn(BaseModel):
    redirect_uri: str = Field(max_length=200)


@router.post("/api/extension/connect")
def connect_extension(body: ConnectIn, user=Depends(current_user),
                      audit=audited("extension.connect")):
    """A one-time code for the extension that asked (its chromiumapp.org redirect
    URL names it), if it's ours (EXTENSION_ORIGINS)."""
    ws = ws_of(user)
    m = REDIRECT.match(body.redirect_uri.strip())
    if not m:
        raise HTTPException(400, "That isn't a browser extension's sign-in address.")
    allowed = extension_ids()
    if not allowed:
        raise HTTPException(400, "The browser extension isn't set up on this server yet: ask "
                                 "your admin (EXTENSION_ORIGINS).")
    if m.group(1) not in allowed:
        raise HTTPException(400, "That extension isn't the Knowledge assistant's, so it can't "
                                 "be connected to your account.")
    if not api_keys_store.settings(ws)["api_enabled"]:
        raise HTTPException(403, "Your workspace's admins have turned the API off, and the "
                                 "extension asks through it.")
    code = _issue(ws, user["username"], m.group(1))
    audit.record("extension", m.group(1), {"scopes": SCOPES, "days": KEY_DAYS}, user=user)
    return {"code": code, "expires_in": CODE_SECONDS}


# ---- The extension ----------------------------------------------------------------------
class ExchangeIn(BaseModel):
    code: str = Field(min_length=1, max_length=100)


@router.post("/api/v1/extension/exchange", include_in_schema=False)
def exchange_code(body: ExchangeIn, request: Request, audit=audited("extension.connected")):
    """The extension's key for a code from /api/extension/connect: once, within a
    minute, for a person who is still a member and not turned off."""
    rate_limit(request, "extension_exchange")
    found = _redeem(body.code.strip())
    if not found:
        raise HTTPException(400, "That code has expired or was already used. Connect again.")
    ws, username, extension_id = found
    origin = (request.headers.get("origin") or "").rstrip("/")
    sent_by = ORIGIN.match(origin)
    if sent_by and sent_by.group(1) != extension_id:
        raise HTTPException(400, "That code was made for another extension.")
    member = members_store.check_group_admin(members_store.get(username))
    if not member or member["workspace"] != ws or member["disabled"] or \
            not workspaces.valid(ws):
        raise HTTPException(400, "That code has expired or was already used. Connect again.")
    try:
        row, key = api_keys_store.create(ws, username, KEY_NAME, SCOPES, member["role"],
                                         days=KEY_DAYS, client="extension")
    except api_keys_store.Refused as e:
        raise HTTPException(400, str(e))
    audit.record("api_key", row["id"], {"name": row["name"], "prefix": row["prefix"],
                                        "scopes": row["scopes"], "extension": extension_id,
                                        "client": "extension"},
                 workspace=ws, actor=username)
    return {"key": key, "expires_at": row["expires_at"], "name": member["name"],
            "workspace": ws}


@router.delete("/api/v1/extension/key", include_in_schema=False)
def disconnect_extension(p=api_key("ask"), audit=audited("extension.disconnect")):
    """The extension's own key stops working (its "Disconnect")."""
    if p.surface != "extension":
        raise HTTPException(400, "This isn't a browser extension's key.")
    row = api_keys_store.revoke(p.workspace, p.key_id, p.username, created_by=p.username)
    if not row:
        raise HTTPException(404, "That key is no longer active.")
    p.record(audit, "api_key", p.key_id, {"prefix": p.prefix})
    return {"revoked": True}
