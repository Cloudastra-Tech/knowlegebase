"""
SCIM 2.0 provisioning: a workspace's identity provider (Okta, Entra ID, ...)
adds, changes and removes its members and groups. The protocol is in
kb/services/scim.py, the data and its rules in scim_store.py.

    /scim/v2/ServiceProviderConfig | Schemas | ResourceTypes     what this server supports
    GET    /scim/v2/Users          ?filter=userName eq "x"&startIndex&count
    POST   /scim/v2/Users          provision someone
    GET | PUT | PATCH | DELETE  /scim/v2/Users/{id}
    GET    /scim/v2/Groups         ?filter=displayName eq "x"&excludedAttributes=members
    POST   /scim/v2/Groups
    GET | PUT | PATCH | DELETE  /scim/v2/Groups/{id}

Every request carries "Authorization: Bearer <token>", one of the workspace's
SCIM tokens: the token alone decides the workspace. Answers and errors are
application/scim+json. Every change is in the audit log (actor_type "scim")
and in the workspace's SCIM events; refusals are in the events too.

The admin's side, under /api/admin/sign-in/scim (Access > Sign-in > Provisioning):
    GET    /api/admin/sign-in/scim             base URL, tokens, last sync, rules, events
    POST   /api/admin/sign-in/scim/tokens      a new token, shown once
    DELETE /api/admin/sign-in/scim/tokens/{id} revoke it
    PUT    /api/admin/sign-in/scim/roles       {rules, make_role_source}: roles from SCIM's groups
"""

import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

import lifecycle_store
import members_store
import scim_store
import sessions_store
from kb import logs
from kb.audit import audited
from kb.deps import public_url, require_admin, ws_of
from kb.routers.admin_sign_in import GroupRule
from kb.services import scim as protocol
from kb.services.scim import ScimError
from kb.services.sign_in import explained

log = logging.getLogger("uvicorn.error")
MEDIA_TYPE = "application/scim+json"


class ScimJSON(JSONResponse):
    media_type = MEDIA_TYPE


def _error(e):
    headers = {"WWW-Authenticate": 'Bearer realm="scim"'} if e.status == 401 else None
    return ScimJSON(e.body(), status_code=e.status, headers=headers)


class ScimRoute(APIRoute):
    """Every failure answered as a SCIM error, never FastAPI's own JSON."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def scim_handler(request):
            try:
                return await handler(request)
            except ScimError as e:
                return _error(e)
            except scim_store.Refused as e:
                return _error(ScimError(e.status, e.detail, e.scim_type))
            except RequestValidationError:
                return _error(ScimError(400, "The request isn't valid JSON.", "invalidSyntax"))
            except HTTPException as e:
                return _error(ScimError(e.status_code, str(e.detail)))
        return scim_handler


api = APIRouter(prefix="/scim/v2", route_class=ScimRoute, default_response_class=ScimJSON)
admin = APIRouter()


def scim_token(request: Request, authorization: str = Header(default="")):
    """The workspace whose token this is: {"workspace", "token_id", "base"}."""
    scheme, _, token = authorization.strip().partition(" ")
    found = scim_store.check_token(token.strip()) if scheme.lower() == "bearer" else None
    if not found:
        raise ScimError(401, "A valid SCIM bearer token is required.")
    workspace, token_id = found
    if lifecycle_store.status(workspace) != lifecycle_store.ACTIVE:
        raise ScimError(403, "This workspace is suspended or being deleted.")
    logs.bind(workspace=workspace)
    return {"workspace": workspace, "token_id": token_id,
            "base": public_url(request, "/scim/v2")}


def _actor(ctx):
    return f"scim:{ctx['token_id']}"


def _done(ctx, audit, action, target_type, target_id, details, event_email, code):
    """A SCIM write happened: the audit log and the SCIM events."""
    audit.record(target_type, target_id, details, workspace=ctx["workspace"], actor=_actor(ctx),
                 action=action, actor_type="scim")
    members_store.log_event(ctx["workspace"], members_store.SCIM_SOURCE, event_email, "scim", code)


def _refused(ctx, e, who):
    """Note a refusal in the SCIM events, then answer with it."""
    if isinstance(e, members_store.NoAdminLeft):
        members_store.log_event(ctx["workspace"], members_store.SCIM_SOURCE, who, "scim",
                                "scim_last_admin")
        return ScimError(400, "This would leave the workspace with no admin ("
                              f"{', '.join(e.admins)}). Make someone else admin first.",
                         "mutability")
    members_store.log_event(ctx["workspace"], members_store.SCIM_SOURCE, who, "scim", e.code)
    return ScimError(e.status, e.detail, e.scim_type)


# ---- Discovery -------------------------------------------------------------------------
@api.get("/ServiceProviderConfig")
def service_provider_config(ctx=Depends(scim_token)):
    return protocol.service_provider_config(ctx["base"])


@api.get("/Schemas")
def list_schemas(ctx=Depends(scim_token)):
    found = protocol.schemas(ctx["base"])
    return protocol.list_response(found, len(found), 1)


@api.get("/Schemas/{schema_id}")
def get_schema(schema_id: str, ctx=Depends(scim_token)):
    for s in protocol.schemas(ctx["base"]):
        if s["id"] == schema_id:
            return s
    raise ScimError(404, f"No schema {schema_id}.")


@api.get("/ResourceTypes")
def list_resource_types(ctx=Depends(scim_token)):
    found = protocol.resource_types(ctx["base"])
    return protocol.list_response(found, len(found), 1)


@api.get("/ResourceTypes/{name}")
def get_resource_type(name: str, ctx=Depends(scim_token)):
    for r in protocol.resource_types(ctx["base"]):
        if r["id"] == name:
            return r
    raise ScimError(404, f"No resource type {name}.")


# ---- Users ---------------------------------------------------------------------------------
@api.get("/Users")
def list_users(request: Request, ctx=Depends(scim_token)):
    q = request.query_params
    attr, value = protocol.parse_filter(q.get("filter"), scim_store.USER_FILTERS)
    start, count = protocol.page(q.get("startIndex"), q.get("count"))
    total, users = scim_store.list_users(ctx["workspace"], attr, value, start, count)
    return protocol.list_response(
        [protocol.pick_attributes(protocol.user_resource(u, ctx["base"]), q.get("attributes"),
                                  q.get("excludedAttributes")) for u in users], total, start)


def _user_or_404(ctx, user_id):
    user = scim_store.get_user(ctx["workspace"], user_id)
    if not user:
        raise ScimError(404, f"User {user_id} not found.")
    return user


@api.get("/Users/{user_id}")
def get_user(user_id: str, request: Request, ctx=Depends(scim_token)):
    q = request.query_params
    return protocol.pick_attributes(protocol.user_resource(_user_or_404(ctx, user_id), ctx["base"]),
                                    q.get("attributes"), q.get("excludedAttributes"))


@api.post("/Users", status_code=201)
def create_user(body: dict = Body(...), ctx=Depends(scim_token),
                audit=audited("scim.user.create")):
    user = protocol.user_from_resource(body)
    try:
        saved, taken_over = scim_store.create_user(ctx["workspace"], user)
    except (scim_store.Refused, members_store.NoAdminLeft) as e:
        raise _refused(ctx, e, user["email"] or user["user_name"])
    if not saved["active"]:
        sessions_store.delete_user(saved["username"], ctx["workspace"])
    _done(ctx, audit, None, "member", saved["username"],
          {"scim_id": saved["id"], "user_name": saved["user_name"], "active": saved["active"],
           "taken_over": taken_over}, saved["username"], "scim_user_created")
    return ScimJSON(protocol.user_resource(saved, ctx["base"]), status_code=201,
                    headers={"Location": f"{ctx['base']}/Users/{saved['id']}"})


def _save_user(ctx, audit, user_id, new):
    """PUT and PATCH: store the user's new attributes, end their sessions if
    they're turned off (or have a new email), and record it."""
    try:
        saved, changed = scim_store.update_user(ctx["workspace"], user_id, new)
    except LookupError:
        raise ScimError(404, f"User {user_id} not found.")
    except (scim_store.Refused, members_store.NoAdminLeft) as e:
        raise _refused(ctx, e, new["email"] or new["user_name"])
    if changed.get("active") is False:          # out now, on every device
        sessions_store.delete_user(saved["username"], ctx["workspace"])
    code = ("scim_user_deactivated" if changed.get("active") is False else
            "scim_user_reactivated" if changed.get("active") is True else "scim_user_updated")
    action = {"scim_user_deactivated": "scim.user.deactivate",
              "scim_user_reactivated": "scim.user.reactivate"}.get(code)
    _done(ctx, audit, action, "member", saved["username"],
          {"scim_id": saved["id"], "changed": changed}, saved["username"], code)
    return protocol.user_resource(saved, ctx["base"])


@api.put("/Users/{user_id}")
def replace_user(user_id: str, body: dict = Body(...), ctx=Depends(scim_token),
                 audit=audited("scim.user.update")):
    current = _user_or_404(ctx, user_id)
    return _save_user(ctx, audit, user_id, protocol.user_from_resource(body, current))


@api.patch("/Users/{user_id}")
def patch_user(user_id: str, body: dict = Body(...), ctx=Depends(scim_token),
               audit=audited("scim.user.update")):
    current = _user_or_404(ctx, user_id)
    return _save_user(ctx, audit, user_id, protocol.patch_user(current, body))


@api.delete("/Users/{user_id}", status_code=204)
def delete_user(user_id: str, ctx=Depends(scim_token), audit=audited("scim.user.delete")):
    try:
        username = scim_store.delete_user(ctx["workspace"], user_id)
    except members_store.NoAdminLeft as e:
        user = scim_store.get_user(ctx["workspace"], user_id)
        raise _refused(ctx, e, user["username"] if user else None)
    if not username:
        raise ScimError(404, f"User {user_id} not found.")
    sessions_store.delete_user(username, ctx["workspace"])
    _done(ctx, audit, None, "member", username,
          {"scim_id": user_id, "anonymise_after_days": scim_store.retention_days()},
          username, "scim_user_deleted")
    return Response(status_code=204)


# ---- Groups --------------------------------------------------------------------------------
@api.get("/Groups")
def list_groups(request: Request, ctx=Depends(scim_token)):
    q = request.query_params
    attr, value = protocol.parse_filter(q.get("filter"), scim_store.GROUP_FILTERS)
    start, count = protocol.page(q.get("startIndex"), q.get("count"))
    excluded = q.get("excludedAttributes") or ""
    with_members = "members" not in excluded.lower()
    total, groups = scim_store.list_groups(ctx["workspace"], attr, value, start, count,
                                           with_members)
    return protocol.list_response(
        [protocol.pick_attributes(protocol.group_resource(g, ctx["base"]), q.get("attributes"),
                                  excluded) for g in groups], total, start)


def _group_or_404(ctx, group_id, with_members=True):
    group = scim_store.get_group(ctx["workspace"], group_id, with_members)
    if not group:
        raise ScimError(404, f"Group {group_id} not found.")
    return group


@api.get("/Groups/{group_id}")
def get_group(group_id: str, request: Request, ctx=Depends(scim_token)):
    q = request.query_params
    excluded = q.get("excludedAttributes") or ""
    group = _group_or_404(ctx, group_id, "members" not in excluded.lower())
    return protocol.pick_attributes(protocol.group_resource(group, ctx["base"]),
                                    q.get("attributes"), excluded)


def _group_body(body):
    if not isinstance(body, dict):
        raise ScimError(400, "Send a Group object.", "invalidSyntax")
    b = {str(k).lower(): v for k, v in body.items()}
    members = b.get("members") or []
    if not isinstance(members, list):
        raise ScimError(400, "members must be a list.", "invalidValue")
    ids = [m.get("value") for m in members if isinstance(m, dict)]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(members):
        raise ScimError(400, "Each member needs a value (the user's id).", "invalidValue")
    return (protocol._text(b.get("displayname"), "displayName", 256),
            protocol._text(b.get("externalid"), "externalId"), ids)


def _save_group(ctx, audit, group_id, name, external_id, member_ids, action):
    try:
        group, created, unknown, changed = scim_store.save_group(
            ctx["workspace"], group_id, name, external_id, member_ids)
    except LookupError:
        raise ScimError(404, f"Group {group_id} not found.")
    except (scim_store.Refused, members_store.NoAdminLeft) as e:
        raise _refused(ctx, e, name)
    if unknown:
        log.warning("SCIM %s: group %s names users it has no one for: %s", ctx["workspace"],
                    group["id"], ", ".join(unknown[:20]))
    _done(ctx, audit, action, "scim_group", group["id"],
          {"display_name": group["display_name"], "members_changed": changed,
           "unknown_members": unknown[:50]}, group["display_name"],
          "scim_group_created" if created else "scim_group_updated")
    return group


@api.post("/Groups", status_code=201)
def create_group(body: dict = Body(...), ctx=Depends(scim_token),
                 audit=audited("scim.group.create")):
    name, external_id, ids = _group_body(body)
    group = _save_group(ctx, audit, None, name, external_id, ids, None)
    return ScimJSON(protocol.group_resource(group, ctx["base"]), status_code=201,
                    headers={"Location": f"{ctx['base']}/Groups/{group['id']}"})


@api.put("/Groups/{group_id}")
def replace_group(group_id: str, body: dict = Body(...), ctx=Depends(scim_token),
                  audit=audited("scim.group.update")):
    _group_or_404(ctx, group_id, with_members=False)
    name, external_id, ids = _group_body(body)
    return protocol.group_resource(
        _save_group(ctx, audit, group_id, name, external_id, ids, None), ctx["base"])


@api.patch("/Groups/{group_id}")
def patch_group(group_id: str, body: dict = Body(...), ctx=Depends(scim_token),
                audit=audited("scim.group.update")):
    current = _group_or_404(ctx, group_id)
    name, external_id, ids = protocol.patch_group(current, body)
    return protocol.group_resource(
        _save_group(ctx, audit, group_id, name, external_id, ids, None), ctx["base"])


@api.delete("/Groups/{group_id}", status_code=204)
def delete_group(group_id: str, ctx=Depends(scim_token), audit=audited("scim.group.delete")):
    try:
        name = scim_store.delete_group(ctx["workspace"], group_id)
    except members_store.NoAdminLeft as e:
        group = scim_store.get_group(ctx["workspace"], group_id, with_members=False)
        raise _refused(ctx, e, group["display_name"] if group else None)
    if name is None:
        raise ScimError(404, f"Group {group_id} not found.")
    _done(ctx, audit, None, "scim_group", group_id, {"display_name": name}, name,
          "scim_group_deleted")
    return Response(status_code=204)


# ---- The admin's side ---------------------------------------------------------------------
def _settings(request, ws):
    return {"base_url": public_url(request, "/scim/v2"),
            "tokens": scim_store.list_tokens(ws),
            "max_tokens": scim_store.MAX_TOKENS,
            "last_sync_at": scim_store.last_sync(ws),
            "role_source": members_store.role_source(ws),
            "group_rules": [{"group": r["group_name"], "role": r["role"]}
                            for r in members_store.scim_group_rules(ws)],
            "retention_days": scim_store.retention_days(),
            **scim_store.counts(ws),
            "events": [{**e, "connection": "SCIM", **explained(e["reason"])}
                       for e in members_store.recent_events(ws, scim=True)]}


@admin.get("/api/admin/sign-in/scim")
def scim_settings(request: Request, user=Depends(require_admin)):
    return _settings(request, ws_of(user))


@admin.post("/api/admin/sign-in/scim/tokens")
def create_scim_token(request: Request, user=Depends(require_admin),
                      audit=audited("scim.token.create")):
    try:
        row, token = scim_store.create_token(ws_of(user), user["username"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("scim_token", row["id"], {"hint": row["hint"]}, user=user)
    return {**row, "token": token}


@admin.delete("/api/admin/sign-in/scim/tokens/{token_id}")
def revoke_scim_token(token_id: int, user=Depends(require_admin),
                      audit=audited("scim.token.revoke")):
    row = scim_store.revoke_token(ws_of(user), token_id)
    if not row:
        raise HTTPException(404, "This workspace has no such active token.")
    audit.record("scim_token", token_id, {"hint": row["hint"]}, user=user)
    return row


class ScimRulesIn(BaseModel):
    rules: list[GroupRule] = Field(default_factory=list, max_length=200)
    make_role_source: bool = False   # take over setting roles from a provider


@admin.put("/api/admin/sign-in/scim/roles")
def set_scim_roles(body: ScimRulesIn, request: Request, user=Depends(require_admin),
                   audit=audited("scim.roles")):
    """Roles from the groups SCIM pushes: they apply at once. Rules make SCIM the
    workspace's one role source (members_store.py)."""
    ws = ws_of(user)
    try:
        members_store.set_scim_group_rules(ws, [(r.group, r.role) for r in body.rules],
                                           make_role_source=body.make_role_source)
    except members_store.RoleSourceTaken as e:
        source = members_store.get_connection(e.source)
        name = source["label"] if source else "another provider"
        raise HTTPException(409, f"Roles come from {name}. A workspace takes roles from one "
                                 "source: switch it to SCIM to save these rules.")
    except members_store.NoAdminLeft as e:
        who = ", ".join(e.admins)
        raise HTTPException(400, f"This would leave the workspace with no admin ({who}). "
                                 "Make someone else admin on the Members tab first.")
    audit.record("workspace", ws, {"rules": [r.model_dump() for r in body.rules],
                                   "role_source": members_store.role_source(ws)}, user=user)
    return _settings(request, ws)


router = APIRouter()               # both, for kb/app.py
router.include_router(api)
router.include_router(admin)
