"""A new workspace's first steps (kb/services/onboarding.py), billing or not.

    GET    /api/onboarding            this person's first run (suggested questions until
                                      they've asked one); for admins, the checklist too
    PUT    /api/onboarding/dismissed  {dismissed}: put the checklist away (or back), for
                                      the whole workspace (admins)
    POST   /api/onboarding/sample     "Try it with a sample HR handbook" (HR / Admin)
    DELETE /api/onboarding/sample     remove it, from the index too, in one click
    PUT    /api/onboarding/tours/{tour}  {outcome: finished | skipped}: this member has
                                      seen the product tour (members, or admin for admins)
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
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
           "sample": onboarding.sample_state(ws),
           "tours": _tours_for(user)}
    if user["role"] == "admin" and role == "hr":
        out["checklist"] = onboarding.state(ws)
    return out


def _tours_for(user, tours=None):
    """The member's tours (`tours`, or read now): the admin one for admins only."""
    tours = tours if tours is not None else onboarding.tours(ws_of(user), user["username"])
    if user["role"] != "admin":
        tours.pop("admin", None)
    return tours


class TourIn(BaseModel):
    outcome: Literal["finished", "skipped"]


@router.put("/api/onboarding/tours/{tour}")
def tour_seen(tour: Literal["members", "admin"], body: TourIn, user=Depends(current_user)):
    """Their own, so not audited; kept on the server so it doesn't start again in
    another browser."""
    if tour == "admin" and user["role"] != "admin":
        raise HTTPException(403, "The admin tour is for admins.")
    return _tours_for(user, onboarding.mark_tour(ws_of(user), user["username"], tour,
                                                 body.outcome))


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
