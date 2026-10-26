"""Sign-up: a new company creates its workspace (how: services/signup.py). Google
sends the founder back to its usual callback (routers/sso.py)."""

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import members_store
import sso
from kb.deps import public_url
from kb.rate_limit import rate_limit
from kb.services.sign_in import login_error, mark_browser
from kb.services.signup import SIGNUP_KEY, signup_codes, signup_on

router = APIRouter()
log = logging.getLogger("uvicorn.error")


@router.get("/api/auth/signup/google/start")
async def signup_start(request: Request, name: str = "", code: str = ""):
    rate_limit(request, "signup")
    name, code = " ".join(name.split()), code.strip()
    if not signup_on():
        return login_error("signup_off")
    if not 1 <= len(name) <= 60:
        return RedirectResponse("/#signup?error=name")
    codes = signup_codes()
    if codes and (code not in codes or members_store.signup_code_used(code)):
        return RedirectResponse("/#signup?error=invite")
    c = sso.client("google")
    mark_browser(request)
    request.session[SIGNUP_KEY] = {"name": name, "code": code if codes else None,
                                   "at": time.time()}
    try:
        return await c.authorize_redirect(
            request, public_url(request, "/api/auth/sso/google/callback"),
            **sso.authorize_params("google"))
    except Exception:              # couldn't reach Google's discovery document
        log.exception("Sign-up: couldn't start the Google sign-in")
        return login_error("sso_failed", tab="signup")
