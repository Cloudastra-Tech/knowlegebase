"""
Integrations: connect, sync and disconnect a workspace's connectors (connectors/).

Every connector module has the same shape (services/sync.py), so the endpoints
below work for all of them:
  POST   /api/integrations/{provider}/connect   -> {url} of the provider's consent screen
  GET    /api/integrations/{provider}/callback  the provider redirects the browser back here
  POST   /api/integrations/{provider}/sync      sync now
  DELETE /api/integrations/{provider}           disconnect + delete the synced copies
"""

import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import docs_store
import integrations_store
import workspaces
import kb.state  # as kb.state: the callback's `state` parameter would hide it
from connectors import google_drive
from kb.deps import current_role, current_user, public_url, require_hr, ws_of
from kb.rate_limit import user_limit
from kb.services import agents, indexing, sync
from kb.services.sync import LABELS, SETUP_ENV

router = APIRouter()
log = logging.getLogger("uvicorn.error")

OAUTH_STATE_SECONDS = 10 * 60


def _connector(provider):
    c = sync.CONNECTORS.get(provider)
    if not c:
        raise HTTPException(404, "Unknown integration.")
    return c


def _redirect_uri(request: Request, provider):
    return public_url(request, f"/api/integrations/{provider}/callback")


def _integration_info(ws, provider):
    row = integrations_store.get(ws, provider)
    if row:
        status = row["status"]
    else:
        status = "disconnected" if sync.CONNECTORS[provider].configured() else "not_configured"
    info = {"id": provider, "name": LABELS[provider], "status": status,
            "account": row and row["account"], "connected_by": row and row["connected_by"],
            "last_sync_at": row and row["last_sync_at"],
            "doc_count": (row and row["doc_count"]) or 0,
            "last_error": row and row["last_error"],
            "access": integrations_store.access_of(row)}
    if provider == google_drive.PROVIDER:
        info["folders"] = ((row and row.get("settings")) or {}).get("folders", [])
    return info


@router.get("/api/integrations")
def list_integrations(user=Depends(current_user), role=Depends(current_role)):
    ws = ws_of(user)
    return {"workspace": ws, "integrations": [
        {"id": "uploads", "name": "Uploaded files", "status": "connected",
         "doc_count": len(docs_store.list_docs(workspaces.docs_root(ws)))},
        _integration_info(ws, "google_drive"),
        _integration_info(ws, "notion"),
        {"id": "outline", "name": "Outline wiki",
         "status": "connected" if agents.outline_on(ws, role) else "unavailable"},
    ]}


@router.post("/api/integrations/{provider}/connect")
def integration_connect(provider: str, request: Request, user=Depends(require_hr)):
    c = _connector(provider)
    if not c.configured():
        raise HTTPException(400, f"{LABELS[provider]} isn't set up on this server yet "
                                 f"({SETUP_ENV[provider]} missing).")
    oauth_states = kb.state.oauth_states
    now = time.time()
    for s in [k for k, v in oauth_states.items() if v["expires"] < now]:
        del oauth_states[s]
    state = secrets.token_urlsafe(24)
    redirect_uri = _redirect_uri(request, provider)
    oauth_states[state] = {"workspace": ws_of(user), "user": user["username"],
                           "provider": provider, "expires": now + OAUTH_STATE_SECONDS,
                           "redirect_uri": redirect_uri}
    return {"url": c.auth_url(redirect_uri, state)}


@router.get("/api/integrations/{provider}/callback")
def integration_callback(provider: str, state: str = "", code: str = "", error: str = ""):
    """The provider sends the browser back here. The one-time `state` ties it to the
    workspace and user who clicked Connect (a redirect carries no session header)."""
    c = _connector(provider)
    pending = kb.state.oauth_states.pop(state, None)
    if not pending or pending["expires"] < time.time() or pending.get("provider") != provider:
        return RedirectResponse("/#integrations?error=expired")
    if error or not code:
        return RedirectResponse("/#integrations?error=" + (error or "cancelled"))
    ws = pending["workspace"]
    try:
        c.finish_connect(ws, code, pending["redirect_uri"], pending["user"])
    except Exception:
        log.exception("Integration %s: couldn't finish connecting workspace %s", provider, ws)
        return RedirectResponse("/#integrations?error=connect_failed")
    if sync.ready_to_sync(ws, provider):
        sync.start_sync(ws, provider)
        return RedirectResponse(f"/#integrations?connected={provider}")
    # Drive, new connection: nothing is synced until the admin picks folders.
    return RedirectResponse(f"/#integrations?connected={provider}&choose=1")


@router.post("/api/integrations/{provider}/sync")
def integration_sync(provider: str, user=Depends(require_hr)):
    _connector(provider)
    user_limit(user, "sync")
    ws = ws_of(user)
    if not integrations_store.get(ws, provider):
        raise HTTPException(400, f"{LABELS[provider]} isn't connected.")
    sync.start_sync(ws, provider)
    return {"ok": True}


@router.delete("/api/integrations/{provider}")
def integration_disconnect(provider: str, user=Depends(require_hr)):
    c = _connector(provider)
    ws = ws_of(user)
    c.disconnect(ws)
    indexing.schedule_index(ws)            # drop its files from the search index
    return {"ok": True}


class AccessIn(BaseModel):
    access: str = Field(pattern=r"^(restricted|everyone)$")


@router.put("/api/integrations/{provider}/access")
def integration_access(provider: str, body: AccessIn, user=Depends(require_hr)):
    """Who may ask about this connection's files: HR and admins only
    ("restricted", the default) or everyone in the workspace. The files are
    re-indexed so their chunks carry the new level."""
    _connector(provider)
    ws = ws_of(user)
    row = integrations_store.get(ws, provider)
    if not row:
        raise HTTPException(400, f"{LABELS[provider]} isn't connected.")
    if integrations_store.access_of(row) != body.access:
        integrations_store.update(ws, provider,
                                  settings=dict(row.get("settings") or {}, access=body.access))
        log.warning("%s set who can ask about %s files in %s to %s", user["username"],
                    LABELS[provider], ws, body.access)
        indexing.schedule_index(ws)
    return {"access": body.access}


class FoldersIn(BaseModel):
    folders: list[dict]


@router.get("/api/integrations/google_drive/folders")
def drive_folders(parent: str = "", user=Depends(require_hr)):
    """Browse what can be picked: top level (Shared Drives + My Drive root) or a
    folder — its sub-folders and its files."""
    ws = ws_of(user)
    if not integrations_store.get(ws, google_drive.PROVIDER):
        raise HTTPException(400, "Google Drive isn't connected.")
    try:
        return google_drive.list_folders(ws, parent or None)
    except Exception as e:
        log.exception("Google Drive: couldn't list folders for workspace %s", ws)
        raise HTTPException(502, f"Couldn't list Google Drive folders: {e}")


@router.put("/api/integrations/google_drive/folders")
def drive_set_folders(body: FoldersIn, user=Depends(require_hr)):
    """Save which folders / Shared Drives / single files to sync, then sync
    (anything no longer picked is dropped from the index)."""
    ws = ws_of(user)
    drive = integrations_store.get(ws, google_drive.PROVIDER)
    if not drive:
        raise HTTPException(400, "Google Drive isn't connected.")
    kinds = ("drive", "folder", "file")
    folders = [{"id": str(f["id"]), "name": str(f.get("name", ""))[:200],
                "kind": f.get("kind") if f.get("kind") in kinds else "folder",
                **({"mimeType": str(f["mimeType"])[:100]} if f.get("mimeType") else {})}
               for f in body.folders if f.get("id")][:200]
    settings = dict(drive.get("settings") or {}, folders=folders)
    integrations_store.update(ws, google_drive.PROVIDER, settings=settings)
    sync.start_sync(ws, google_drive.PROVIDER)
    return {"folders": folders}
