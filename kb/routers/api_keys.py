"""API keys (api_keys_store.py) and the workspace's settings for the public API
and the MCP server, as the site manages them with a signed-in session.

    GET    /api/me/api-keys            my keys, what I may give them, the API and MCP URLs
    POST   /api/me/api-keys            {name, scopes, days} -> the key, shown once
    DELETE /api/me/api-keys/{id}       revoke one of mine
    GET    /api/admin/api              the settings, and every key in the workspace   (admin)
    PUT    /api/admin/api              {api_enabled, mcp_enabled, mcp_tools}           (admin)
    POST   /api/admin/api/service-keys {name, scopes, days}: a key acting as a public
                                       user, shown once                                (admin)
    DELETE /api/admin/api/keys/{id}    revoke any key in the workspace                 (admin)
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import api_keys_store as store
from kb.audit import audited
from kb.deps import current_user, public_url, require_admin, ws_of

router = APIRouter()


class NewKeyIn(BaseModel):
    name: str
    scopes: list[str]
    days: int = store.DEFAULT_EXPIRY_DAYS


class SettingsIn(BaseModel):
    api_enabled: bool | None = None
    mcp_enabled: bool | None = None
    mcp_tools: list[str] | None = None


def _urls(request):
    return {"api_url": public_url(request, "/api/v1"), "mcp_url": public_url(request, "/mcp"),
            "openapi_url": public_url(request, "/api/v1/openapi.json")}


def _create(user, body, kind, audit):
    settings = store.settings(ws_of(user))
    if not settings["api_enabled"] and not settings["mcp_enabled"]:
        raise HTTPException(403, "Your workspace's admins have turned the API and the MCP "
                                 "server off.")
    try:
        row, key = store.create(ws_of(user), user["username"], body.name, body.scopes,
                                user["role"], kind=kind, days=body.days)
    except store.Refused as e:
        raise HTTPException(400, str(e))
    audit.record("api_key", row["id"], {"name": row["name"], "kind": kind,
                                        "prefix": row["prefix"], "scopes": row["scopes"],
                                        "days": body.days}, user=user)
    return {**row, "key": key}


@router.get("/api/me/api-keys")
def my_keys(request: Request, user=Depends(current_user)):
    ws = ws_of(user)
    return {"keys": store.list_keys(ws, created_by=user["username"], kind="personal"),
            "scopes": store.scopes_for(user["role"]), "all_scopes": list(store.SCOPES),
            "expiry_days": list(store.EXPIRY_DAYS), "default_days": store.DEFAULT_EXPIRY_DAYS,
            "max_keys": store.MAX_PERSONAL, "settings": store.settings(ws), **_urls(request)}


@router.post("/api/me/api-keys")
def create_my_key(body: NewKeyIn, user=Depends(current_user),
                  audit=audited("api_key.create")):
    return _create(user, body, "personal", audit)


@router.delete("/api/me/api-keys/{key_id}")
def revoke_my_key(key_id: int, user=Depends(current_user), audit=audited("api_key.revoke")):
    row = store.revoke(ws_of(user), key_id, user["username"], created_by=user["username"])
    if not row:
        raise HTTPException(404, "You have no such active key.")
    audit.record("api_key", key_id, {"name": row["name"], "prefix": row["prefix"]}, user=user)
    return row


@router.get("/api/admin/api")
def api_settings(request: Request, user=Depends(require_admin)):
    ws = ws_of(user)
    return {"settings": store.settings(ws), "keys": store.list_keys(ws),
            "tools": list(store.MCP_TOOLS), "service_scopes": list(store.SERVICE_SCOPES),
            "expiry_days": list(store.EXPIRY_DAYS), "default_days": store.DEFAULT_EXPIRY_DAYS,
            "max_service_keys": store.MAX_SERVICE, **_urls(request)}


@router.put("/api/admin/api")
def set_api_settings(body: SettingsIn, user=Depends(require_admin),
                     audit=audited("api_settings.update")):
    try:
        settings = store.set_settings(ws_of(user), user["username"], **body.model_dump())
    except store.Refused as e:
        raise HTTPException(400, str(e))
    audit.record("api_settings", ws_of(user),
                 {k: v for k, v in body.model_dump().items() if v is not None}, user=user)
    return settings


@router.post("/api/admin/api/service-keys")
def create_service_key(body: NewKeyIn, user=Depends(require_admin),
                       audit=audited("api_key.create")):
    return _create(user, body, "service", audit)


@router.delete("/api/admin/api/keys/{key_id}")
def revoke_key(key_id: int, user=Depends(require_admin), audit=audited("api_key.revoke")):
    row = store.revoke(ws_of(user), key_id, user["username"])
    if not row:
        raise HTTPException(404, "This workspace has no such active key.")
    audit.record("api_key", key_id, {"name": row["name"], "prefix": row["prefix"],
                                     "owner": row["created_by"]}, user=user)
    return row
