"""
Integrations: connect, pick, sync and disconnect a workspace's connectors.

Every connector is listed in connectors/registry.py with the same shape
(connectors/base.py), so the endpoints below work for all of them:
  GET    /api/integrations                       every connector's card (+ uploads, soon)
  GET    /api/integrations/catalog               the registry, for anyone (landing page)
  POST   /api/integrations/{provider}/connect    oauth: {url} of the provider's consent screen
                                                 (body {url}: what it needs first, e.g.
                                                 Zendesk's subdomain)
  GET    /api/integrations/{provider}/callback   the provider redirects the browser back here
                                                 (a connector may share another's path:
                                                 Jira comes back on Confluence's)
  POST   /api/integrations/{provider}/setup      token / url: check the form, connect
  GET    /api/integrations/{provider}/containers what can be picked (?parent= for a tree)
  PUT    /api/integrations/{provider}/containers save the picks, then sync
  POST   /api/integrations/{provider}/sync       sync now (full / confirm a mass delete)
  GET    /api/integrations/{provider}/runs       sync history and skipped items
  PUT    /api/integrations/{provider}/access     who may ask about its files (the old levels)
  PUT    /api/integrations/{provider}/policy     its permission policy: source, workspace,
                                                 restricted (kb/services/permissions.py)
  POST   /api/integrations/{provider}/permissions/sync   sync permissions now
  DELETE /api/integrations/{provider}            disconnect + delete the synced copies

Errors reach the browser as plain-language messages for a code
(connectors/base.py ERRORS), never as an exception's text.
"""

import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import docs_store
import integrations_store
import sync_store
import workspaces
import kb.audit
import kb.state  # as kb.state: the callback's `state` parameter would hide it
from connectors import base, registry
from kb import quotas
from kb.audit import audited
from kb.deps import current_role, current_user, public_url, require_hr, ws_of
from kb.rate_limit import user_limit
from kb.services import agents, permissions, sync

router = APIRouter()
log = logging.getLogger("uvicorn.error")

OAUTH_STATE_SECONDS = 10 * 60
# Why connecting failed, for the redirect back to the Integrations screen.
CALLBACK_ERRORS = {"access_denied": "consent_denied", "consent_required": "consent_denied",
                   "permission_denied": "consent_denied"}     # (Microsoft's admin consent)


def _connector(provider):
    c = registry.get(provider)
    if not c:
        raise HTTPException(404, "Unknown integration.")
    return c


def _connected(ws, provider):
    c = _connector(provider)
    row = integrations_store.get(ws, provider)
    if not row:
        raise HTTPException(400, f"{c.LABEL} isn't connected.")
    return c, row


def _redirect_uri(request: Request, provider):
    """Where the provider sends the browser back. One OAuth app may serve several
    connectors through one callback URL (Atlassian's: Jira uses Confluence's)."""
    c = registry.get(provider)
    return public_url(request, f"/api/integrations/{(c and c.CALLBACK) or provider}/callback")


def _ctx(ws, provider, row):
    return base.SyncContext(workspace=ws, provider=provider, settings=row.get("settings") or {},
                            known={})


def _error(code):
    """{code, message} for a stored error code. A last_error from before codes
    existed holds an exception's text: it's shown as "unknown", never as is."""
    if not code:
        return None
    code = code if code in base.ERRORS else "unknown"
    return {"code": code, "message": base.message(code)}


def _run_summary(run):
    if not run:
        return None
    return {k: run[k] for k in ("id", "kind", "status", "started_at", "finished_at", "upserted",
                                "deleted", "skipped", "unchanged", "held_deletes",
                                "limit_reached")} | {"error": _error(run["error_code"])}


def _integration_info(ws, c, role):
    provider = c.PROVIDER
    row = integrations_store.get(ws, provider)
    if row:
        status = row["status"]
    else:
        status = "disconnected" if c.configured() else "not_configured"
    settings = (row and row.get("settings")) or {}
    state = sync_store.state(ws, provider) if row else {}
    synced, skipped = sync_store.counts(ws, provider) if row else (0, 0)
    last = sync_store.last_run(ws, provider) if row else None
    error = row and row.get("last_error")
    info = {**registry.manifest(c), "status": status,
            "account": row and row["account"], "connected_by": row and row["connected_by"],
            "last_sync_at": row and row["last_sync_at"],
            "doc_count": synced or (row and row["doc_count"]) or 0, "skipped_count": skipped,
            "error": _error(error if status == "error" else None),
            "access": integrations_store.access_of(row),
            # Who may ask about its files (kb/services/permissions.py), and the
            # policies it may have (source: only when it reads the source's permissions).
            "policy": integrations_store.policy_of(row),
            "policies": list(permissions.policies(provider)),
            "containers": settings.get(c.SETTINGS_KEY) or [],
            "held_deletes": state.get("pending_deletes") or 0,
            "last_run": _run_summary(last)}
    if provider == "website":
        info["limits"] = {"max_pages": settings.get("max_pages"),
                          "max_depth": settings.get("max_depth")}
    if provider == "outline":
        info["base_url"] = settings.get("base_url")
        # The server-wide wiki (.env), searched live, when this workspace has no synced one.
        info["live_search"] = agents.outline_on(ws, role)
    if provider in ("confluence_dc", "jira_dc"):
        info["base_url"] = settings.get("base_url")
    if provider == "zendesk":
        info["base_url"] = settings.get("subdomain")
    if provider == "s3":
        info["s3"] = c.card(ws, settings)
    info["sign_in"] = c.can_sign_in() and c.configured()
    info["options"] = c.options_of(settings)
    info["access_locked"] = settings.get("access_lock") == "restricted"
    if provider == "google_drive":
        info["folders"] = info["containers"]           # the old name
    return info


@router.get("/api/integrations")
def list_integrations(user=Depends(current_user), role=Depends(current_role)):
    ws = ws_of(user)
    uploads = {"id": "uploads", "name": "Uploaded files", "status": "connected",
               "availability": "live", "description": "Files you upload on the Documents page",
               "doc_count": len(docs_store.list_docs(workspaces.docs_root(ws)))}
    return {"workspace": ws, "integrations": [uploads] + [
        _integration_info(ws, c, role) for c in registry.CONNECTORS.values()] + [
        {**s, "availability": "soon", "status": "unavailable"} for s in registry.SOON]}


@router.get("/api/integrations/catalog")
def integrations_catalog():
    """Every connector, live or coming soon: names and kinds only, no workspace data."""
    return {"connectors": [{k: e.get(k) for k in ("id", "name", "description", "icon",
                                                  "availability")}
                           for e in registry.catalog()]}


class ConnectIn(BaseModel):
    url: str = Field(default="", max_length=2000)


@router.post("/api/integrations/{provider}/connect")
def integration_connect(provider: str, request: Request, body: ConnectIn | None = None,
                        user=Depends(require_hr), audit=audited("integration.connect_start")):
    c = _connector(provider)
    quotas.check(ws_of(user), "connector", user=user, provider=provider)   # the plan's limit
    if not c.can_sign_in():
        raise HTTPException(400, f"{c.LABEL} is connected with a form, not a sign-in.")
    if not c.configured():
        raise HTTPException(400, f"{c.LABEL} isn't set up on this server yet "
                                 f"({c.SETUP_ENV} missing).")
    oauth_states = kb.state.oauth_states
    now = time.time()
    for s in [k for k, v in oauth_states.items() if v["expires"] < now]:
        del oauth_states[s]
    state = secrets.token_urlsafe(24)
    redirect_uri = _redirect_uri(request, provider)
    try:
        url, step = c.connect_start(redirect_uri, state, (body or ConnectIn()).model_dump())
    except base.FormError as e:
        raise HTTPException(400, str(e))
    oauth_states[state] = {"workspace": ws_of(user), "user": user["username"],
                           "provider": provider, "expires": now + OAUTH_STATE_SECONDS,
                           "redirect_uri": redirect_uri,
                           **({"step": step} if step is not None else {})}
    audit.record("integration", provider, user=user)
    return {"url": url}


@router.get("/api/integrations/{provider}/callback")
def integration_callback(provider: str, request: Request, state: str = "", code: str = "",
                         error: str = ""):
    """The provider sends the browser back here. The one-time `state` ties it to the
    workspace and user who clicked Connect (a redirect carries no session header).
    A connector may need one more trip through the provider (SharePoint: sign-in,
    then the tenant admin's consent): it gets a new one-time state and goes on."""
    pending = kb.state.oauth_states.pop(state, None)
    c = registry.get((pending or {}).get("provider") or "")
    if (not pending or not c or pending["expires"] < time.time()
            or (c.CALLBACK or c.PROVIDER) != provider):
        return RedirectResponse("/#integrations?error=expired")
    provider = c.PROVIDER                  # the connector the state was made for
    step = pending.get("step")
    if not error:
        code = c.callback_code(dict(request.query_params), step)
    if error or not code:
        return RedirectResponse("/#integrations?error="
                                + CALLBACK_ERRORS.get(error, "cancelled" if not error else
                                                      "connect_failed"))
    ws = pending["workspace"]
    try:
        c.finish_connect(ws, code, pending["redirect_uri"], pending["user"],
                         **({"step": step} if step is not None else {}))
    except base.NextStep as nxt:
        again = secrets.token_urlsafe(24)
        kb.state.oauth_states[again] = {**pending, "step": nxt.data,
                                        "expires": time.time() + OAUTH_STATE_SECONDS}
        return RedirectResponse(nxt.url(again))
    except Exception as e:
        log.warning("Integration %s: couldn't finish connecting workspace %s (%s)", provider,
                    ws, type(e).__name__, exc_info=not isinstance(e, base.ConnectorError))
        reason = (getattr(e, "reason", None)
                  or ("no_refresh_token" if "refresh token" in str(e) else "connect_failed"))
        return RedirectResponse(f"/#integrations?error={reason}")
    row = integrations_store.get(ws, provider) or {}
    kb.audit.record(request, ws, "integration.connect", pending["user"], "integration", provider,
                    {"account": row.get("account")})
    if sync.ready_to_sync(ws, provider):
        sync.start_sync(ws, provider)
        return RedirectResponse(f"/#integrations?connected={provider}")
    # Nothing is synced until the admin picks what to sync.
    return RedirectResponse(f"/#integrations?connected={provider}&choose=1")


class SetupIn(BaseModel):
    url: str = Field(default="", max_length=2000)
    token: str = Field(default="", max_length=2000)
    email: str = Field(default="", max_length=320)
    cf_client_id: str = Field(default="", max_length=500)
    cf_client_secret: str = Field(default="", max_length=500)
    max_pages: int | None = Field(default=None, ge=1, le=100_000)
    max_depth: int | None = Field(default=None, ge=1, le=100)
    # Amazon S3 (connectors/s3.py): the bucket, and a role to assume or access keys.
    bucket: str = Field(default="", max_length=300)
    region: str = Field(default="", max_length=40)
    prefix: str = Field(default="", max_length=1024)
    endpoint: str = Field(default="", max_length=300)
    role_arn: str = Field(default="", max_length=2048)
    access_key_id: str = Field(default="", max_length=128)
    secret_access_key: str = Field(default="", max_length=256)


@router.post("/api/integrations/{provider}/setup")
def integration_setup(provider: str, body: SetupIn, user=Depends(require_hr),
                      audit=audited("integration.connect")):
    """Connect a token / address connector (Outline, a website) from its form."""
    c = _connector(provider)
    if c.AUTH == "oauth":
        raise HTTPException(400, f"{c.LABEL} is connected by signing in to it.")
    quotas.check(ws_of(user), "connector", user=user, provider=provider)   # the plan's limit
    user_limit(user, "sync")
    ws = ws_of(user)
    try:
        account = c.connect_form(ws, body.model_dump(), user["username"])
    except base.FormError as e:
        raise HTTPException(400, str(e))
    # The address is recorded; the token never is.
    audit.record("integration", provider, {"account": account,
                                           "url": (body.url or body.bucket).strip()[:300] or None},
                 user=user)
    if sync.ready_to_sync(ws, provider):
        sync.start_sync(ws, provider)
    return {"ok": True, "account": account,
            "choose": not sync.ready_to_sync(ws, provider)}


@router.get("/api/integrations/{provider}/containers")
def integration_containers(provider: str, parent: str = "", user=Depends(require_hr)):
    """What can be picked to sync: folders, pages, collections, ... (`parent`: inside one)."""
    ws = ws_of(user)
    c, row = _connected(ws, provider)
    try:
        items = c.list_containers(_ctx(ws, provider, row), parent or None)
    except base.ConnectorError as e:
        log.warning("Integration %s: couldn't list what can be picked in %s (%s)", provider,
                    ws, e)
        raise HTTPException(502, base.message(base.error_code(e)))
    except Exception:
        log.exception("Integration %s: couldn't list what can be picked in %s", provider, ws)
        raise HTTPException(502, base.message("unknown"))
    return {"items": items, "picked": (row.get("settings") or {}).get(c.SETTINGS_KEY) or []}


class ContainersIn(BaseModel):
    containers: list[dict[str, Any]] = Field(max_length=500)
    max_pages: int | None = Field(default=None, ge=1, le=100_000)
    max_depth: int | None = Field(default=None, ge=1, le=100)
    options: dict[str, Any] | None = None     # the connector's own (check_options)


@router.put("/api/integrations/{provider}/containers")
def integration_set_containers(provider: str, body: ContainersIn, user=Depends(require_hr),
                               audit=audited("integration.folders")):
    """Save what to sync, then sync: everything again if the picks changed, and
    what's no longer picked is removed (the admin's choice, so the mass-delete
    guard doesn't hold it back)."""
    ws = ws_of(user)
    c, row = _connected(ws, provider)
    try:
        picked = c.check_containers(body.containers)
        options = c.check_options(body.options, row.get("settings") or {}) \
            if body.options is not None else {}
    except base.FormError as e:
        raise HTTPException(400, str(e))
    settings = dict(row.get("settings") or {})
    changed = settings.get(c.SETTINGS_KEY) != picked
    settings[c.SETTINGS_KEY] = picked
    for key, value in options.items():
        changed |= settings.get(key) != value
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
    for key in ("max_pages", "max_depth"):
        if getattr(body, key) is not None and provider == "website":
            changed |= settings.get(key) != getattr(body, key)
            settings[key] = getattr(body, key)
    integrations_store.update(ws, provider, settings=settings)
    audit.record("integration", provider,
                 {"containers": [p["id"] for p in picked][:50],
                  **{k: settings.get(k) for k in ("max_pages", "max_depth") if k in settings},
                  **({"options": {k: options[k] for k in sorted(options)}} if options else {})},
                 user=user)
    if integrations_store.policy_of(row) != integrations_store.policy_of({"settings": settings}):
        from kb.services import indexing
        indexing.schedule_retag(ws, provider)  # e.g. locked to HR and admins
    if changed:
        sync.start_sync(ws, provider, full=True, confirm_deletes=True)
    return {"containers": picked}


class SyncIn(BaseModel):
    full: bool = False
    confirm_deletes: bool = False


@router.post("/api/integrations/{provider}/sync")
def integration_sync(provider: str, body: SyncIn | None = None, user=Depends(require_hr),
                     audit=audited("integration.sync")):
    """Sync now. full: list everything again. confirm_deletes: apply deletes the
    mass-delete guard held back."""
    _connector(provider)
    user_limit(user, "sync")
    ws = ws_of(user)
    _connected(ws, provider)
    body = body or SyncIn()
    sync.start_sync(ws, provider, full=body.full, confirm_deletes=body.confirm_deletes)
    audit.record("integration", provider, {k: True for k in ("full", "confirm_deletes")
                                           if getattr(body, k)} or None, user=user)
    return {"ok": True}


@router.get("/api/integrations/{provider}/runs")
def integration_runs(provider: str, user=Depends(require_hr)):
    """The connection's sync history (newest first) and what it skipped, and why."""
    ws = ws_of(user)
    _connected(ws, provider)
    return {"runs": [_run_summary(r) for r in sync_store.runs(ws, provider)],
            "skipped": sync_store.skipped(ws, provider)}


@router.delete("/api/integrations/{provider}")
def integration_disconnect(provider: str, user=Depends(require_hr),
                           audit=audited("integration.disconnect")):
    _connector(provider)
    sync.disconnect(ws_of(user), provider)
    audit.record("integration", provider, user=user)
    return {"ok": True}


class AccessIn(BaseModel):
    access: str = Field(pattern=r"^(restricted|everyone)$")


@router.put("/api/integrations/{provider}/access")
def integration_access(provider: str, body: AccessIn, user=Depends(require_hr),
                       audit=audited("integration.access")):
    """Who may ask about this connection's files: HR and admins only
    ("restricted", the default) or everyone in the workspace. Their chunks are
    re-tagged with the new level (nothing is embedded again)."""
    from kb.services import indexing
    ws = ws_of(user)
    c, row = _connected(ws, provider)
    if body.access == "everyone" and (row.get("settings") or {}).get("access_lock"):
        raise HTTPException(400, f"{c.LABEL} syncs tickets, which hold customers' personal "
                                 "data, so only HR and admins can ask about it. Turn tickets "
                                 "off to change this.")
    policy = "workspace" if body.access == "everyone" else "restricted"
    if integrations_store.policy_of(row) != policy:
        # The level IS the policy now: no separate policy key to disagree with it.
        settings = dict(row.get("settings") or {}, access=body.access)
        settings.pop("policy", None)
        integrations_store.update(ws, provider, settings=settings)
        audit.record("integration", provider, {"access": body.access}, user=user)
        indexing.schedule_retag(ws, provider)
    return {"access": body.access}


class PolicyIn(BaseModel):
    policy: str = Field(pattern=r"^(source|workspace|restricted)$")


@router.put("/api/integrations/{provider}/policy")
def integration_policy(provider: str, body: PolicyIn, user=Depends(require_hr),
                       audit=audited("integration.policy")):
    """The connection's PERMISSION POLICY (kb/services/permissions.py): source (who
    may read each file at the source; only for connectors that read permissions),
    workspace (everyone) or restricted (HR and admins, the default). Its chunks
    are re-tagged (nothing is embedded again)."""
    from kb.services import indexing
    ws = ws_of(user)
    c, row = _connected(ws, provider)
    if body.policy not in permissions.policies(provider):
        raise HTTPException(400, f"{c.LABEL} doesn't read who may see each item at the "
                                 "source, so it can't follow the source's permissions.")
    if body.policy != "restricted" and (row.get("settings") or {}).get("access_lock"):
        raise HTTPException(400, f"{c.LABEL} syncs tickets, which hold customers' personal "
                                 "data, so only HR and admins can ask about it. Turn tickets "
                                 "off to change this.")
    before = integrations_store.policy_of(row)
    if before != body.policy:
        integrations_store.update(ws, provider, settings=dict(
            row.get("settings") or {}, policy=body.policy,
            access="everyone" if body.policy == "workspace" else "restricted"))
        audit.record("integration", provider, {"policy": [before, body.policy]}, user=user)
        indexing.schedule_retag(ws, provider)
    return {"policy": body.policy}


@router.post("/api/integrations/{provider}/permissions/sync")
def integration_permissions_sync(provider: str, user=Depends(require_hr),
                                 audit=audited("integration.permissions_sync")):
    """SYNC PERMISSIONS NOW: list everything at the source again (a full sync, so
    every item's permissions are read afresh), and work out every file's ACL again
    from them and this workspace's members and groups now (a re-tag, no embedding).
    Otherwise this happens after every scheduled sync."""
    from kb.services import indexing
    _connector(provider)
    user_limit(user, "sync")
    ws = ws_of(user)
    _connected(ws, provider)
    sync.start_sync(ws, provider, full=True)
    indexing.schedule_retag(ws, provider)
    audit.record("integration", provider, user=user)
    return {"ok": True}

