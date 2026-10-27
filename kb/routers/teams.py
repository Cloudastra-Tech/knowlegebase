"""
The Microsoft Teams bot (kb/services/teams.py has how it answers):

  Set up, from the Integrations screen
    GET    /api/admin/teams           the workspace's Teams bot: which bot, which tenant,
                                      its settings and messaging endpoint      (HR / Admin)
    PUT    /api/admin/teams/app       {mode: custom, app_id, app_tenant, client_secret}: the
                                      customer's own Azure Bot; or {mode: central, tenant_id}:
                                      this server's bot, answering that tenant  (admin)
    GET    /api/admin/teams/package   the Teams app (manifest.json + icons) as a zip  (admin)
    PUT    /api/admin/teams/settings  {unknown_users}                             (admin)
    DELETE /api/admin/teams           disconnect: the bot's secret and tenant are forgotten (admin)

  Called by the Bot Connector: every request's JWT is checked (for the App ID
  of the bot it was sent to) before anything in it is used
    POST   /api/teams/messages                  this server's bot
    POST   /api/teams/apps/{app_key}/messages   a customer's own bot

Teams wants a quick answer: a message is only checked, noted and queued
(kb/services/teams.take); the answer follows from a job. A feedback click (an
adaptiveCard/action invoke) is answered in the response.
"""

import json
import logging
import re
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import teams_store
from kb.audit import audited
from kb.deps import public_url, require_admin, require_hr, ws_of
from kb.services import teams

router = APIRouter()
log = logging.getLogger("uvicorn.error")

SECRET = re.compile(r"^[\x21-\x7e]{16,200}$")      # printable, no spaces


def _status(install):
    if install and install["status"] == "connected":
        return "connected"
    if install and install["tenant_id"] and teams.app_of(install):
        return "configured"
    return "not_configured"


def _view(request, ws, user):
    install = teams_store.get(ws)
    keep = ("app_id", "app_tenant", "tenant_id", "has_secret", "last_activity_at",
            "updated_by", "updated_at")
    central = teams.central_app()
    return {"status": _status(install), "can_manage": user["role"] == "admin",
            "central_available": central is not None,
            "mode": (install or {}).get("mode") or "custom",
            **{k: (install or {}).get(k) for k in keep},
            "unknown_users": (install or {}).get("unknown_users") or "refuse",
            "bot_app_id": (install or {}).get("app_id") if (install or {}).get("mode") == "custom"
            else (central or {}).get("app_id"),
            "messaging_endpoint": public_url(request, teams.messaging_path(install))}


@router.get("/api/admin/teams")
def teams_settings(request: Request, user=Depends(require_hr)):
    return _view(request, ws_of(user), user)


class AppIn(BaseModel):
    mode: str = Field(pattern=r"^(central|custom)$")
    app_id: str = Field(default="", max_length=60)
    app_tenant: str = Field(default="", max_length=60)
    client_secret: str = Field(default="", max_length=200)
    tenant_id: str = Field(default="", max_length=60)


@router.put("/api/admin/teams/app")
def set_teams_app(body: AppIn, request: Request, user=Depends(require_admin),
                  audit=audited("teams.app")):
    """The customer's own Azure Bot (its secret sealed), or this server's bot
    answering the customer's tenant. A tenant is answered by one workspace."""
    ws = ws_of(user)
    try:
        if body.mode == "central":
            if not teams.central_app():
                raise HTTPException(400, "This server has no Teams bot of its own "
                                         "(TEAMS_APP_ID, TEAMS_APP_PASSWORD). Register your own "
                                         "bot instead.")
            tenant = body.tenant_id.strip()
            if not teams.GUID.match(tenant):
                raise HTTPException(400, "That isn't a Microsoft Entra tenant ID: it looks like "
                                         "00000000-0000-0000-0000-000000000000 (Entra admin "
                                         "center → Overview).")
            teams_store.use_central_app(ws, tenant, user["username"])
        else:
            app_id, app_tenant = body.app_id.strip(), body.app_tenant.strip()
            secret = body.client_secret.strip()
            if not teams.GUID.match(app_id):
                raise HTTPException(400, "That isn't a Microsoft App ID: copy it from the Azure "
                                         "Bot's Configuration page (a GUID).")
            if not teams.GUID.match(app_tenant):
                raise HTTPException(400, "That isn't a tenant ID: copy the App Tenant ID from "
                                         "the Azure Bot's Configuration page (a GUID).")
            if not secret and teams_store.get(ws) and (teams_store.get(ws) or {}).get(
                    "app_id") == app_id:
                secret = teams_store.app_secret(ws) or ""      # unchanged: kept as it was
            if not SECRET.match(secret):
                raise HTTPException(400, "Paste the client secret's Value (Certificates & "
                                         "secrets in the bot's app registration).")
            teams_store.set_custom_app(ws, app_id, app_tenant, secret, user["username"])
    except teams_store.TenantTaken:
        raise HTTPException(409, "That Microsoft tenant is already connected to another "
                                 "workspace here.")
    after = teams_store.get(ws)
    # The App ID and tenant are recorded; the secret never is.
    audit.record("teams_app", body.mode, {"mode": body.mode, "app_id": after["app_id"],
                                          "tenant": after["tenant_id"]}, user=user)
    return _view(request, ws, user)


@router.get("/api/admin/teams/package")
def teams_package(request: Request, user=Depends(require_admin)):
    """The Teams app to upload (Teams admin center → Manage apps → Upload, or
    Teams → Apps → Upload a custom app): manifest.json with this server's host and
    the bot's App ID, and the icons."""
    view = _view(request, ws_of(user), user)
    if not view["bot_app_id"]:
        raise HTTPException(400, "Connect the bot first: the app needs its Microsoft App ID.")
    data = teams.app_package(public_url(request, ""), view["bot_app_id"])
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition":
                             'attachment; filename="knowledge-teams-app.zip"'})


class SettingsIn(BaseModel):
    unknown_users: str = Field(pattern=r"^(refuse|public)$")


@router.put("/api/admin/teams/settings")
def set_teams_settings(body: SettingsIn, request: Request, user=Depends(require_admin),
                       audit=audited("teams.settings")):
    """People Teams knows but this workspace doesn't: refused (the default), or
    answered from public documents only."""
    ws = ws_of(user)
    before = (teams_store.get(ws) or {}).get("unknown_users") or "refuse"
    teams_store.set_settings(ws, user["username"], body.unknown_users)
    if before != body.unknown_users:
        audit.record("workspace", ws, {"unknown_users": [before, body.unknown_users]},
                     user=user)
    return _view(request, ws, user)


@router.delete("/api/admin/teams")
def teams_disconnect(request: Request, user=Depends(require_admin),
                     audit=audited("teams.disconnect")):
    """Stop answering in Teams: the bot's secret, the tenant and the settings are
    forgotten here. Remove the app in Teams too (deploy.md)."""
    ws = ws_of(user)
    install = teams_store.get(ws)
    if not install:
        raise HTTPException(400, "Teams isn't connected.")
    teams_store.remove(ws)
    audit.record("teams_tenant", install["tenant_id"], {"mode": install["mode"]}, user=user)
    return _view(request, ws, user)


# ---- Requests from the Bot Connector -------------------------------------------------------
@dataclass
class TeamsRequest:
    activity: dict
    install: dict
    app: dict


async def bot_connector(request: Request):
    """The activity, once the Bot Connector's token on it checks out for the bot
    it was sent to, and the workspace answering its tenant. Anything else is
    refused before it's used."""
    key = request.path_params.get("app_key")
    install = teams_store.by_app_key(key) if key else None
    app = teams.app_of(install) if (install or not key) else None
    if not app or (key and not install):
        raise HTTPException(401, "This request isn't from the Bot Connector.")
    try:
        activity = json.loads(await request.body())
    except ValueError:
        raise HTTPException(400, "Not JSON.")
    if not isinstance(activity, dict):
        raise HTTPException(400, "Not an activity.")
    try:
        await run_in_threadpool(teams.verify, request.headers.get("authorization"),
                                app["app_id"], activity)
    except teams.Refused as e:
        log.info("Teams: refused a request (%s)", e.why)
        raise HTTPException(e.status, "This request isn't from the Bot Connector.")
    tenant = teams.tenant_of(activity)
    if not key:
        install = teams_store.by_tenant(tenant)
        install = install if install and install["mode"] == "central" else None
    elif install["tenant_id"] != tenant:
        install = None
    return TeamsRequest(activity, install, app)


@router.post("/api/teams/messages")
@router.post("/api/teams/apps/{app_key}/messages")
async def teams_messages(req: TeamsRequest = Depends(bot_connector),
                         audit=audited("teams.activity")):
    if not req.install:                 # a tenant no workspace answers: nothing to do
        if req.activity.get("type") == "invoke":
            return {"statusCode": 200, "type": "application/vnd.microsoft.activity.message",
                    "value": teams.OTHER_ORG}
        return Response(status_code=200)
    if req.activity.get("type") == "invoke":
        return await run_in_threadpool(teams.take_invoke, req.install, req.app, req.activity)
    await run_in_threadpool(teams.take, req.install, req.activity)
    return Response(status_code=200)
