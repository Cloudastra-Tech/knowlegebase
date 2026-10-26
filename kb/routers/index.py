"""Re-indexing: start one by hand, and its progress (the work: services/indexing.py)."""

from fastapi import APIRouter, Depends

from kb import state
from kb.deps import current_user, require_hr, ws_of
from kb.rate_limit import user_limit
from kb.services import indexing

router = APIRouter()


@router.post("/api/reindex")
def reindex(user=Depends(require_hr)):
    """Manual re-index (changes already re-index automatically)."""
    user_limit(user, "reindex")
    indexing.schedule_index(ws_of(user))
    return index_status(user)


@router.get("/api/index/status")
def index_status(user=Depends(current_user)):
    with state.index_lock:
        status = state.indexes.get(ws_of(user)) or state.new_index_state()
        return {k: status[k] for k in ("status", "step", "total", "message", "pending",
                                       "summary", "error", "finished_at")}
