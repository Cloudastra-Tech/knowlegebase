"""Re-indexing: start one by hand, and its progress (the work: services/indexing.py).
Admins also see the workspace's recent background jobs (GET /api/admin/jobs)."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from kb import jobs
from kb.audit import audited
from kb.deps import current_role, current_user, require_admin, require_hr, ws_of
from kb.rate_limit import user_limit
from kb.services import indexing

router = APIRouter()

# A job's payload keys an admin may see: what kind of run it was, never export
# ids, message bodies or anything secret.
JOB_DETAILS = ("full", "provider")
ERROR_CHARS = 500


def _status(user, role):
    """The index status. The raw error (an exception's text) is only for an admin
    viewing the app as HR / Admin; everyone gets its `reason` code."""
    status = indexing.index_status(ws_of(user))
    if not (user["role"] == "admin" and role == "hr"):
        status["error"] = None
    return status


@router.post("/api/reindex")
def reindex(full: bool = False, user=Depends(require_hr), role=Depends(current_role),
            audit=audited("index.reindex")):
    """Manual re-index (changes already re-index automatically): every document
    is checked and only what changed is embedded again. full=true rebuilds the
    whole index instead, beside the current one, which keeps answering until
    the new one is done: admins only (it embeds everything again, which costs)."""
    if full and user["role"] != "admin":
        raise HTTPException(403, "Only admins can rebuild the whole index.")
    user_limit(user, "reindex")
    indexing.schedule_index(ws_of(user), full=full)
    audit.record("index", ws_of(user), {"full": full}, user=user)
    return _status(user, role)


@router.get("/api/index/status")
def index_status(user=Depends(current_user), role=Depends(current_role)):
    return _status(user, role)


def _job(row):
    payload = json.loads(row.get("payload") or "{}")
    progress = json.loads(row.get("progress") or "null") or {}
    error = row.get("error")
    return {"id": row["id"], "kind": row["kind"], "status": row["status"],
            "attempts": row["attempts"], "max_attempts": row["max_attempts"],
            "details": {k: payload[k] for k in JOB_DETAILS if k in payload},
            "progress": progress.get("message") if row["status"] == "running" else None,
            "error": error[:ERROR_CHARS] if error else None,
            "reason": indexing.reason_of(error) if row["kind"] in indexing.KINDS else None,
            "created_at": row["created_at"], "started_at": row["started_at"],
            "finished_at": row["finished_at"]}


@router.get("/api/admin/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=200), user=Depends(require_admin)):
    """The workspace's background jobs (indexing, syncs, digests, drafts, ...),
    newest first, with their status and last error. Read only."""
    return {"jobs": [_job(r) for r in jobs.recent(ws_of(user), limit)]}
