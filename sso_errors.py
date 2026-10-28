"""
sso_errors.py  —  WHY a sign-in through a workspace's provider didn't work.

Every failure and refusal gets a stable code. The person signing in only sees
a short, general message and a reference (K7Q2-9XMA); the workspace's admin
sees the same reference in Recent sign-ins with the code's reason and how to
fix it. Only the code is stored: never text the provider (or whoever made the
link) wrote, which could say anything.

A provider whose recent real sign-ins all failed is "failing" (health()), so
the admin screen can say so on the provider before anyone reports it.
"""

import base64
import re
import secrets

import httpx
from authlib.integrations.base_client import OAuthError
from joserfc.errors import JoseError
from saml2 import samlp
from saml2.response import IncorrectlySigned, StatusError, UnsolicitedResponse, VerificationError
from saml2.sigver import SignatureError, SigverError
from saml2.validate import ResponseLifetimeExceed, ToEarly

import url_guard

# Authlib uses httpx2 instead of httpx when it's installed (langsmith brings it).
try:
    import httpx2
    _HTTP_MODULES = (httpx, httpx2)
except ImportError:                     # pragma: no cover
    _HTTP_MODULES = (httpx,)
_STATUS_ERRORS = tuple(m.HTTPStatusError for m in _HTTP_MODULES)
_HTTP_ERRORS = tuple(m.HTTPError for m in _HTTP_MODULES)

# code: (reason, fix, counts against the provider's health)
# A code that doesn't count is one the person signing in caused (they cancelled,
# or used the back button), so it says nothing about the provider's settings.
ERRORS = {
    # ---- OpenID Connect ----
    "invalid_client": (
        "Client ID or secret is wrong",
        "Copy the client ID and secret from your provider's app again and save them here "
        "(Edit). A secret that expired at the provider needs a new one.", True),
    "invalid_grant": (
        "Redirect URI doesn't match what's registered, or the sign-in took too long",
        "Check the redirect URI registered at your provider is exactly the one under Setup "
        "details, then try again.", True),
    "unauthorized_client": (
        "The provider's app isn't allowed to sign people in this way",
        "Turn on the authorization code flow (\"standard flow\") for the app at your "
        "provider.", True),
    "invalid_scope": (
        "The provider doesn't allow the openid, email and profile scopes for this app",
        "Allow those scopes for the app at your provider.", True),
    "consent_required": (
        "The provider needs an admin to approve this app first",
        "Grant consent for the app at your provider (Entra ID: \"Grant admin consent\").",
        True),
    "access_denied": (
        "Cancelled at the provider",
        "Nothing to fix if they cancelled. If they didn't, check they're assigned to the app "
        "at your provider.", False),
    "provider_unreachable": (
        "Couldn't reach the provider",
        "Check your provider is up and its issuer URL is right, then try again in a minute.",
        True),
    "token_invalid": (
        "The provider's answer couldn't be verified (issuer, audience or signature)",
        "Check the issuer URL and client ID here match your provider's app exactly.", True),
    "state_mismatch": (
        "The sign-in was finished in another browser or tab, or took too long",
        "Nothing to fix: they should start again from the sign-in page.", False),
    "provider_error": (
        "The provider refused the sign-in",
        "Look for the attempt in your provider's sign-in logs.", True),
    "settings_unreadable": (
        "This provider's saved settings can't be read (the server's encryption key changed)",
        "Edit the provider and enter its client secret again.", True),
    # ---- SAML ----
    "saml_signature": (
        "The provider's signing certificate changed — refresh its metadata",
        "Refresh its metadata, or paste the new certificate (Edit).", True),
    "saml_unsigned": (
        "Neither the response nor the assertion is signed",
        "Make your provider sign the assertion or the response.", True),
    "saml_unsolicited": (
        "Sign-in started at the provider's app launcher; start from our sign-in page",
        "People should start from our sign-in page. Hide the app's tile in your provider's "
        "launcher, or make it a bookmark to our sign-in page.", True),
    "saml_unknown_request": (
        "The answer was to a sign-in that expired, was already finished, or was started "
        "for another provider",
        "Nothing to fix if it happens now and then: they should start again.", False),
    "saml_expired": (
        "Clocks are more than 2 minutes apart, or the answer took too long",
        "Check your provider's clock, and that its answers are valid for a few minutes.",
        True),
    "saml_audience": (
        "The answer was meant for another app (audience or destination doesn't match)",
        "Check your provider has this app's entity ID and ACS URL exactly as under Setup "
        "details.", True),
    "saml_issuer": (
        "The answer came from a different identity provider than the one set up here",
        "Refresh the provider's metadata, or check its entity ID (Edit).", True),
    "saml_no_email": (
        "The answer had no email address",
        "Make your provider send the email as the NameID or an attribute, and set Email "
        "attribute if it has another name (Edit).", True),
    "saml_refused": (
        "The provider refused the sign-in (for example, they aren't assigned to the app)",
        "Check they're assigned to the app at your provider, and look in its sign-in logs.",
        True),
    "saml_replayed": (
        "The same answer was sent twice (the back button, or a replay)",
        "Nothing to fix: they should start again from the sign-in page.", False),
    "saml_invalid": (
        "The provider's answer couldn't be read",
        "Run Test sign-in to see what the provider sends.", True),
    # ---- Who may sign in (the provider worked) ----
    "unverified": (
        "The provider didn't send a verified email address",
        "Make the provider send a verified email (OpenID Connect: the email claim; SAML: "
        "the NameID or an email attribute).", False),
    "not_allowed": (
        "Their email isn't on a verified domain of this workspace, or the account belongs "
        "to another workspace",
        "Verify their email's domain, or ask support about the other workspace.", False),
    "not_workspace": (
        "They used a personal or guest account, not a work one",
        "They should sign in with their work account.", False),
    "workspace_closed": (
        "The workspace is suspended, or being deleted (only admins may sign in)",
        "Cancel the deletion under Access > Data, or ask support about a suspension.", False),
    "disabled": (
        "Their access has been turned off (Members)",
        "Turn their access back on under Members.", False),
    # The joining policy (join_store.py, Access > Sign-in)
    "invite_only": (
        "New people join by invite only, and they had no invite",
        "Invite them under Members, provision them with SCIM, or change the joining policy.",
        False),
    "join_requested": (
        "Asked to join: their request waits for an admin",
        "Approve or deny it under Members > Pending requests.", False),
    "join_denied": (
        "An admin denied their request to join recently, so they can't ask again yet",
        "Invite them under Members if they should be let in after all.", False),
    "sso_required": (
        "Single sign-on is required, so their password can't be used",
        "They should sign in with single sign-on.", False),
    # ---- Warnings (members_store.py) ----
    "last_admin_kept": (
        "Their admin comes from groups the role source hasn't sent in a while, but "
        "they're the only admin, so they keep it",
        "Make someone else admin (Members), or have them sign in through the role source.",
        False),
    # ---- What your identity provider did through SCIM (kb/routers/scim.py) ----
    "scim_user_created": ("Provisioned", None, False),
    "scim_user_updated": ("Profile updated", None, False),
    "scim_user_deactivated": ("Deactivated, and signed out everywhere", None, False),
    "scim_user_reactivated": ("Reactivated", None, False),
    "scim_user_deleted": (
        "Deleted: access off now, details anonymised after the retention window", None, False),
    "scim_user_anonymised": ("Anonymised after the retention window", None, False),
    "scim_group_created": ("Group pushed", None, False),
    "scim_group_updated": ("Group or its members changed", None, False),
    "scim_group_deleted": ("Group removed", None, False),
    "scim_domain": (
        "Refused: the email isn't on a verified domain, and wasn't invited",
        "Verify the domain under Email domains, or invite the address first.", False),
    "scim_taken": (
        "Refused: the email, userName or group name belongs to someone or something else",
        "Check for a duplicate at your identity provider, or an account in another workspace.",
        False),
    "scim_exists": (
        "Refused: already provisioned",
        "Your identity provider should match the existing user (by userName) instead of "
        "creating it again.", False),
    "scim_no_email": (
        "Refused: the user has no email address",
        "Map an email to the user's emails (or userName) at your identity provider.", False),
    "scim_invalid": ("Refused: the request was missing something", None, False),
    "scim_last_admin": (
        "Refused: it would leave the workspace with no admin",
        "Make someone else admin (Members, or an admin group) first.", False),
    # ---- Anything else ----
    "sso_failed": (
        "Something went wrong on this server",
        "Look for the reference in the server log.", True),
}

# The error codes OpenID Connect providers send back (RFC 6749 §4.1.2.1, OIDC
# Core §3.1.2.6), as codes above. Any other one is "provider_error".
_PROVIDER_ERRORS = {
    "access_denied": "access_denied",
    "unauthorized_client": "unauthorized_client",
    "invalid_scope": "invalid_scope",
    "consent_required": "consent_required",
    "interaction_required": "consent_required",
    "server_error": "provider_unreachable",
    "temporarily_unavailable": "provider_unreachable",
}
# What the token endpoint can say (RFC 6749 §5.2).
_TOKEN_ERRORS = {
    "invalid_client": "invalid_client",
    "unauthorized_client": "unauthorized_client",
    "invalid_grant": "invalid_grant",
    "invalid_scope": "invalid_scope",
    "mismatching_state": "state_mismatch",
}

_REF_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"    # no 0/O, 1/I/L


def describe(code):
    """{"reason", "fix"} for a code, or None for one that isn't known (an event
    logged before codes, for example)."""
    if code not in ERRORS:
        return None
    reason, fix, _ = ERRORS[code]
    return {"reason": reason, "fix": fix}


def new_ref():
    """A short reference for one failed sign-in, shown to the person and the admin."""
    ref = "".join(secrets.choice(_REF_LETTERS) for _ in range(8))
    return f"{ref[:4]}-{ref[4:]}"


# ---- Which code -----------------------------------------------------------------
def from_provider_error(error):
    """The code for an `error` an OpenID Connect provider sent the browser back with."""
    return _PROVIDER_ERRORS.get(error, "provider_error")


def from_oidc_exception(e):
    """The code for what went wrong exchanging the provider's answer for a
    verified ID token (Authlib, httpx and url_guard errors)."""
    if isinstance(e, OAuthError):           # the token endpoint said no, or the state
        # Keycloak answers a wrong client secret with unauthorized_client ("Invalid
        # client or Invalid client credentials") where the RFC says invalid_client.
        if e.error == "unauthorized_client" and re.search(
                r"invalid client|credential", e.description or "", re.I):
            return "invalid_client"
        return _TOKEN_ERRORS.get(e.error, "provider_error")
    if isinstance(e, JoseError):            # the ID token's signature, issuer, audience, ...
        return "token_invalid"
    if isinstance(e, _STATUS_ERRORS):
        status = e.response.status_code
        return ("invalid_client" if status == 401 else
                "provider_unreachable" if status >= 500 else "provider_error")
    if isinstance(e, _HTTP_ERRORS + (url_guard.UnsafeURL,)):    # discovery, JWKS, token
        return "provider_unreachable"
    return "sso_failed"


def from_saml_exception(e, saml_response=""):
    """The code for why pysaml2 refused an IdP's answer."""
    text = str(e)
    if isinstance(e, (SignatureError, IncorrectlySigned)):
        return "saml_signature"
    if isinstance(e, SigverError):
        return "saml_unsigned" if "signed" in text else "saml_signature"
    if isinstance(e, UnsolicitedResponse):
        # No InResponseTo at all: the IdP started it (its app launcher).
        return "saml_unknown_request" if _in_response_to(saml_response) else "saml_unsolicited"
    if isinstance(e, StatusError):
        return "saml_refused"
    if isinstance(e, (ResponseLifetimeExceed, ToEarly)):
        return "saml_expired"
    if isinstance(e, VerificationError):
        if "Condition" in text or "subject confirmation" in text:
            return "saml_expired"
        if "recipient" in text:
            return "saml_audience"
    if "AudienceRestrictions" in text:
        return "saml_audience"
    return "saml_invalid"


def _in_response_to(saml_response):
    """The InResponseTo of an answer (base64 XML), or None."""
    try:
        return samlp.response_from_string(base64.b64decode(saml_response)).in_response_to
    except Exception:
        return None


# ---- Provider health ---------------------------------------------------------------
def health(events):
    """How a provider is doing, from its events (newest first: dicts with
    "outcome", "reason", "at"). Failing: its latest real sign-ins failed, with
    nothing working since. A refusal counts as working (the provider answered;
    the person just isn't let in), and so does a passing Test sign-in, so an admin
    who fixed the settings sees it straight away. Failures the person caused, and
    failed tests, are skipped."""
    failures = []
    for e in events:
        if e["outcome"] in ("signed_in", "refused") or (e["outcome"] == "test"
                                                        and not e["reason"]):
            break
        if e["outcome"] == "failed" and ERRORS.get(e["reason"], (0, 0, True))[2]:
            failures.append(e)
    if not failures:
        return {"status": "ok"}
    code = failures[0]["reason"]
    return {"status": "failing", "since": failures[-1]["at"], "count": len(failures),
            "code": code, **(describe(code) or {"reason": code, "fix": None})}
