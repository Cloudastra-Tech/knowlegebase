"""
Sign-up: a new company creates its workspace.

The founder names the workspace, then signs in with Google. Their Google
Workspace account proves their company owns its primary domain (the `hd`
claim), so that domain is verified for the new workspace straight away and
they become its admin. It reuses the Google callback (routers/sso.py), so the
provider needs no new redirect URI: /start (routers/signup.py) marks this
browser's sign-in cookie, the callback takes the mark off.
"""

import logging
import os

from fastapi.responses import RedirectResponse

import kb.audit
import members_store
import sso
import sso_errors
from kb.services.sign_in import ProviderFailed, code_for, login_error, provider_answer

log = logging.getLogger("uvicorn.error")

SIGNUP_KEY = "signup"
SIGNUP_SECONDS = 15 * 60


def signup_on():
    """Can a new company create a workspace here? Only when whoever runs the server
    says so (WORKSPACE_SIGNUP=on), and only with Google Workspace, which proves the
    founder's company owns its domain."""
    return (os.getenv("WORKSPACE_SIGNUP", "off").strip().lower() in ("on", "true", "1", "yes")
            and "google" in sso.configured())


def signup_codes():
    """Invite codes (WORKSPACE_SIGNUP_CODES, comma-separated). When there are any,
    sign-up needs one, and each makes one workspace."""
    return {c.strip() for c in os.getenv("WORKSPACE_SIGNUP_CODES", "").split(",") if c.strip()}


async def finish_signup(c, request, name, code=None):
    """Google sent the founder back: create the workspace, or say why not."""
    try:
        claims, _ = await provider_answer("google", c, request)
        email, person, org = sso.identity("google", claims)
    except ProviderFailed as e:
        ref = sso_errors.new_ref()
        log.warning("Sign-up: Google sign-in failed (%s), reference %s", e.code, ref)
        return login_error(e.error, ref, tab="signup")
    except sso.NotAllowed as e:    # unverified, or a personal account
        return login_error(e.reason, tab="signup")
    if email.rpartition("@")[2] != org:
        return login_error("signup_domain", tab="signup")
    codes = signup_codes()
    if codes and code not in codes:          # removed from the list meanwhile
        return login_error("invite", tab="signup")
    try:
        workspace = members_store.create_workspace(name, org, email, person,
                                                   code=code if codes else None)
    except members_store.WorkspaceTaken as e:
        return login_error(e.reason, tab="signup")
    except Exception:
        ref = sso_errors.new_ref()
        log.exception("Sign-up: couldn't create the workspace, reference %s", ref)
        return login_error("sso_failed", ref, tab="signup")
    log.warning("Sign-up: %s created the workspace %s for %s", email, workspace, org)
    kb.audit.record(request, workspace, "workspace.create", email, "workspace", workspace,
                    {"name": name, "domain": org, "invite_code": bool(code and codes)})
    members_store.log_event(workspace, "google", email, "signed_in")
    user = {"username": email, "name": person, "provider": "google", "org": org,
            "workspace": workspace, "role": "admin"}
    return RedirectResponse("/#sso?welcome=1&code=" + code_for(request, user))
