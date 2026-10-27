"""
Platform operators (platform.md PL-18): the people who run this server for every
workspace, kept apart from any workspace's members and roles.

WHO: an email in OPERATOR_EMAILS (comma-separated), signed in through one of the
server-wide single sign-on providers (sso.py; OPERATOR_SSO_PROVIDERS narrows
which), never with a password. OPERATOR_REQUIRE_MFA=on also requires the
provider to say a second factor was used (the ID token's `amr`). The list is
read again on every request, so removing an address ends that operator's access
at once. Their session (OPERATOR_SESSION_HOURS, default 4) is good for
/api/operator/... only; a member's session is never good there, nor theirs in a
workspace.

SUPPORT ACCESS: an operator can open a workspace as support only while its admin
allows it (lifecycle_store.grant_support, "Allow Cloudastra support for 72
hours"). That session acts as an admin of the workspace, as
"support:<operator email>", ends when the grant does, can't delete the
workspace, grant access or erase anyone (deps.require_tenant_admin), and EVERY
request it makes is in the workspace's audit log with actor_type "operator".
"""

import os
import time

from fastapi import Header, HTTPException, Request

import audit_store
import lifecycle_store
import sessions_store

SUPPORT_PREFIX = "support:"
MFA_METHODS = {"mfa", "otp", "hwk", "swk", "sms", "fpt", "face", "iris", "retina", "tel"}


def emails():
    return {e.strip().lower() for e in os.getenv("OPERATOR_EMAILS", "").split(",") if e.strip()}


def is_operator(email):
    return bool(email) and email.strip().lower() in emails()


def providers(configured):
    """The server-wide providers operators may sign in with, of those configured."""
    wanted = {p.strip() for p in os.getenv("OPERATOR_SSO_PROVIDERS", "").split(",") if p.strip()}
    return [p for p in configured if not wanted or p in wanted]


def mfa_required():
    return os.getenv("OPERATOR_REQUIRE_MFA", "off").strip().lower() in ("1", "on", "true", "yes")


def used_mfa(claims):
    amr = claims.get("amr") or []
    return bool(MFA_METHODS & {str(a).lower() for a in (amr if isinstance(amr, list) else [amr])})


def session_seconds():
    try:
        return max(1, int(os.getenv("OPERATOR_SESSION_HOURS", "4"))) * 3600
    except ValueError:
        return 4 * 3600


def require_operator(request: Request, authorization: str = Header(default="")):
    """The signed-in operator, from an operator session; 401 otherwise."""
    token = authorization.removeprefix("Bearer ").strip()
    user = sessions_store.get(token)
    if not user or not user.get("operator") or not is_operator(user.get("username")):
        if user and user.get("operator"):
            sessions_store.delete(token)        # no longer in OPERATOR_EMAILS
        raise HTTPException(401, "Sign in as an operator.")
    return user


# ---- Support access: an operator in a workspace ---------------------------------------
def support_user(operator, workspace):
    """The session user an operator gets in a workspace that allows support."""
    return {"username": SUPPORT_PREFIX + operator["username"], "name": "Cloudastra support",
            "impersonator": operator["username"], "workspace": workspace, "role": "admin"}


def support_access(user, now=None):
    """{"workspace", "role"} for a support session while it may still be used:
    the operator is still one, and the workspace still allows support and isn't
    suspended. None otherwise."""
    ws = user.get("workspace")
    if not (ws and is_operator(user.get("impersonator"))):
        return None
    if not lifecycle_store.support_open(ws, now) or \
            lifecycle_store.status(ws) == lifecycle_store.SUSPENDED:
        return None
    return {"workspace": ws, "role": "admin"}


def support_seconds(workspace, now=None):
    """How long a support session may last: an hour, or less if the grant ends sooner."""
    until = lifecycle_store.state(workspace)["support_until"] or 0
    return max(0, min(3600, int(until - (now or time.time()))))


def audit_request(user, request):
    """Every request made as support is in the workspace's audit log."""
    audit_store.record(user["workspace"], "support.request", actor=user["impersonator"],
                       actor_type="operator", target_type="request",
                       target_id=f"{request.method} {request.url.path}",
                       ip=request.client.host if request.client else None,
                       user_agent=request.headers.get("user-agent"))
