"""A new workspace's first steps (kb/services/onboarding.py), billing or not.

    GET    /api/onboarding            this person's first run (suggested questions until
                                      they've asked one); for admins, the checklist too
    PUT    /api/onboarding/dismissed  {dismissed}: put the checklist away (or back), for
                                      the whole workspace (admins)
    POST   /api/onboarding/sample     "Try it with a sample HR handbook" (HR / Admin)
    DELETE /api/onboarding/sample     remove it, from the index too, in one click
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from kb.audit import audited
from kb.deps import current_role, current_user, current_viewer, require_admin, require_hr, ws_of
from kb.routers.knowledge import listing
from kb.services import onboarding

router = APIRouter()


@router.get("/api/onboarding")
def onboarding_view(user=Depends(current_user), role=Depends(current_role),
                    viewer=Depends(current_viewer)):
    ws = ws_of(user)
    first_run = not onboarding.asked_before(ws, user["username"])
    out = {"first_run": first_run,
           "suggestions": (onboarding.suggestions(listing(user, role, viewer)["items"])
                           if first_run else []),
           "sample": onboarding.sample_state(ws)}
    if user["role"] == "admin" and role == "hr":
        out["checklist"] = onboarding.state(ws)
    return out


class DismissIn(BaseModel):
    dismissed: bool


@router.put("/api/onboarding/dismissed")
def dismiss_checklist(body: DismissIn, user=Depends(require_admin),
                      audit=audited("onboarding.dismiss")):
    onboarding.dismiss(ws_of(user), body.dismissed)
    audit.record("workspace", ws_of(user), {"dismissed": body.dismissed}, user=user)
    return onboarding.state(ws_of(user))


@router.post("/api/onboarding/sample")
def add_sample_docs(user=Depends(require_hr), audit=audited("onboarding.sample_add")):
    keys = onboarding.add_sample(ws_of(user))
    audit.record("doc", "sample", {"files": keys}, user=user)
    return onboarding.sample_state(ws_of(user))


@router.delete("/api/onboarding/sample")
def remove_sample_docs(user=Depends(require_hr), audit=audited("onboarding.sample_remove")):
    keys = onboarding.remove_sample(ws_of(user))
    audit.record("doc", "sample", {"files": keys}, user=user)
    return onboarding.sample_state(ws_of(user))
