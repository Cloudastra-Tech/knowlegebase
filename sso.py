"""
sso.py  —  SINGLE SIGN-ON: "Continue with Google / Microsoft / Okta / ...".

The provider (Google Workspace, Microsoft Entra ID, or any OpenID Connect
provider such as Okta, Keycloak or Auth0) proves WHO someone is.
This file decides WHAT they get: which workspace (company) and which role.

That decision comes from members_store.py (the database; manage it with
`python auth.py`):

  * an email on a domain a workspace has VERIFIED signs in to that workspace
  * their role is the one on their member row: public unless someone set it
    (`python auth.py role priya@cloudastra.in hr`); new people start as public
  * anyone else is turned away — having an account at the provider is not enough
  * it's checked again on every request (api.py), so changes apply straight away

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

A workspace's OWN provider ("connection", set up by its admin, stored in
members_store.py) only signs people in to that workspace, and only with an
email on a domain that workspace has verified. The login page finds it from
the email someone types (connections_for_email). If the admin gave it group
rules, it's the workspace's role source: the groups it sends at sign-in set the
person's role. Other providers sign people in without changing their role (see
the top of members_store.py, also for how that combines with a role set by hand).

Authlib does the OpenID Connect work: the redirect, state + PKCE, and checking
the ID token's signature, audience, issuer and expiry.
"""

import os
import re
import secrets
import threading
import time

from authlib.integrations.starlette_client import OAuth

import lifecycle_store
import members_store
import url_guard

CODE_SECONDS = 60           # the one-time code that hands a new session to the browser

LABELS = {"google": "Google", "microsoft": "Microsoft"}
SCOPES = "openid email profile"
DEFAULT_GROUP_CLAIM = "groups"
_GENERIC_ID = re.compile(r"^[a-z][a-z0-9]{0,19}$")

_oauth = OAuth()
_clients = {}
_connection_clients = {}  # connection id -> (updated_at, Authlib client)
_codes = {}                 # code -> (user, browser, expires)
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


def connection_client(conn):
    """The Authlib client for a workspace's own provider. Built from the endpoints
    saved with it (no discovery fetch), and every call it makes to the provider
    goes through url_guard. Rebuilt whenever the connection is edited."""
    cached = _connection_clients.get(conn["id"])
    if cached and cached[0] == conn["updated_at"]:
        return cached[1]
    c = OAuth().register(
        name=f"conn_{conn['id']}",
        client_id=conn["client_id"],
        client_secret=(members_store.connection_keys(conn["id"]) or {}).get("client_secret"),
        client_kwargs={"scope": SCOPES, "code_challenge_method": "S256", "timeout": 10,
                       "event_hooks": {"request": [url_guard.httpx_hook], "response": []}},
        **conn["metadata"],
    )
    _connection_clients[conn["id"]] = (conn["updated_at"], c)
    return c


def connections_for_email(email):
    """The enabled providers of the workspace that verified this email's domain."""
    workspace = members_store.workspace_for_domain(email.rpartition("@")[2])
    return members_store.list_connections(workspace, enabled_only=True) if workspace else []


# ---- Who gets in -------------------------------------------------------------
def identity(provider, claims):
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


def _workspace_for(email, org):
    """The workspace that verified this email's domain, or None. A Google sign-in
    must also come from a Google Workspace whose primary domain (org) that
    workspace verified, so another organisation's Workspace can't vouch for it."""
    workspace = members_store.workspace_for_domain(email.rpartition("@")[2])
    if workspace and org is not None and org not in members_store.verified_domains(workspace):
        return None
    return workspace


def access_for(email, org=None, connection=None):
    """{"workspace", "role"} for someone signed in with single sign-on, or None
    if they may no longer use the app. Checked on every request: someone who came
    through a workspace's own provider is also signed out once it's turned off."""
    workspace = _workspace_for(email, org)
    if workspace and connection is not None:
        conn = members_store.get_connection(connection)
        if not conn or not conn["enabled"] or conn["workspace"] != workspace:
            return None
    member = members_store.get(email) if workspace else None
    if not member or member["workspace"] != workspace or member["disabled"]:
        return None
    member = members_store.check_group_admin(member)
    if not lifecycle_store.signs_in(workspace, member["role"]):
        return None                    # suspended, or being deleted (admins only)
    return {"workspace": workspace, "role": member["role"]}


def check_open(workspace, email):
    """NotAllowed("workspace_closed") if `email` may not sign in to the workspace
    now: it's suspended, or being deleted and they aren't one of its admins."""
    member = members_store.get(email)
    role = member["role"] if member and member["workspace"] == workspace else "public"
    if not lifecycle_store.signs_in(workspace, role):
        raise NotAllowed("workspace_closed")


def user_for(provider, claims):
    """The session user for someone the provider just signed in. Adds them as a
    member the first time."""
    email, name, org = identity(provider, claims)
    workspace = _workspace_for(email, org)
    if workspace:
        check_open(workspace, email)
    member = members_store.record_sso_login(email, name, workspace) if workspace else None
    if not member:
        raise NotAllowed("not_allowed")
    if member["disabled"]:
        raise NotAllowed("disabled")
    return {"username": email, "name": name, "provider": provider, "org": org,
            "workspace": workspace, "role": member["role"]}


def check_connection_identity(conn, claims):
    """(email, name) of someone a workspace's own provider vouches for, or
    NotAllowed. The provider belongs to the workspace, so it's trusted for that
    workspace's verified domains and nothing else. An email it marks as
    unverified is refused; one with no email_verified claim at all (Entra ID
    sends none) is accepted, since the domain check already limits it to the
    workspace's own."""
    email = claims.get("email")
    email = email.strip().lower() if isinstance(email, str) else ""
    if str(claims.get("email_verified")).lower() == "false" or "@" not in email:
        raise NotAllowed("unverified")
    if members_store.workspace_for_domain(email.rpartition("@")[2]) != conn["workspace"]:
        raise NotAllowed("not_allowed")
    member = members_store.get(email)
    if member and member["workspace"] != conn["workspace"]:
        raise NotAllowed("not_allowed")       # an account in another workspace
    if member and member["disabled"]:
        raise NotAllowed("disabled")
    return email, claims.get("name") or email.split("@")[0]


def claim_values(claims, name):
    """The values of a claim or SAML attribute, as a list of strings. Names are
    matched without regard to case; one that isn't there whole may be a dotted
    path into nested claims ("realm_access.roles"). Whole names come first, as
    SAML attribute names are often URLs, dots and all."""
    def lookup(container, key):
        if not isinstance(container, dict):
            return None
        if key in container:
            return container[key]
        return next((v for k, v in container.items() if k.lower() == key.lower()), None)

    value = lookup(claims, name)
    if value is None and "." in name:
        value = claims
        for part in name.split("."):
            value = lookup(value, part)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def groups_from(conn, claims):
    """(groups, role they give) under the provider's rules (None: no rules).
    Only kept if the provider is the workspace's role source."""
    groups = claim_values(claims, conn.get("group_claim") or DEFAULT_GROUP_CLAIM)
    rules = members_store.group_rules(conn["id"])
    return groups, members_store.role_for_groups(rules, groups) if rules else None


def try_connection(conn, claims):
    """What WOULD happen if these claims signed in, without signing anyone in
    (the admin's "Test sign-in")."""
    try:
        email, name = check_connection_identity(conn, claims)
    except NotAllowed as e:
        email = claims.get("email")
        email = email.strip().lower() if isinstance(email, str) else ""
        detail = None
        if e.reason == "not_allowed":          # which of the two rules it broke
            domain = email.rpartition("@")[2]
            detail = (f"{domain} isn't a verified domain of this workspace."
                      if members_store.workspace_for_domain(domain) != conn["workspace"]
                      else "This account belongs to another workspace.")
        return {"ok": False, "reason": e.reason, "detail": detail, "email": email or None}
    member = members_store.get(email)
    sets_roles = members_store.role_source(conn["workspace"]) == conn["id"]
    found = groups_from(conn, claims) if sets_roles else None
    override = member["override_role"] if member else None
    # Through another provider, the role their groups gave stays as it is.
    group_role = found[1] if found else member["group_role"] if member else None
    return {"ok": True, "email": email, "name": name,
            "role": override or group_role or "public",
            "role_source": "override" if override else "groups" if group_role else "default",
            "groups": found[0] if found else None, "new_member": member is None,
            "sets_roles": sets_roles}


def user_for_connection(conn, claims):
    """The session user for someone a workspace's own provider just signed in."""
    email, name = check_connection_identity(conn, claims)
    check_open(conn["workspace"], email)
    member = members_store.record_sso_login(email, name, conn["workspace"],
                                            groups=groups_from(conn, claims),
                                            source=conn["id"])
    if not member:
        raise NotAllowed("not_allowed")
    members_store.record_connection_login(conn["id"])
    return {"username": email, "name": name, "provider": conn["protocol"], "connection": conn["id"],
            "org": None, "workspace": conn["workspace"], "role": member["role"]}


# ---- Handing the session to the browser --------------------------------------
# The provider sends the BROWSER back to us, so the result has to travel in a
# URL. Rather than put the session token there (history, logs), we send a short
# one-time code that the site swaps for the session with a POST.
def issue_code(user, browser=None):
    """A one-time code for `user`, redeemable only by the browser that started the
    sign-in (`browser`: the ID in its sign-in cookie; see api.py)."""
    code = secrets.token_urlsafe(24)
    now = time.time()
    with _codes_lock:
        for c in [c for c, (_, _, exp) in _codes.items() if exp < now]:
            del _codes[c]
        _codes[code] = (user, browser, now + CODE_SECONDS)
    return code


def redeem_code(code, browser=None):
    """The user a code was issued for, once; None if unknown, used, expired, or
    redeemed by another browser."""
    with _codes_lock:
        user, issued_to, expires = _codes.pop(code or "", (None, None, 0))
    if expires < time.time() or not secrets.compare_digest(str(issued_to), str(browser)):
        return None
    return user
