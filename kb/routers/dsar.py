"""Data requests about one member (kb/services/dsar.py): everything the workspace
holds about them, as JSON, and erasing it. The workspace's own admins only, never
support opened as one; nobody can erase themselves (another admin does)."""

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

import members_store
from kb.audit import audited
from kb.deps import require_tenant_admin, ws_of
from kb.services import dsar

router = APIRouter()


def _member(user, username):
    member = members_store.get(username)
    if not member or member["workspace"] != ws_of(user):
        raise HTTPException(404, "This workspace has no such member.")
    return member


@router.get("/api/admin/members/{username}/export")
def export_member_data(username: str, user=Depends(require_tenant_admin),
                       audit=audited("member.data_export")):
    member = _member(user, username)
    data = dsar.export_member(member["workspace"], member["username"])
    audit.record("member", member["username"],
                 {k: len(v) for k, v in data.items() if isinstance(v, list)}, user=user)
    name = f"member-data-{time.strftime('%Y%m%d', time.gmtime())}.json"
    return Response(json.dumps(data, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class EraseIn(BaseModel):
    confirm: str = Field(max_length=320)       # their username, typed


@router.post("/api/admin/members/{username}/erase")
def erase_member_data(username: str, body: EraseIn, user=Depends(require_tenant_admin),
                      audit=audited("member.erase")):
    member = _member(user, username)
    if member["username"] == user["username"]:
        raise HTTPException(400, "You can't erase yourself. Ask another admin.")
    if body.confirm.strip().lower() != member["username"]:
        raise HTTPException(400, "Type their username exactly to erase their data.")
    pseudonym, counts = dsar.erase_member(member["workspace"], member["username"])
    # The event names them by their pseudonym only.
    audit.record("member", pseudonym, counts, user=user)
    return {"ok": True, "pseudonym": pseudonym, "deleted": counts}
