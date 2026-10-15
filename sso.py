"""
sso.py  —  SINGLE SIGN-ON: "Continue with Google / Microsoft / Okta / ...".

The provider (Google Workspace, Microsoft Entra ID, or any OpenID Connect
provider such as Okta, Keycloak or Auth0) proves WHO someone is.
This file decides WHAT they get: which workspace (company) and which role.

That decision lives in config/sso.json (gitignored, like users.json):

    {
      "workspaces": {
        "default": {
          "domains": ["cloudastra.in", "cloudastra.co"],
          "hr": ["priya@cloudastra.in"]
        }
      }
    }

  * an email on one of a workspace's domains signs in to that workspace
  * emails listed under "hr" get the HR / Admin role; everyone else is public
  * anyone else is turned away — having an account at the provider is not enough
  * it's checked again on every request (api.py), so edits apply straight away

Server settings (.env). A provider only shows on the login page once it's set:
    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET     (the same OAuth client Google Drive uses)
    MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET / MICROSOFT_TENANT_ID
Any other OpenID Connect provider — list its id, then give its settings:
    OIDC_PROVIDERS=okta,keycloak
    OIDC_OKTA_ISSUER=https://dev-123456.okta.com/oauth2/default
    OIDC_OKTA_CLIENT_ID / OIDC_OKTA_CLIENT_SECRET
    OIDC_OKTA_LABEL=Okta                         (button text, optional)
Such a provider must vouch for the email (email_verified). Anyone who can get a
verified address on one of your domains there can sign in, so add only
providers your organisation controls.

Authlib does the OpenID Connect work: the redirect, state + PKCE, and checking
the ID token's signature, audience, issuer and expiry.
"""

import json
import os
import re
import secrets
import threading
import time

from authlib.integrations.starlette_client import OAuth

import workspaces

CONFIG_FILE = os.getenv("SSO_CONFIG", os.path.join("config", "sso.json"))
CODE_SECONDS = 60           # the one-time code that hands a new session to the browser

LABELS = {"google": "Google", "microsoft": "Microsoft"}
SCOPES = "openid email profile"
_GENERIC_ID = re.compile(r"^[a-z][a-z0-9]{0,19}$")

_oauth = OAuth()
_clients = {}
_codes = {}                 # code -> (user, expires)
_codes_lock = threading.Lock()


class NotAllowed(Exception):
    """The provider signed someone in, but this app won't let them in.
    `reason` is a short code the login page turns into a message."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ---- Providers ---------------------------------------------------------------
def configured():
    """The providers whose settings are filled in, in login-page order."""
    out = []
    if os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
        out.append("google")
    if (os.getenv("MICROSOFT_CLIENT_ID") and os.getenv("MICROSOFT_CLIENT_SECRET")
            and os.getenv("MICROSOFT_TENANT_ID")):
        out.append("microsoft")
    for p in os.getenv("OIDC_PROVIDERS", "").split(","):
        p = p.strip().lower()
        if (_GENERIC_ID.match(p) and p not in LABELS and p not in out
                and all(_setting(p, k) for k in ("ISSUER", "CLIENT_ID", "CLIENT_SECRET"))):
            out.append(p)
    return out


def _setting(provider, key):
    """GOOGLE_CLIENT_ID for a built-in provider, OIDC_OKTA_CLIENT_ID for others."""
    prefix = provider.upper() if provider in LABELS else f"OIDC_{provider.upper()}"
    return os.getenv(f"{prefix}_{key}", "")


def label(provider):
    """The text on the provider's sign-in button."""
    return LABELS.get(provider) or _setting(provider, "LABEL") or provider.capitalize()


def authorize_params(provider):
    """Extra parameters for the sign-in redirect. Google and Microsoft always show
    their account chooser, so someone signed in to a personal account can pick
    their work one; other providers may not support `prompt=select_account`."""
    return {"prompt": "select_account"} if provider in LABELS else {}


def _metadata_url(provider):
    if provider not in LABELS:
        return _setting(provider, "ISSUER").rstrip("/") + "/.well-known/openid-configuration"
    if provider == "google":
        return "https://accounts.google.com/.well-known/openid-configuration"
    # The TENANT-specific endpoint, not /common: its issuer names our tenant, so
    # Authlib's issuer check only accepts tokens from our own directory.
    tenant = os.environ["MICROSOFT_TENANT_ID"]
    return f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"


def client(provider):
    """The Authlib client for a provider, or None if it isn't set up."""
    if provider not in configured():
        return None
    if provider not in _clients:
        _clients[provider] = _oauth.register(
            name=provider,
            server_metadata_url=_metadata_url(provider),
            client_id=_setting(provider, "CLIENT_ID"),
            client_secret=_setting(provider, "CLIENT_SECRET"),
            client_kwargs={"scope": SCOPES, "code_challenge_method": "S256"},
        )
    return _clients[provider]


# ---- Who gets in -------------------------------------------------------------
def load_config():
    """config/sso.json, or {} if there is none. Raises ValueError if it isn't valid JSON."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _identity(provider, claims):
    """(email, name, org) from a VERIFIED ID token. `org` is the Google Workspace
    the account belongs to (None for other providers). Refuses addresses the
    provider doesn't vouch for."""
    org = None
    if provider == "google":
        email = (claims.get("email") or "").strip().lower()
        # A personal Google account can carry any address, so require a verified
        # address on a Workspace account (hd = that Workspace's primary domain).
        if not claims.get("email_verified"):
            raise NotAllowed("unverified")
        if not claims.get("hd"):
            raise NotAllowed("not_workspace")
        org = claims["hd"].strip().lower()
    elif provider == "microsoft":
        # Guests and personal Microsoft accounts carry an `idp` naming their own
        # provider; members of our tenant (pinned by the issuer check) don't.
        if claims.get("idp") and claims["idp"] != claims.get("iss"):
            raise NotAllowed("not_workspace")
        # preferred_username is the UPN, which Entra only allows on the tenant's
        # verified domains. The `email` claim isn't verified, so it's never used.
        email = (claims.get("preferred_username") or "").strip().lower()
    else:
        # Any other OpenID Connect provider: only an address it has verified.
        if str(claims.get("email_verified")).lower() != "true":
            raise NotAllowed("unverified")
        email = (claims.get("email") or "").strip().lower()
    if "@" not in email:
        raise NotAllowed("unverified")
    return email, (claims.get("name") or email.split("@")[0]), org


def access_for(email, org=None, config=None):
    """{"workspace", "role"} for an email, or None if no workspace takes it.
    A Google sign-in must also come from a Workspace whose primary domain (org)
    is one of that workspace's domains."""
    config = load_config() if config is None else config
    domain = email.rpartition("@")[2]
    for slug, ws in (config.get("workspaces") or {}).items():
        if not workspaces.valid(slug):
            continue
        domains = {d.strip().lower() for d in ws.get("domains", [])}
        if domain in domains:
            if org is not None and org not in domains:
                return None
            hr = {e.strip().lower() for e in ws.get("hr", [])}
            return {"workspace": slug, "role": "hr" if email in hr else "public"}
    return None


def user_for(provider, claims):
    """The session user for someone the provider just signed in."""
    email, name, org = _identity(provider, claims)
    access = access_for(email, org)
    if not access:
        raise NotAllowed("not_allowed")
    return {"username": email, "name": name, "provider": provider, "org": org, **access}


# ---- Handing the session to the browser --------------------------------------
# The provider sends the BROWSER back to us, so the result has to travel in a
# URL. Rather than put the session token there (history, logs), we send a short
# one-time code that the site swaps for the session with a POST.
def issue_code(user):
    code = secrets.token_urlsafe(24)
    now = time.time()
    with _codes_lock:
        for c in [c for c, (_, exp) in _codes.items() if exp < now]:
            del _codes[c]
        _codes[code] = (user, now + CODE_SECONDS)
    return code


def redeem_code(code):
    """The user a code was issued for, once; None if unknown, used or expired."""
    with _codes_lock:
        user, expires = _codes.pop(code or "", (None, 0))
    return user if expires > time.time() else None
