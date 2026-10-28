"""Access groups (platform.md PL-09; groups_store.py): who may see content. Roles
gate screens; groups gate documents (access.py).

The Access screen's Groups tab (admins): manual groups made and filled here, and
the identity provider's groups (from sign-in claims and SCIM) read-only, with
their members. The built-in group "HR & Admin" is everyone whose role is HR or
Admin. The pickers that say who may see a document (HR / Admin) list everyone,
the groups and the members (GET /api/access/principals)."""

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

import access
import groups_store as store
from kb.audit import audited
from kb.deps import require_admin, require_hr, ws_of

router = APIRouter()

GROUP_ID = Path(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class GroupIn(BaseModel):
    name: str = Field(max_length=store.NAME_CHARS)
    description: str = Field("", max_length=store.DESCRIPTION_CHARS)


class GroupPatch(BaseModel):
    name: str | None = Field(None, max_length=store.NAME_CHARS)
    description: str | None = Field(None, max_length=store.DESCRIPTION_CHARS)


class MembersIn(BaseModel):
    usernames: list[str] = Field(max_length=5000)


def _view(ws, g, names):
    users = store.members(ws, g["id"])
    return {"id": g["id"], "name": g["name"], "source": g["source"],
            "description": g.get("description") or "", "email": g.get("email"),
            "editable": g["source"] == "manual", "principal": access.group(g["id"]),
            "members": [{"username": u, "name": names.get(u) or u} for u in users],
            "updated_at": g.get("updated_at")}


@router.get("/api/admin/groups")
def list_groups(user=Depends(require_admin)):
    """Every group: the built-in one, the manual ones, and the identity provider's
    (idp: sign-in claims; scim: pushed), with their members."""
    ws = ws_of(user)
    names = store.workspace_members(ws)
    return {"groups": [_view(ws, g, names) for g in store.list_groups(ws)],
            "members": [{"username": u, "name": n or u} for u, n in sorted(names.items())]}


@router.post("/api/admin/groups")
def create_group(body: GroupIn, user=Depends(require_admin), audit=audited("group.create")):
    ws = ws_of(user)
    try:
        g = store.create(ws, body.name, body.description, user["username"])
    except store.Invalid as e:
        raise HTTPException(400, str(e))
    audit.record("group", g["id"], {"name": g["name"]}, user=user)
    return _view(ws, g, store.workspace_members(ws))


def _manual(ws, group_id):
    g = store.get(ws, group_id)
    if not g:
        raise HTTPException(404, "That group doesn't exist.")
    if g["source"] != "manual":
        raise HTTPException(400, "This group comes from your identity provider or roles: "
                                 "change it there.")
    return g


@router.patch("/api/admin/groups/{group_id}")
def update_group(body: GroupPatch, group_id: str = GROUP_ID, user=Depends(require_admin),
                 audit=audited("group.update")):
    ws = ws_of(user)
    _manual(ws, group_id)
    try:
        g = store.update(ws, group_id, body.name, body.description)
    except store.Invalid as e:
        raise HTTPException(400, str(e))
    audit.record("group", group_id, {k: v for k, v in body.model_dump().items()
                                     if v is not None}, user=user)
    return _view(ws, g, store.workspace_members(ws))


@router.put("/api/admin/groups/{group_id}/members")
def set_group_members(body: MembersIn, group_id: str = GROUP_ID, user=Depends(require_admin),
                      audit=audited("group.members")):
    """Replace a manual group's members. What they may see changes at their next
    question: principals are looked up on every request."""
    ws = ws_of(user)
    _manual(ws, group_id)
    before = set(store.members(ws, group_id))
    try:
        after = store.set_members(ws, group_id, body.usernames, user["username"])
    except store.Invalid as e:
        raise HTTPException(400, str(e))
    audit.record("group", group_id, {"added": sorted(set(after) - before),
                                     "removed": sorted(before - set(after))}, user=user)
    return _view(ws, store.get(ws, group_id), store.workspace_members(ws))


@router.delete("/api/admin/groups/{group_id}")
def delete_group(group_id: str = GROUP_ID, user=Depends(require_admin),
                 audit=audited("group.delete")):
    """A manual group goes. Documents shared with it keep the grant, which no one
    holds any more: they're hidden from its former members (fails closed)."""
    ws = ws_of(user)
    g = _manual(ws, group_id)
    store.delete(ws, group_id)
    audit.record("group", group_id, {"name": g["name"]}, user=user)
    return {"ok": True}


@router.get("/api/access/principals")
def principals(user=Depends(require_hr)):
    """Who a document can be shared with, for the access pickers: everyone, every
    group, every member."""
    ws = ws_of(user)
    names = store.workspace_members(ws)
    return {"principals": [{"principal": access.EVERYONE, "kind": "everyone",
                            "label": "Everyone in the workspace"}]
            + [{"principal": access.group(g["id"]), "kind": "group", "label": g["name"],
                "source": g["source"]} for g in store.list_groups(ws)]
            + [{"principal": access.user(u), "kind": "user", "label": n or u, "detail": u}
               for u, n in sorted(names.items())]}
