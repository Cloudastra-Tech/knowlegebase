"""
Who is calling the public API (/api/v1, kb/routers/api_v1.py) or the MCP server
(/mcp, kb/mcp_server.py): an API key (api_keys_store.py), sent as
"Authorization: Bearer kb_live_...". A browser session doesn't work there, and a
key doesn't work on the app's own /api/* (kb/deps.current_user takes sessions only).

Checked on EVERY call, like a session is:
  * the key: known, not revoked, not expired;
  * the workspace still lets keys in: the API, or the MCP server, is on
    (api_settings), and the MCP server takes personal keys only;
  * a personal key's person: still a member of the key's workspace and not
    turned off, with their role as it is NOW, so a demotion limits the key at
    once; a service key's admin: still an enabled admin of the workspace;
  * the scope the call needs is one of the key's, and the person's role still
    allows it (api_keys_store.SCOPE_ROLES).
Then the key's rate limit (kb/rate_limit.key_limit).

A refusal never says more than the caller may know: an unknown, revoked and
expired key all read the same.
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import access
import api_keys_store
import groups_store
import lifecycle_store
import members_store
import workspaces
from kb import logs
from kb.rate_limit import key_limit

INVALID = "A valid API key is required: Authorization: Bearer kb_live_..."


class KeyRefused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass(frozen=True)
class Principal:
    key_id: int
    prefix: str
    kind: str                  # personal / service
    workspace: str
    username: str              # who it acts as: the person, or "key:<prefix>" (service)
    name: str
    member_role: str           # their role now: public / hr / admin (a service key: public)
    scopes: tuple
    surface: str               # api / mcp

    @property
    def role(self):
        """The access role it searches and reads as (rag.ROLE_ACCESS)."""
        return "hr" if self.member_role in members_store.STAFF_ROLES else "public"

    @property
    def viewer(self):
        """What it may read (access.py): a personal key, its person's principals
        now; a service key, everyone's content only."""
        if self.kind != "personal":
            return access.for_role("public")
        return groups_store.principals_for(self.workspace, self.username, self.role)

    @property
    def user(self):
        """As kb/deps' current_user gives a signed-in person, for code that takes one."""
        return {"username": self.username, "name": self.name, "workspace": self.workspace,
                "role": self.member_role}

    def may(self, scope):
        return scope in self.scopes and self.member_role in api_keys_store.SCOPE_ROLES.get(
            scope, (self.member_role,))

    def record(self, audit, target_type=None, target_id=None, details=None, action=None):
        """Write `audit` (kb/audit.Audit) as done with this key."""
        actor = self.username if self.kind == "personal" else f"key:{self.prefix}"
        return audit.record(target_type, target_id,
                            {**(details or {}), "surface": self.surface, "key": self.prefix,
                             "key_kind": self.kind},
                            workspace=self.workspace, actor=actor, action=action,
                            actor_type="api_key")


def authenticate(authorization, surface, scope=None):
    """The Principal for an Authorization header's key, or KeyRefused (401 / 403)."""
    scheme, _, token = (authorization or "").strip().partition(" ")
    key = api_keys_store.find(token.strip()) if scheme.lower() == "bearer" else None
    if not key or not workspaces.valid(key["workspace"]):
        raise KeyRefused(401, INVALID)
    ws = key["workspace"]
    if lifecycle_store.status(ws) != lifecycle_store.ACTIVE:
        raise KeyRefused(403, "This workspace is suspended or being deleted.")
    settings = api_keys_store.settings(ws)
    if surface == "mcp":
        if not settings["mcp_enabled"]:
            raise KeyRefused(403, "Your workspace's admins have turned the MCP server off.")
        if key["kind"] != "personal":
            raise KeyRefused(403, "The MCP server takes personal API keys only, so answers "
                                  "follow the person asking.")
    elif not settings["api_enabled"]:
        raise KeyRefused(403, "Your workspace's admins have turned the API off.")

    member = members_store.check_group_admin(members_store.get(key["created_by"]))
    alive = member and member["workspace"] == ws and not member["disabled"]
    if key["kind"] == "personal":
        if not alive:
            raise KeyRefused(401, INVALID)
        username, name, role = member["username"], member["name"], member["role"]
    else:
        if not alive or member["role"] != "admin":
            raise KeyRefused(401, INVALID)
        username, name, role = f"key:{key['prefix']}", key["name"], "public"
    principal = Principal(key["id"], key["prefix"], key["kind"], ws, username, name, role,
                          tuple(key["scopes"]), surface)
    if scope and not principal.may(scope):
        if scope not in principal.scopes:
            raise KeyRefused(403, f"This key doesn't have the {scope} scope.")
        raise KeyRefused(403, f"This key's owner no longer has a role that allows {scope}.")
    return principal


_bearer = HTTPBearer(auto_error=False, description="An API key: kb_live_...")


def api_key(scope):
    """Depends(...) handing the endpoint the Principal of a key with `scope`."""
    def api_key(request: Request,
                credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
        authorization = (f"{credentials.scheme} {credentials.credentials}"
                         if credentials else "")
        try:
            principal = authenticate(authorization, "api", scope)
        except KeyRefused as e:
            raise HTTPException(e.status, e.message,
                                headers={"WWW-Authenticate": "Bearer"} if e.status == 401
                                else None)
        logs.bind_user(principal.user)
        key_limit(principal.key_id, principal.workspace)
        return principal
    return Depends(api_key)
