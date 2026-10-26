"""Re-indexing: start one by hand, and its progress (the work: services/indexing.py)."""

from fastapi import APIRouter, Depends

from kb.audit import audited
from kb.deps import current_user, require_hr, ws_of
from kb.rate_limit import user_limit
from kb.services import indexing

router = APIRouter()


@router.post("/api/reindex")
def reindex(full: bool = False, user=Depends(require_hr), audit=audited("index.reindex")):
    """Manual re-index (changes already re-index automatically): every document
    is checked and only what changed is embedded again. full=true rebuilds the
    whole index instead, beside the current one, which keeps answering until
    the new one is done."""
    user_limit(user, "reindex")
    indexing.schedule_index(ws_of(user), full=full)
    audit.record("index", ws_of(user), {"full": full}, user=user)
    return index_status(user)


@router.get("/api/index/status")
def index_status(user=Depends(current_user)):
    return indexing.index_status(ws_of(user))
