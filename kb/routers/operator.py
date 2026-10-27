"""The platform operator console (kb/operators.py, kb/services/operator.py): every
workspace's state and usage, and what an operator may do about it. Only with an
operator's session (single sign-on, OPERATOR_EMAILS); every action is in the
workspace's audit log as actor_type "operator", and in the operators' own
chain (audit_store.PLATFORM).

    GET  /api/operator/providers            how operators sign in
    GET  /api/operator/sso/{provider}/start | /callback
    POST /api/operator/auth/exchange        {code} -> {token, operator}
    POST /api/operator/auth/logout
    GET  /api/operator/me
    GET  /api/operator/workspaces           every workspace, with seats and usage
    POST /api/operator/workspaces/{ws}/suspend {reason} | /unsuspend
    POST /api/operator/workspaces/{ws}/reindex | /reset-rate-limits
    POST /api/operator/workspaces/{ws}/extend-trial {days}  more trial (billing.py)
    POST /api/operator/workspaces/{ws}/billing-owner {email}  who owns its billing
    POST /api/operator/workspaces/{ws}/support-session  -> a one-time code (only while
                                            the workspace's admin allows support)
    GET  /api/operator/workspaces/{ws}/audit   its audit log
    GET  /api/operator/audit                the operators' own chain
"""

import logging
import secrets
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import api_keys_store
import audit_store
import billing_store
import db
import lifecycle_store
import members_store
import sessions_store
import sso
import workspaces
from kb import operators, rate_limit
from kb.audit import audited, client_ip
from kb.deps import public_url
from kb.operators import require_operator
from kb.services import billing, indexing
from kb.services import operator as console
from kb.services.sign_in import BROWSER_KEY, ProviderFailed, code_for, mark_browser, \
    provider_answer

router = APIRouter()
log = logging.getLogger("uvicorn.error")


def _back(**params):
    return RedirectResponse("/operator#" + urllib.parse.urlencode(params), status_code=303)


@router.get("/api/operator/providers")
def operator_providers():
    return {"providers": [{"id": p, "name": sso.label(p),
                           "url": f"/api/operator/sso/{p}/start"}
                          for p in operators.providers(sso.configured())]}


@router.get("/api/operator/sso/{provider}/start")
async def operator_sso_start(provider: str, request: Request):
    rate_limit.rate_limit(request, "sso_start")
    c = sso.client(provider) if provider in operators.providers(sso.configured()) else None
    if not c:
        return _back(error="sso_unavailable")
    mark_browser(request)
    try:
        return await c.authorize_redirect(
            request, public_url(request, f"/api/operator/sso/{provider}/callback"),
            **sso.authorize_params(provider))
    except Exception:
        log.exception("Operator SSO %s: couldn't start sign-in", provider)
        return _back(error="sso_failed")


@router.get("/api/operator/sso/{provider}/callback")
async def operator_sso_callback(provider: str, request: Request):
    c = sso.client(provider) if provider in operators.providers(sso.configured()) else None
    if not c:
        return _back(error="sso_unavailable")
    try:
        claims, _ = await provider_answer(f"operator {provider}", c, request)
        email, name, _ = sso.identity(provider, claims)
    except ProviderFailed as e:
        return _back(error=e.error)
    except sso.NotAllowed as e:
        return _back(error=e.reason)
    if not operators.is_operator(email):
        log.warning("Operator sign-in refused for %s: not in OPERATOR_EMAILS", email)
        return _back(error="not_operator")
    if operators.mfa_required() and not operators.used_mfa(claims):
        return _back(error="mfa_required")
    operator = {"username": email, "name": name, "operator": True, "provider": provider}
    return _back(code=code_for(request, operator))


class CodeIn(BaseModel):
    code: str = Field(max_length=200)


@router.post("/api/operator/auth/exchange")
def operator_exchange(body: CodeIn, request: Request, audit=audited("operator.sign_in")):
    operator = sso.redeem_code(body.code, request.session.get(BROWSER_KEY))
    if not operator or not operator.get("operator") or not operators.is_operator(
            operator["username"]):
        raise HTTPException(401, "That sign-in expired. Please sign in again.")
    token = secrets.token_urlsafe(32)
    sessions_store.create(token, operator, operators.session_seconds())
    audit.record("operator", operator["username"], {"provider": operator["provider"]},
                 workspace=audit_store.PLATFORM, actor=operator["username"],
                 actor_type="operator")
    return {"token": token, "operator": _public(operator)}


def _public(operator):
    return {"email": operator["username"], "name": operator.get("name"),
            "provider": operator.get("provider")}


@router.post("/api/operator/auth/logout")
def operator_logout(request: Request, operator=Depends(require_operator),
                    audit=audited("operator.sign_out")):
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    sessions_store.delete(token)
    audit.record("operator", operator["username"], workspace=audit_store.PLATFORM,
                 actor=operator["username"], actor_type="operator")
    return {"ok": True}


@router.get("/api/operator/me")
def operator_me(operator=Depends(require_operator)):
    return _public(operator)


@router.get("/api/operator/workspaces")
def operator_workspaces(operator=Depends(require_operator)):
    return {"workspaces": console.overview()}


def _workspace(ws):
    conn = db.connect()
    try:
        found = workspaces.valid(ws) and conn.execute("SELECT 1 FROM workspaces WHERE slug = ?",
                                                      (ws,)).fetchone()
    finally:
        conn.close()
    if not found:
        raise HTTPException(404, "No such workspace.")
    return ws


def _record(audit, operator, ws, details=None):
    """In the workspace's log and the operators' own."""
    audit.record("workspace", ws, details, workspace=ws, actor=operator["username"],
                 actor_type="operator")
    audit_store.record(audit_store.PLATFORM, audit.action, actor=operator["username"],
                       actor_type="operator", target_type="workspace", target_id=ws,
                       details=details, ip=client_ip(audit.request))


class SuspendIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/api/operator/workspaces/{ws}/suspend")
def operator_suspend(ws: str, body: SuspendIn, operator=Depends(require_operator),
                     audit=audited("operator.suspend")):
    ws = _workspace(ws)
    lifecycle_store.suspend(ws, body.reason.strip(), operator["username"])
    ended = _end_sessions(ws)
    _record(audit, operator, ws, {"reason": body.reason.strip(), "sessions_ended": ended})
    return {"ok": True}


def _end_sessions(ws):
    conn = db.connect()
    try:
        with conn:
            return conn.execute("DELETE FROM sessions WHERE workspace = ?", (ws,)).rowcount
    finally:
        conn.close()


@router.post("/api/operator/workspaces/{ws}/unsuspend")
def operator_unsuspend(ws: str, operator=Depends(require_operator),
                       audit=audited("operator.unsuspend")):
    ws = _workspace(ws)
    if not lifecycle_store.unsuspend(ws):
        raise HTTPException(409, "It isn't suspended.")
    _record(audit, operator, ws)
    return {"ok": True}


@router.post("/api/operator/workspaces/{ws}/reindex")
def operator_reindex(ws: str, operator=Depends(require_operator),
                     audit=audited("operator.reindex")):
    ws = _workspace(ws)
    indexing.schedule_index(ws, full=True)
    _record(audit, operator, ws)
    return {"ok": True}


@router.post("/api/operator/workspaces/{ws}/reset-rate-limits")
def operator_reset_rate_limits(ws: str, operator=Depends(require_operator),
                               audit=audited("operator.reset_rate_limits")):
    ws = _workspace(ws)
    people = [m["username"] for m in members_store.list_members(ws)]
    keys = [k["id"] for k in api_keys_store.list_keys(ws)]
    n = rate_limit.reset_workspace(ws, people, keys)
    _record(audit, operator, ws, {"counters": n})
    return {"ok": True, "counters": n}


class ExtendTrialIn(BaseModel):
    days: int = Field(ge=1, le=90)


@router.post("/api/operator/workspaces/{ws}/extend-trial")
def operator_extend_trial(ws: str, body: ExtendTrialIn, operator=Depends(require_operator),
                          audit=audited("operator.extend_trial")):
    """More trial for a workspace: from its end, or from now if it has ended (which
    lifts read-only, and cancels the deletion an expired trial asked for)."""
    ws = _workspace(ws)
    ends = billing.extend_trial(ws, body.days)
    _record(audit, operator, ws, {"days": body.days, "trial_ends_at": ends})
    return {"ok": True, "trial_ends_at": ends}


class OwnerIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


@router.post("/api/operator/workspaces/{ws}/billing-owner")
def operator_billing_owner(ws: str, body: OwnerIn, operator=Depends(require_operator),
                           audit=audited("operator.billing_owner")):
    """Make a member the owner of the workspace's billing (a workspace made before
    billing, or whose owner left)."""
    ws = _workspace(ws)
    member = members_store.get(body.email)
    if not member or member["workspace"] != ws or member["disabled"]:
        raise HTTPException(404, "That person isn't a member of this workspace.")
    billing_store.add_owner(ws, member["username"], operator["username"])
    _record(audit, operator, ws, {"owner": member["username"]})
    return {"ok": True}


@router.post("/api/operator/workspaces/{ws}/support-session")
def operator_support_session(ws: str, request: Request, operator=Depends(require_operator),
                             audit=audited("operator.support_session")):
    """Open the workspace as support: a one-time code for /api/auth/sso/exchange.
    Only while its admin allows support, and the session ends with the grant."""
    ws = _workspace(ws)
    user = operators.support_user(operator, ws)
    if not operators.support_access(user):
        raise HTTPException(403, "The workspace's admins haven't allowed support access "
                                 "(or it's suspended).")
    _record(audit, operator, ws, {"support_until": lifecycle_store.state(ws)["support_until"]})
    mark_browser(request)
    return {"code": code_for(request, user)}


@router.get("/api/operator/workspaces/{ws}/audit")
def operator_workspace_audit(ws: str, request: Request, cursor: int | None = None,
                             limit: int = Query(audit_store.PAGE, ge=1, le=audit_store.MAX_PAGE),
                             operator=Depends(require_operator)):
    ws = _workspace(ws)
    events, next_cursor = audit_store.list_events(ws, cursor=cursor, limit=limit)
    if cursor is None:                     # reading a workspace's log is itself logged
        audit_store.record(ws, "operator.audit_view", actor=operator["username"],
                           actor_type="operator", target_type="workspace", target_id=ws,
                           ip=client_ip(request))
    return {"events": events, "next_cursor": next_cursor,
            "verify": audit_store.verify(ws)}


@router.get("/api/operator/audit")
def operator_audit(cursor: int | None = None,
                   limit: int = Query(audit_store.PAGE, ge=1, le=audit_store.MAX_PAGE),
                   operator=Depends(require_operator)):
    events, next_cursor = audit_store.list_events(audit_store.PLATFORM, cursor=cursor,
                                                  limit=limit)
    return {"events": events, "next_cursor": next_cursor}
