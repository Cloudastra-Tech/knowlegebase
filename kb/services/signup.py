"""
Sign-up: a new company creates its workspace. Only when whoever runs the server
says so (WORKSPACE_SIGNUP=on), in one of three ways, each proving the company
owns its email domain before the workspace exists (WORKSPACE_SIGNUP_METHODS
narrows them, e.g. "google,microsoft365"):

  google        Google Workspace: the founder's account proves their company owns
                its primary domain (the `hd` claim), which must be their email's.
                It reuses the Google callback (routers/sso.py), so the provider
                needs no new redirect URI: /start (routers/signup.py) marks this
                browser's sign-in cookie, the callback takes the mark off.
  microsoft365  Microsoft 365, through the multi-tenant app (kb/services/m365.py):
                Microsoft Graph's `organization` lists the domains their Entra
                tenant has verified, and their email must be on one of them.
  email         anyone else: the founder confirms their email with a link, then
                adds a DNS TXT record for the domain. The workspace stays PENDING
                (no workspace yet, only a row in pending_signups) until the record
                checks out; then it's made and the founder sets a password.

Either way the domain is verified for the new workspace straight away, the
founder becomes its admin and the owner of its billing, and, with BILLING=on,
its trial starts (kb/services/billing.py).
"""

import logging
import os
import re
import secrets
import time

from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

import auth
import kb.audit
import mailer
import members_store
import onboarding_store
import sessions_store
import sso
import sso_errors
import sso_setup
from kb.services import billing, m365
from kb.services.sign_in import ProviderFailed, code_for, login_error, provider_answer
from kb.settings import SESSION_SECONDS

log = logging.getLogger("uvicorn.error")

SIGNUP_KEY = "signup"
SIGNUP_SECONDS = 15 * 60
METHODS = ("google", "microsoft365", "email")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[a-z0-9.-]{1,253}\.[a-z]{2,63}$")
SIGNUPS_PER_HOUR = 3               # email sign-ups started for one address
# Email providers anyone can get an address at: they prove nothing about a company.
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "pm.me", "gmx.com", "gmx.de", "web.de", "mail.com", "yandex.com",
    "yandex.ru", "zoho.com", "qq.com", "163.com", "rediffmail.com", "fastmail.com",
    "tutanota.com", "hey.com"}


def _on():
    return os.getenv("WORKSPACE_SIGNUP", "off").strip().lower() in ("on", "true", "1", "yes")


def signup_methods():
    """The ways a new company can create a workspace here now (none when sign-up is off)."""
    if not _on():
        return []
    wanted = {m.strip().lower() for m in os.getenv("WORKSPACE_SIGNUP_METHODS", "").split(",")
              if m.strip()}
    ready = {"google": "google" in sso.configured(), "microsoft365": m365.configured(),
             "email": auth.password_login_on()}
    return [m for m in METHODS if ready[m] and (not wanted or m in wanted)]


def signup_on():
    """Can a new company create a workspace here?"""
    return bool(signup_methods())


def signup_codes():
    """Invite codes (WORKSPACE_SIGNUP_CODES, comma-separated). When there are any,
    sign-up needs one, and each makes one workspace."""
    return {c.strip() for c in os.getenv("WORKSPACE_SIGNUP_CODES", "").split(",") if c.strip()}


def _create(request, method, name, domain, email, person, code):
    """Make the workspace, or raise WorkspaceTaken. Returns its slug."""
    codes = signup_codes()
    workspace = members_store.create_workspace(name, domain, email, person,
                                               code=code if codes else None)
    billing.founded(workspace, email)
    log.warning("Sign-up: %s created the workspace %s for %s (%s)", email, workspace, domain,
                method)
    kb.audit.record(request, workspace, "workspace.create", email, "workspace", workspace,
                    {"name": name, "domain": domain, "method": method,
                     "invite_code": bool(code and codes)})
    return workspace


def _finish_with_provider(request, method, name, code, email, person, domain):
    """A provider proved the founder owns `domain`: create the workspace and sign them in."""
    codes = signup_codes()
    if codes and code not in codes:          # removed from the list meanwhile
        return login_error("invite", tab="signup")
    try:
        workspace = _create(request, method, name, domain, email, person, code)
    except members_store.WorkspaceTaken as e:
        return login_error(e.reason, tab="signup")
    except Exception:
        ref = sso_errors.new_ref()
        log.exception("Sign-up: couldn't create the workspace, reference %s", ref)
        return login_error("sso_failed", ref, tab="signup")
    members_store.log_event(workspace, method, email, "signed_in")
    user = {"username": email, "name": person, "provider": method,
            "org": domain if method == "google" else None,
            "workspace": workspace, "role": "admin"}
    return RedirectResponse("/#sso?welcome=1&code=" + code_for(request, user))


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
    return _finish_with_provider(request, "google", name, code, email, person, org)


# ---- Microsoft 365 -----------------------------------------------------------------------
async def m365_claims(c, request):
    """Microsoft sent the browser back: what Graph says about the person (claims for
    sso.identity), or ProviderFailed."""
    error = request.query_params.get("error")
    if error:
        detail = f"{error}: {request.query_params.get('error_description', '')}".rstrip(": ")
        log.warning("Microsoft 365: provider returned %s", detail)
        raise ProviderFailed("cancelled" if error == "access_denied" else "sso_failed",
                             sso_errors.from_provider_error(error), detail)
    try:
        token = await c.authorize_access_token(request)
        return await run_in_threadpool(m365.claims_for, token["access_token"])
    except Exception as e:
        log.warning("Microsoft 365: sign-in callback failed: %s: %s", type(e).__name__, e)
        raise ProviderFailed("sso_failed", "token_exchange", f"{type(e).__name__}: {e}") from None


async def finish_m365_signup(c, request, name, code=None):
    try:
        claims = await m365_claims(c, request)
        email, person, _ = sso.identity(m365.PROVIDER, claims)
    except ProviderFailed as e:
        ref = sso_errors.new_ref()
        log.warning("Sign-up: Microsoft 365 sign-in failed (%s), reference %s", e.code, ref)
        return login_error(e.error, ref, tab="signup")
    except sso.NotAllowed as e:    # a guest, or not on a domain the tenant verified
        return login_error(e.reason, tab="signup")
    return _finish_with_provider(request, m365.PROVIDER, name, code, email, person,
                                 email.rpartition("@")[2])


# ---- Email and DNS -----------------------------------------------------------------------
class SignupRefused(HTTPException):
    """A step of the email sign-up can't go on: `code` for tests and logs, the
    message for the founder."""

    def __init__(self, status, code, message):
        super().__init__(status, message)
        self.code = code


def start_email_signup(name, email, code, link_base):
    """Step 1: a pending sign-up, and the confirmation link emailed to the founder.
    Returns the pending row. Raises SignupRefused."""
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise SignupRefused(400, "email", "Enter your work email address.")
    domain = email.rpartition("@")[2]
    if domain in PERSONAL_DOMAINS:
        raise SignupRefused(400, "personal_email", "Use your work email: a personal address "
                                                   "can't create a company's workspace.")
    try:
        domain = sso_setup.normalize_domain(domain)
    except sso_setup.SetupError:
        raise SignupRefused(400, "email", "Enter your work email address.") from None
    if members_store.workspace_for_domain(domain):
        raise SignupRefused(409, "workspace_exists", "Your company already has a workspace "
                                                     "here. Sign in instead.")
    if members_store.get(email):
        raise SignupRefused(409, "account_exists", "That account already belongs to a "
                                                   "workspace. Sign in instead.")
    codes = signup_codes()
    if codes and (code not in codes or members_store.signup_code_used(code)):
        raise SignupRefused(400, "invite", "That invite code isn't valid, or it has already "
                                           "been used.")
    if onboarding_store.recent_signups(email, time.time() - 3600) >= SIGNUPS_PER_HOUR:
        raise SignupRefused(429, "too_many", "We've sent several links to this address in the "
                                             "last hour. Use the newest one, or wait a while.")
    row, link = onboarding_store.new_signup(name, email, domain,
                                            members_store._code_hash(code) if codes else None)
    try:
        mailer.send(email, "signup_verify", workspace_name=name, domain=domain,
                    link=f"{link_base}/signup/verify?token={link}",
                    expires=time.strftime("%d %B %Y, %H:%M UTC", time.gmtime(row["expires_at"])))
    except mailer.MailError as e:
        log.warning("Sign-up: couldn't email %s: %s", email, e)
        raise SignupRefused(503, "mail_failed", "We couldn't send the confirmation email. "
                                                "Please try again later.") from None
    return row


def pending(link):
    """The pending sign-up of an emailed link, or SignupRefused."""
    row = onboarding_store.signup_by_link(link or "")
    if not row or row["workspace"]:
        raise SignupRefused(404, "expired", "This link has expired or was already used. "
                                            "Start again to get a new one.")
    return row


def pending_view(row):
    name, value = members_store.txt_record({"domain": row["domain"],
                                            "verify_token": row["verify_token"]})
    return {"workspace_name": row["workspace_name"], "email": row["email"],
            "domain": row["domain"], "txt_name": name, "txt_value": value,
            "expires_at": row["expires_at"]}


def confirm_email(link):
    """Step 2: the founder opened the link, so the address is theirs."""
    row = pending(link)
    onboarding_store.confirm_email(row["id"])
    return pending_view(row)


def complete_email_signup(request, link, password):
    """Step 3: the DNS record checks out: make the workspace, give the founder their
    password and a session. Returns {"token", "user"}. Raises SignupRefused."""
    row = pending(link)
    if not row["email_verified_at"]:
        raise SignupRefused(400, "email", "Open the link in the email first.")
    if len(password or "") < 8:
        raise SignupRefused(400, "password", "Choose a password of at least 8 characters.")
    view = pending_view(row)
    if view["txt_value"] not in sso_setup.txt_values(view["txt_name"]):
        raise SignupRefused(400, "dns", f"No TXT record {view['txt_name']} with the value "
                                        f"{view['txt_value']} yet. DNS changes can take a few "
                                        "minutes to appear.")
    codes = signup_codes()
    code = None
    if row["code_hash"]:
        code = next((c for c in codes if members_store._code_hash(c) == row["code_hash"]), None)
        if not code:
            raise SignupRefused(400, "invite", "That invite code is no longer valid.")
    person = row["email"].split("@")[0]
    try:
        workspace = _create(request, "email", row["workspace_name"], row["domain"],
                            row["email"], person, code)
    except members_store.WorkspaceTaken as e:
        messages = {"workspace_exists": "Your company already has a workspace here.",
                    "account_exists": "That account already belongs to a workspace.",
                    "invite": "That invite code has already been used."}
        raise SignupRefused(409, e.reason, messages.get(e.reason, "Couldn't create it.")) \
            from None
    members_store.set_password(row["email"], person, auth.hash_password(password), workspace)
    members_store.touch_login(row["email"])
    onboarding_store.finish_signup(row["id"], workspace)
    members_store.log_event(workspace, "password", row["email"], "signed_in")
    user = {"username": row["email"], "name": person, "workspace": workspace, "role": "admin"}
    token = secrets.token_urlsafe(32)
    sessions_store.create(token, user, SESSION_SECONDS)
    return {"token": token, "user": user}
