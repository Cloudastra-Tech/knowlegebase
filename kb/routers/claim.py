"""Claiming a workspace nobody can administer any more, with Google Workspace and a
one-time code from an operator (join_store.py has the rules and why).

    POST /api/auth/claim                {code} -> {domain, url}: the code is checked and
                                        kept in this browser's sign-in cookie
    GET  /api/auth/claim/google/start   Google sends the claimant back to its usual
                                        callback (routers/sso.py), which finishes it
                                        (kb/services/joining.py)

Codes come from the operator console (POST /api/operator/workspaces/{ws}/claim-code)
or `python auth.py claim-code`.
"""

import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import audit_store
import join_store
import sso
from kb.audit import audited
from kb.deps import public_url
from kb.rate_limit import rate_limit
from kb.services.joining import CLAIM_KEY, CLAIM_SECONDS
from kb.services.sign_in import login_error, mark_browser
from kb.services.signup import SIGNUP_KEY

router = APIRouter()
log = logging.getLogger("uvicorn.error")

# Why a workspace can't be claimed (join_store.ClaimRefused), for the claim page.
REFUSED = {
    "claim_code": (400, "That claim code isn't valid, or it has expired or been used. Ask "
                        "whoever runs this server for a new one."),
    "claim_verified": (409, "This workspace already has a verified domain: sign in with it "
                            "instead, or ask one of its admins."),
    "claim_admin": (409, "This workspace still has an admin who can sign in. Ask them to "
                         "make you an admin."),
    "claim_taken": (409, "That domain or your account already belongs to another workspace."),
    "workspace_closed": (409, "This workspace is suspended or being deleted."),
}


class ClaimIn(BaseModel):
    code: str = Field(min_length=1, max_length=200)


def _refused(reason):
    status, message = REFUSED.get(reason, (400, "This workspace can't be claimed."))
    return HTTPException(status, message)


@router.post("/api/auth/claim")
def claim_start(body: ClaimIn, request: Request, audit=audited("workspace.claim_start")):
    """Check a claim code before sending the browser to Google."""
    rate_limit(request, "claim")
    if not sso.client("google"):
        raise HTTPException(400, "Google sign-in isn't set up on this server, so workspaces "
                                 "can't be claimed here.")
    found = join_store.find_code(body.code)
    if not found:
        raise _refused("claim_code")
    refused = join_store.claimable(found["workspace"])
    if refused:
        raise _refused(refused)
    mark_browser(request)
    request.session.pop(SIGNUP_KEY, None)
    request.session[CLAIM_KEY] = {"code": body.code.strip(), "at": time.time()}
    # Nobody is signed in yet: the operators' chain has it.
    audit.record("workspace", found["workspace"], {"domain": found["domain"],
                                                    "claim_code": found["id"]},
                 workspace=audit_store.PLATFORM, actor=None)
    return {"domain": found["domain"], "url": "/api/auth/claim/google/start"}


@router.get("/api/auth/claim/google/start")
async def claim_google_start(request: Request):
    rate_limit(request, "sso_start")
    claim = request.session.get(CLAIM_KEY)
    c = sso.client("google")
    if not c or not claim or time.time() - claim.get("at", 0) > CLAIM_SECONDS:
        return login_error("expired", tab="claim")
    found = join_store.find_code(claim["code"])
    if not found:
        return login_error("claim_code", tab="claim")
    try:
        # hd: Google shows only accounts of that Workspace (a hint; the callback checks).
        return await c.authorize_redirect(
            request, public_url(request, "/api/auth/sso/google/callback"),
            prompt="select_account", hd=found["domain"])
    except Exception:              # couldn't reach Google's discovery document
        log.exception("Claim: couldn't start the Google sign-in")
        return login_error("sso_failed", tab="claim")
