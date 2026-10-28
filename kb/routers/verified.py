"""Verified answers (kb/services/verified.py): the admin screen's list, editor and
review queue, and the drafts "Save as verified answer" (a chat answer) and "Add a
verified answer" (a content gap) start from. HR and admins, viewing the app as HR.

Every change is audited by what kind of change it was and the answer's id, never
its text: answers are the workspace's content."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

import verified_store as store
from kb.audit import audited
from kb.deps import current_viewer, require_hr, ws_of
from kb.services import verified

router = APIRouter()

ANSWER_ID = r"^[0-9a-f]{32}$"


class AnswerIn(BaseModel):
    title: str | None = None
    variants: list[str] | None = None
    answer_md: str | None = None
    sources: list[str] | None = None
    access: str | None = None
    owner: str | None = None
    review_days: int | None = None
    from_message: str | None = None
    from_gap: int | None = None


def _fields(body):
    return {k: v for k, v in body.model_dump().items() if v is not None}


def _details(row, before=None):
    out = {"access": row["access"], "review_days": row["review_days"],
           "questions": len(row["variants"]), "sources": len(row["sources"]),
           "owner": row["owner"]}
    if before and before["access"] != row["access"]:
        out["access_was"] = before["access"]
    return out


@router.get("/api/admin/verified-answers")
def list_answers(user=Depends(require_hr)):
    """Every verified answer, with its live status (current, due, expired), who may
    own one, and the choices the editor offers."""
    ws = ws_of(user)
    names = {}
    return {"answers": [verified.view(ws, row, names=names) for row in store.list_for(ws)],
            "owners": verified.owners(ws), "review_days": list(verified.REVIEW_DAYS),
            "default_review_days": verified.DEFAULT_REVIEW_DAYS,
            "notice_days": verified.NOTICE_DAYS}


@router.get("/api/admin/verified-answers/draft")
def draft_answer(message_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"),
                 gap_id: int | None = None, user=Depends(require_hr),
                 viewer=Depends(current_viewer)):
    """A new answer's fields from one of your chat answers (one you may still
    read), or from a content gap."""
    ws = ws_of(user)
    if message_id:
        found = verified.draft_from_message(ws, user, viewer, message_id)
    elif gap_id is not None:
        found = verified.draft_from_gap(ws, viewer, gap_id)
    else:
        raise HTTPException(400, "Say which answer or content gap to start from.")
    if not found:
        raise HTTPException(404, "That answer or content gap isn't there any more.")
    return found


@router.post("/api/admin/verified-answers")
def create_answer(body: AnswerIn, user=Depends(require_hr),
                  audit=audited("verified_answer.create")):
    ws = ws_of(user)
    try:
        row = verified.create(ws, _fields(body), user)
    except verified.Invalid as e:
        raise HTTPException(400, str(e))
    audit.record("verified_answer", row["id"], {**_details(row),
                                                "from": "chat" if row["from_message"] else
                                                "gap" if row["from_gap"] else "new"}, user=user)
    return verified.view(ws, row)


@router.patch("/api/admin/verified-answers/{answer_id}")
def update_answer(body: AnswerIn, answer_id: str = Path(pattern=ANSWER_ID),
                  user=Depends(require_hr), audit=audited("verified_answer.update")):
    ws = ws_of(user)
    before = store.get(ws, answer_id)
    try:
        row = verified.update(ws, answer_id, _fields(body), user)
    except verified.Invalid as e:
        raise HTTPException(400, str(e))
    if not row:
        raise HTTPException(404, "That verified answer isn't there any more.")
    audit.record("verified_answer", answer_id, _details(row, before), user=user)
    return verified.view(ws, row)


@router.post("/api/admin/verified-answers/{answer_id}/review")
def review_answer(answer_id: str = Path(pattern=ANSWER_ID), user=Depends(require_hr),
                  audit=audited("verified_answer.review")):
    """It's still right: verified again now, by you, so its review date starts again."""
    ws = ws_of(user)
    row = verified.mark_reviewed(ws, answer_id, user)
    if not row:
        raise HTTPException(404, "That verified answer isn't there any more.")
    audit.record("verified_answer", answer_id, {"review_days": row["review_days"]}, user=user)
    return verified.view(ws, row)


@router.delete("/api/admin/verified-answers/{answer_id}")
def delete_answer(answer_id: str = Path(pattern=ANSWER_ID), user=Depends(require_hr),
                  audit=audited("verified_answer.delete")):
    ws = ws_of(user)
    row = store.delete(ws, answer_id)
    if not row:
        raise HTTPException(404, "That verified answer isn't there any more.")
    audit.record("verified_answer", answer_id, _details(row), user=user)
    return {"ok": True}
