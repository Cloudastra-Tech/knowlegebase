"""
connectors/atlassian.py  —  what Confluence Cloud and Jira Cloud share: one
Atlassian OAuth 2.0 (3LO) app, its token rotation and its API gateway.

One app (ATLASSIAN_CLIENT_ID / ATLASSIAN_CLIENT_SECRET, created at
developer.atlassian.com/console/myapp) serves both products. Each connection
asks only for its own product's scopes when the admin consents, and keeps its
own tokens. An app has ONE callback URL (Atlassian staff:
https://community.developer.atlassian.com/t/multiple-callback-urls-for-a-single-outh-2-0-3lo-app/32413;
the docs say redirect_uri "must match" it), so Jira connects through
Confluence's: <PUBLIC_URL>/api/integrations/confluence/callback.

    authorize_url(scopes, redirect_uri, state)   Atlassian's consent screen
    exchange(code, redirect_uri)                 the code -> tokens
    first_site(access_token, product)            the first site the token reaches
    Session(provider)                            tokens kept per connection:
        .access_token(ws)   refreshed under a lock; the ROTATED refresh token
                            is saved before the new access token is used
        .get(ws, url)       one API call: a 401 refreshes once, "scope does not
                            match" is MissingScope, a near-limit answer slows
                            the token's next calls down

Facts (checked 2026-10-27):
  OAuth 2.0 (3LO)   https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/
                    https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
      authorize: audience=api.atlassian.com, client_id, scope (space separated,
      scopes "from multiple products" allowed), redirect_uri, state,
      response_type=code, prompt=consent; offline_access for a refresh token.
      Refresh tokens ROTATE on every use (90 days inactivity, 10 minute reuse
      interval). Calls go to https://api.atlassian.com/ex/{jira|confluence}/{cloudid}/...
      accessible-resources: [{id (cloudid), name, url, scopes, avatarUrl}].
  rate limits       https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
                    https://developer.atlassian.com/cloud/confluence/rate-limiting/
      429 + Retry-After (or X-RateLimit-Reset), X-RateLimit-NearLimit: true when
      less than 20 % of the quota is left; the points quota is per app.
"""

import os
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import AuthExpired, ConfigError, ConnectRefused, MissingScope

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
GATEWAY = "https://api.atlassian.com/ex"         # + /{product}/{cloudid}/...
CALLBACK = "confluence"                          # the app's one callback URL's provider
SETUP_ENV = "ATLASSIAN_CLIENT_ID / ATLASSIAN_CLIENT_SECRET"

RATE = 5                              # calls a second per token
NEAR_LIMIT_PAUSE = 2.0                # seconds a near-limit answer holds the next call back


def configured():
    return bool(os.getenv("ATLASSIAN_CLIENT_ID") and os.getenv("ATLASSIAN_CLIENT_SECRET"))


def authorize_url(scopes, redirect_uri, state):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "audience": "api.atlassian.com",
        "client_id": os.environ["ATLASSIAN_CLIENT_ID"],
        "scope": scopes,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    })


def token_call(body):
    """POST /oauth/token. A refused refresh is 403 invalid_grant (Atlassian's
    docs), a refused code 400 / 401."""
    return http.request("POST", TOKEN_URL, retries=1, ok=(400, 401, 403), json={
        **body, "client_id": os.environ.get("ATLASSIAN_CLIENT_ID", ""),
        "client_secret": os.environ.get("ATLASSIAN_CLIENT_SECRET", "")})


def exchange(code, redirect_uri):
    """The callback's code -> {access_token, refresh_token, expires_at}."""
    r = token_call({"grant_type": "authorization_code", "code": code,
                    "redirect_uri": redirect_uri})
    if r.status_code >= 400:
        raise ConfigError(f"Atlassian refused the code (HTTP {r.status_code})")
    t = r.json()
    if not t.get("refresh_token"):
        raise ConfigError("Atlassian did not return a refresh token")
    return {"access_token": t["access_token"], "refresh_token": t["refresh_token"],
            "expires_at": time.time() + int(t.get("expires_in", 3600)) - 60}


def first_site(access_token, product):
    """The first site the token reaches with a scope of `product` ("confluence",
    "jira"): {"id", "name", "url", ...}. A token can reach several sites, and
    the other product's ones."""
    sites = http.request("GET", RESOURCES_URL, retries=1, headers={
        "Authorization": f"Bearer {access_token}", "Accept": "application/json"}).json()
    site = next((x for x in sites if any(f":{product}" in sc or f"{product}-" in sc
                                         for sc in x.get("scopes") or [])), None)
    if not site:
        raise ConnectRefused("no_site", f"no {product} site in accessible-resources")
    return site


def account_label(site):
    host = urllib.parse.urlsplit(site.get("url") or "").hostname or site.get("name") or "Atlassian"
    return f"{site.get('name') or host} ({host})"


class Session:
    """A Cloud connection's tokens (integrations_store secret: access_token,
    refresh_token, expires_at, cloud_id, site_url) and how to call with them."""

    def __init__(self, provider, label):
        self.provider, self.label = provider, label

    def secret(self, ws):
        sec = store.secret(ws, self.provider)
        if not sec or not sec.get("refresh_token"):
            raise AuthExpired(f"{self.label} is not connected")
        return sec

    def access_token(self, ws, force=False):
        """A valid access token. Refreshing rotates the refresh token, so it runs
        under a lock and the new one is saved before the access token is used."""
        with http.refresh_lock(f"{self.provider}:{ws}"):
            sec = self.secret(ws)
            if not force and sec.get("access_token") and sec.get("expires_at", 0) > time.time():
                return sec["access_token"]
            r = token_call({"grant_type": "refresh_token", "refresh_token": sec["refresh_token"]})
            if r.status_code >= 400:          # invalid_grant: revoked, expired, or rotated away
                raise AuthExpired("Atlassian refused the refresh token")
            t = r.json()
            sec.update(access_token=t["access_token"],
                       refresh_token=t.get("refresh_token") or sec["refresh_token"],
                       expires_at=time.time() + int(t.get("expires_in", 3600)) - 60)
            store.update(ws, self.provider, secret=sec)
            return sec["access_token"]

    def get(self, ws, url, params=None, ok=(), **kwargs):
        """One call; a 401 refreshes the token once. A 401 that says "scope does not
        match" is a missing scope, not an expired token (Atlassian answers so when
        the app wasn't granted what the endpoint needs; reported in
        https://jira.atlassian.com/browse/CONFCLOUD-75668, not in the reference)."""
        bucket = http.bucket_key(self.provider, ws)
        for attempt in (0, 1):
            token = self.access_token(ws, force=attempt == 1)
            r = http.request("GET", url, params=params, ok=(401, *ok), bucket=bucket, rate=RATE,
                             burst=RATE, headers={"Authorization": f"Bearer {token}",
                                                  "Accept": "application/json"}, **kwargs)
            if r.status_code == 401:
                if "scope does not match" in (r.text or "").lower():
                    raise MissingScope(f"GET {http.safe_url(url)}: scope does not match")
                if attempt == 1:
                    raise AuthExpired(f"GET {http.safe_url(url)}: HTTP 401")
                continue
            if (r.headers.get("X-RateLimit-NearLimit") or "").lower() == "true":
                http.get_bucket(bucket, RATE, RATE).pause(NEAR_LIMIT_PAUSE)
            return r
