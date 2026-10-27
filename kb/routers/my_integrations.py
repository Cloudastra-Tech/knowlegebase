"""
My connections: each member's OWN Gmail and Google Calendar (personal connectors,
connectors/registry.py PERSONAL). Anyone signed in may connect their own; no
one, an admin included, can reach anyone else's: every endpoint works on the
asker's connection only, keyed registry.personal_key(provider, their username).
What it syncs is for them alone (kb/services/permissions.py personal_acl).

  GET    /api/me/integrations                       the personal connectors, and mine
  POST   /api/me/integrations/{provider}/connect    {url} of Google's consent screen (the
                                                    callback is the shared
                                                    /api/integrations/{provider}/callback)
  GET    /api/me/integrations/{provider}/containers my labels / calendars to pick
  PUT    /api/me/integrations/{provider}/containers save my picks, then sync
  POST   /api/me/integrations/{provider}/sync       sync mine now
  DELETE /api/me/integrations/{provider}            disconnect mine + delete its copies
"""

import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import integrations_store
import kb.state
import sync_store
from connectors import base, registry
from kb.audit import audited
from kb.deps import current_user, public_url, ws_of
from kb.rate_limit import user_limit
from kb.routers.integrations import OAUTH_STATE_SECONDS, _error, _run_summary
from kb.services import sync

router = APIRouter()
log = logging.getLogger("uvicorn.error")


def _personal(provider):
    c = registry.personal(provider)
    if not c:
        raise HTTPException(404, "Unknown connection.")
    return c


def _mine(user, provider):
    c = _personal(provider)
    ws, key = ws_of(user), registry.personal_key(provider, user["username"])
    return c, ws, key, integrations_store.get(ws, key)


def _info(user, c):
    _, ws, key, row = _mine(user, c.PROVIDER)
    settings = (row and row.get("settings")) or {}
    status = row["status"] if row else ("disconnected" if c.configured() else "not_configured")
    synced, skipped = sync_store.counts(ws, key) if row else (0, 0)
    return {**registry.manifest(c), "status": status, "account": row and row["account"],
            "last_sync_at": row and row["last_sync_at"], "doc_count": synced,
            "skipped_count": skipped,
            "error": _error(row.get("last_error") if row and status == "error" else None),
            "containers": settings.get(c.SETTINGS_KEY) or [],
            "options": c.options_of(settings),
            "last_run": _run_summary(sync_store.last_run(ws, key)) if row else None,
            "sign_in": c.configured()}


@router.get("/api/me/integrations")
def list_my_integrations(user=Depends(current_user)):
    return {"integrations": [_info(user, c) for c in registry.PERSONAL]}


@router.post("/api/me/integrations/{provider}/connect")
def my_integration_connect(provider: str, request: Request, user=Depends(current_user),
                           audit=audited("integration.connect_start")):
    c = _personal(provider)
    if not c.configured():
        raise HTTPException(400, f"{c.LABEL} isn't turned on for this server.")
    user_limit(user, "sync")
    states = kb.state.oauth_states
    now = time.time()
    for s in [k for k, v in states.items() if v["expires"] < now]:
        del states[s]
    state = secrets.token_urlsafe(24)
    redirect_uri = public_url(request, f"/api/integrations/{c.CALLBACK or provider}/callback")
    url, step = c.connect_start(redirect_uri, state, {})
    # The state carries the member's own key: the callback saves it there and nowhere else.
    states[state] = {"workspace": ws_of(user), "user": user["username"],
                     "provider": registry.personal_key(provider, user["username"]),
                     "expires": now + OAUTH_STATE_SECONDS, "redirect_uri": redirect_uri,
                     **({"step": step} if step is not None else {})}
    audit.record("integration", provider, {"personal": True}, user=user)
    return {"url": url}


def _connected(user, provider):
    c, ws, key, row = _mine(user, provider)
    if not row:
        raise HTTPException(400, f"Your {c.LABEL} isn't connected.")
    return c, ws, key, row


@router.get("/api/me/integrations/{provider}/containers")
def my_integration_containers(provider: str, user=Depends(current_user)):
    c, ws, key, row = _connected(user, provider)
    ctx = base.SyncContext(workspace=ws, provider=key, settings=row.get("settings") or {},
                           known={})
    try:
        items = c.list_containers(ctx)
    except base.ConnectorError as e:
        log.warning("My %s: couldn't list what can be picked (%s)", provider, base.error_code(e))
        raise HTTPException(502, base.message(base.error_code(e)))
    except Exception:
        log.exception("My %s: couldn't list what can be picked", provider)
        raise HTTPException(502, base.message("unknown"))
    return {"items": items, "picked": (row.get("settings") or {}).get(c.SETTINGS_KEY) or []}


class ContainersIn(BaseModel):
    containers: list[dict[str, Any]] = Field(max_length=500)
    options: dict[str, Any] | None = None


@router.put("/api/me/integrations/{provider}/containers")
def my_integration_set_containers(provider: str, body: ContainersIn, user=Depends(current_user),
                                  audit=audited("integration.folders")):
    """Save which labels / calendars to sync, then sync them (everything again;
    what's no longer picked is removed: the member's own choice)."""
    c, ws, key, row = _connected(user, provider)
    try:
        picked = c.check_containers(body.containers)
        options = c.check_options(body.options, row.get("settings") or {}) \
            if body.options is not None else {}
    except base.FormError as e:
        raise HTTPException(400, str(e))
    settings = dict(row.get("settings") or {})
    changed = settings.get(c.SETTINGS_KEY) != picked
    settings[c.SETTINGS_KEY] = picked
    for k, value in options.items():
        changed |= settings.get(k) != value
        settings[k] = value
    settings["owner"] = user["username"]            # (never anyone else's)
    integrations_store.update(ws, key, settings=settings)
    # Counts only: a member's label and calendar names stay out of the audit log.
    audit.record("integration", provider, {"personal": True, "picked": len(picked)}, user=user)
    if changed:
        sync.start_sync(ws, key, full=True, confirm_deletes=True)
    return {"containers": picked}


class SyncIn(BaseModel):
    full: bool = False
    confirm_deletes: bool = False


@router.post("/api/me/integrations/{provider}/sync")
def my_integration_sync(provider: str, body: SyncIn | None = None, user=Depends(current_user),
                        audit=audited("integration.sync")):
    _, ws, key, _row = _connected(user, provider)
    user_limit(user, "sync")
    body = body or SyncIn()
    sync.start_sync(ws, key, full=body.full, confirm_deletes=body.confirm_deletes)
    audit.record("integration", provider, {"personal": True}, user=user)
    return {"ok": True}


@router.delete("/api/me/integrations/{provider}")
def my_integration_disconnect(provider: str, user=Depends(current_user),
                              audit=audited("integration.disconnect")):
    _, ws, key, row = _mine(user, provider)
    if row:
        sync.disconnect(ws, key)
    audit.record("integration", provider, {"personal": True}, user=user)
    return {"ok": True}
