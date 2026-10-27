"""Saved prompts and templates (chat.md C22): a person's own, and the workspace's,
which its admins keep. A body may hold {{variables}}: the composer asks for each
before the prompt goes in (prompts_store.variables / fill). Typing "/" at the
start of the composer, or the command palette, lists them."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import prompts_store as store
from kb.audit import audited
from kb.deps import current_role, current_user, ws_of

router = APIRouter()


class PromptIn(BaseModel):
    title: str
    body: str
    workspace: bool = False          # a workspace prompt (admins)


class PromptEdit(BaseModel):
    title: str
    body: str


def _admin(user, role):
    return user["role"] == "admin" and role == "hr"


def view(p, user, role):
    shared = p["owner"] is None
    return {"id": p["id"], "title": p["title"], "body": p["body"], "variables": p["variables"],
            "workspace": shared, "editable": _admin(user, role) if shared else True,
            "updated_at": p["updated_at"]}


def _checked(title, body):
    title = " ".join((title or "").split())[:store.TITLE_CHARS]
    body = (body or "").strip()
    if not title:
        raise HTTPException(400, "Please give the prompt a name.")
    if not body:
        raise HTTPException(400, "Please write the prompt.")
    if len(body) > store.BODY_CHARS:
        raise HTTPException(400, f"A prompt can be up to {store.BODY_CHARS} characters.")
    if len(store.variables(body)) > store.MAX_VARIABLES:
        raise HTTPException(400, f"A prompt can have up to {store.MAX_VARIABLES} "
                                 "{{variables}}.")
    return title, body


def _editable(user, role, prompt_id):
    p = store.get(ws_of(user), prompt_id)
    if not p or (p["owner"] is not None and p["owner"] != user["username"]):
        raise HTTPException(404, "That prompt doesn't exist.")
    if p["owner"] is None and not _admin(user, role):
        raise HTTPException(403, "Only admins can change the workspace's prompts.")
    return p


@router.get("/api/prompts")
def list_prompts(user=Depends(current_user), role=Depends(current_role)):
    """The workspace's prompts, then the person's own."""
    return {"prompts": [view(p, user, role)
                        for p in store.visible(ws_of(user), user["username"])],
            "can_manage_workspace": _admin(user, role)}


@router.post("/api/prompts")
def create_prompt(body: PromptIn, user=Depends(current_user), role=Depends(current_role),
                  audit=audited("prompt.create")):
    ws = ws_of(user)
    if body.workspace and not _admin(user, role):
        raise HTTPException(403, "Only admins can add prompts for the whole workspace.")
    owner = None if body.workspace else user["username"]
    if store.count(ws, owner) >= store.MAX_PER_OWNER:
        raise HTTPException(409, f"There can be up to {store.MAX_PER_OWNER} prompts here.")
    title, text = _checked(body.title, body.body)
    p = store.create(ws, owner, title, text, user["username"])
    audit.record("prompt", p["id"], {"workspace": body.workspace}, user=user)
    return view(p, user, role)


@router.put("/api/prompts/{prompt_id}")
def update_prompt(prompt_id: str, body: PromptEdit, user=Depends(current_user),
                  role=Depends(current_role), audit=audited("prompt.update")):
    p = _editable(user, role, prompt_id)
    title, text = _checked(body.title, body.body)
    p = store.update(ws_of(user), prompt_id, title, text)
    audit.record("prompt", prompt_id, {"workspace": p["owner"] is None}, user=user)
    return view(p, user, role)


@router.delete("/api/prompts/{prompt_id}")
def delete_prompt(prompt_id: str, user=Depends(current_user), role=Depends(current_role),
                  audit=audited("prompt.delete")):
    p = _editable(user, role, prompt_id)
    store.delete(ws_of(user), prompt_id)
    audit.record("prompt", prompt_id, {"workspace": p["owner"] is None}, user=user)
    return {"ok": True}
