"""
Microsoft 365 for any company (sign-up, and signing in to a workspace made with
it): a MULTI-TENANT Entra app, so people from every company's directory can use
it, unlike the server-wide "microsoft" provider (sso.py), which is pinned to one
tenant.

Proof of ownership is the Entra equivalent of Google Workspace's `hd`: Microsoft
Graph's `organization` resource lists the domains the person's tenant has
VERIFIED with Microsoft (a domain can be verified in one tenant at a time), and
their user principal name, which Entra only allows on those domains, must be on
one of them. Guests (userType Guest) are refused.

It asks for the delegated User.Read permission only (enough for /me and
/organization) and takes no ID token: who the person is comes from Graph, over
TLS, with the access token Microsoft just issued to this app. The app needs no
admin consent for User.Read in most tenants.

Settings (.env), and the app registration: deploy.md, "Microsoft 365 sign-up".
    M365_CLIENT_ID / M365_CLIENT_SECRET   the multi-tenant app ("Accounts in any
                                          organizational directory")
    Redirect URI: <PUBLIC_URL>/api/auth/m365/callback
"""

import os

import httpx
from authlib.integrations.starlette_client import OAuth

AUTHORITY = "https://login.microsoftonline.com/organizations/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "User.Read"
PROVIDER = "microsoft365"           # the session's `provider`, and the sign-in events'
TIMEOUT = 10
TRANSPORT = None                    # tests put an httpx.MockTransport here

_oauth = OAuth()
_client = None


class GraphError(Exception):
    """Microsoft Graph didn't answer as expected."""


def configured():
    return bool(os.getenv("M365_CLIENT_ID") and os.getenv("M365_CLIENT_SECRET"))


def client():
    """The Authlib client of the multi-tenant app, or None if it isn't set up."""
    global _client
    if not configured():
        return None
    if _client is None:
        _client = _oauth.register(
            name="m365", client_id=os.environ["M365_CLIENT_ID"],
            client_secret=os.environ["M365_CLIENT_SECRET"],
            authorize_url=f"{AUTHORITY}/authorize", access_token_url=f"{AUTHORITY}/token",
            client_kwargs={"scope": SCOPE, "code_challenge_method": "S256"})
    return _client


def _get(path, token):
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT) as http:
            r = http.get(GRAPH + path, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise GraphError(f"couldn't reach Microsoft Graph ({type(e).__name__})") from None
    if r.status_code != 200:
        raise GraphError(f"Microsoft Graph answered {r.status_code} for {path.split('?')[0]}")
    try:
        return r.json()
    except ValueError:
        raise GraphError("Microsoft Graph didn't answer with JSON") from None


def claims_for(access_token):
    """What Graph says about the person and their tenant, as claims for
    sso.identity(PROVIDER, ...): {"email", "name", "guest", "tenant",
    "tenant_name", "verified_domains"}."""
    me = _get("/me?$select=userPrincipalName,displayName,userType", access_token)
    orgs = _get("/organization?$select=id,displayName,verifiedDomains", access_token)
    org = (orgs.get("value") or [{}])[0]
    return {"email": (me.get("userPrincipalName") or "").strip().lower(),
            "name": me.get("displayName") or None,
            "guest": (me.get("userType") or "").lower() == "guest",
            "tenant": org.get("id"), "tenant_name": org.get("displayName"),
            "verified_domains": sorted({(d.get("name") or "").strip().lower()
                                        for d in org.get("verifiedDomains") or []
                                        if d.get("name")})}
