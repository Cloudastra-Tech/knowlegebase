"""The workspace itself (admins; kb/services/lifecycle.py, lifecycle_store.py):
export it, delete it after a grace period (or cancel that), let Cloudastra
support in for a while, and how long the audit log is kept. Deleting, exporting,
granting support access and audit retention are for the workspace's own admins,
never support opened as one (deps.require_tenant_admin)."""

import time

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import lifecycle_store as store
import members_store
import workspaces
from kb.audit import audited
from kb.deps import require_admin, require_tenant_admin, ws_of
from kb.services import lifecycle

router = APIRouter()


def _export_out(e):
    return {k: e[k] for k in ("id", "status", "requested_by", "created_at", "finished_at",
                              "expires_at", "size", "counts", "error")}


def _view(ws, user):
    st = store.state(ws)
    return {"slug": ws, "name": members_store.workspace_name(ws), "status": st["status"],
            "deleting_at": st["deleting_at"], "delete_requested_at": st["delete_requested_at"],
            "delete_requested_by": st["delete_requested_by"],
            "support_until": st["support_until"], "support_granted_by": st["support_granted_by"],
            "support_hours": list(store.SUPPORT_HOURS), "grace_days": store.grace_days(),
            "audit_days": st["audit_days"], "audit_day_choices": list(store.AUDIT_DAY_CHOICES),
            "export_hours": store.EXPORT_HOURS,
            "exports": [_export_out(e) for e in store.exports(ws)],
            "can_delete": ws != workspaces.DEFAULT, "support_session": bool(user.get("impersonator"))}


@router.get("/api/admin/workspace")
def workspace_settings(user=Depends(require_admin)):
    return _view(ws_of(user), user)


@router.post("/api/admin/workspace/export")
def export_workspace(user=Depends(require_tenant_admin), audit=audited("workspace.export")):
    """Start an export (a job); poll GET /api/admin/workspace for it."""
    ws = ws_of(user)
    if any(e["status"] in ("queued", "running") for e in store.exports(ws)):
        raise HTTPException(409, "An export is already being made.")
    export = lifecycle.start_export(ws, user["username"])
    audit.record("export", export["id"], user=user)
    return _export_out(export)


@router.get("/api/admin/workspace/exports/{eid}/download")
def download_export(eid: str, user=Depends(require_tenant_admin),
                    audit=audited("workspace.export_download")):
    ws = ws_of(user)
    export = store.get_export(ws, eid)
    if not export or export["status"] != "done":
        raise HTTPException(404, "That export isn't there (they're kept "
                                 f"{store.EXPORT_HOURS} hours).")
    audit.record("export", eid, {"size": export["size"]}, user=user)
    name = f"workspace-{ws}-{time.strftime('%Y%m%d', time.gmtime(export['created_at']))}.zip"
    return FileResponse(lifecycle.export_path(export), media_type="application/zip",
                        filename=name)


class DeleteIn(BaseModel):
    confirm: str = Field(max_length=200)        # the workspace's name, typed


@router.delete("/api/admin/workspace")
def delete_workspace(body: DeleteIn = Body(...), user=Depends(require_tenant_admin),
                     audit=audited("workspace.delete")):
    ws = ws_of(user)
    if ws == workspaces.DEFAULT:
        raise HTTPException(400, "The default workspace can't be deleted.")
    if store.status(ws) != store.ACTIVE:
        raise HTTPException(409, "This workspace is already suspended or being deleted.")
    if body.confirm.strip() != members_store.workspace_name(ws):
        raise HTTPException(400, "Type the workspace's name exactly to delete it.")
    when = lifecycle.schedule_deletion(ws, user["username"])
    audit.record("workspace", ws, {"purge_at": when}, user=user)
    return _view(ws, user)


@router.post("/api/admin/workspace/cancel-deletion")
def cancel_workspace_deletion(user=Depends(require_admin),
                              audit=audited("workspace.delete_cancel")):
    ws = ws_of(user)
    if not lifecycle.cancel_deletion(ws, user["username"]):
        raise HTTPException(409, "This workspace isn't being deleted.")
    audit.record("workspace", ws, user=user)
    return _view(ws, user)


class SupportIn(BaseModel):
    hours: int


@router.put("/api/admin/workspace/support-access")
def grant_support(body: SupportIn, user=Depends(require_tenant_admin),
                  audit=audited("support.grant")):
    """Let Cloudastra support open the workspace as an admin until then."""
    if body.hours not in store.SUPPORT_HOURS:
        raise HTTPException(400, f"Choose {', '.join(map(str, store.SUPPORT_HOURS))} hours.")
    ws = ws_of(user)
    until = store.grant_support(ws, body.hours, user["username"])
    audit.record("workspace", ws, {"hours": body.hours, "until": until}, user=user)
    return _view(ws, user)


@router.delete("/api/admin/workspace/support-access")
def revoke_support(user=Depends(require_admin), audit=audited("support.revoke")):
    """Support's access ends now (sessions it opened stop at their next request)."""
    ws = ws_of(user)
    store.revoke_support(ws)
    audit.record("workspace", ws, user=user)
    return _view(ws, user)


class AuditRetentionIn(BaseModel):
    days: int | None                            # None: kept for good


@router.put("/api/admin/workspace/audit-retention")
def set_audit_retention(body: AuditRetentionIn, user=Depends(require_tenant_admin),
                        audit=audited("audit.retention")):
    ws = ws_of(user)
    before = store.state(ws)["audit_days"]
    try:
        store.set_audit_days(ws, body.days)
    except ValueError:
        raise HTTPException(400, "Keep the audit log for good, or for at least a year: "
                                 f"{', '.join(map(str, store.AUDIT_DAY_CHOICES))} days.")
    audit.record("workspace", ws, {"days": body.days, "was": before}, user=user)
    return _view(ws, user)
