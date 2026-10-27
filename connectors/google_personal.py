"""
connectors/google_personal.py  —  what Gmail and Google Calendar share: a member's
OWN Google connection (a PERSONAL connector, connectors/base.py is_personal).

Each member connects their own account from My connections
(kb/routers/my_integrations.py); the connection's key is
registry.personal_key(provider, username), and its settings["owner"] is that
username: everything it syncs is for them alone (kb/services/permissions.py
personal_acl), never by a policy, never the workspace's. Tokens are kept per
key, so one member's token is never used for another's mailbox.

OAuth: the server's Google OAuth client (GOOGLE_CLIENT_ID / SECRET, the one
Drive uses) with each connector's own read-only scope and its own redirect URI
<PUBLIC_URL>/api/integrations/<provider>/callback; offline access for a refresh
token, refreshed when the access token expires (and once on a 401). A refresh
Google refuses (invalid_grant: revoked, or the user's password changed) waits
for the member to reconnect.

Google's review: gmail.readonly is a RESTRICTED scope: an app that stores or
sends Gmail data through a server needs Google's restricted-scope verification
and an annual CASA security assessment before it may be used by anyone outside
the Google Cloud project's own organisation (unverified, it is limited to 100
test users and shows the "unverified app" screen). calendar.readonly is a
SENSITIVE scope: verification, no assessment. Both are off until the operator
turns them on (GMAIL_SYNC / CALENDAR_SYNC = on), because of that review:
  https://developers.google.com/workspace/gmail/api/auth/scopes
  https://developers.google.com/workspace/calendar/api/auth
  https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification
  https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
  https://support.google.com/cloud/answer/7454865 (unverified apps, 100 users)
"""

import logging
import os
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import AuthExpired, ConfigError, Connector
from connectors.google_drive import AUTH_URL, REVOKE_URL, TOKEN_URL, USERINFO_URL

log = logging.getLogger("uvicorn.error")

RATE = 5                               # calls a second per member's token


class GooglePersonal(Connector):
    """A member's own Google connection. Subclasses set PROVIDER, SCOPE, SWITCH."""
    AUTH = "oauth"
    PERSONAL = True
    PICKER = "list"
    PICKER_REQUIRED = True
    SCOPE = ""
    SWITCH = ""                        # the env switch that turns it on (restricted / sensitive)
    SETUP_ENV = "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET"

    def configured(self):
        return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET")
                    and (os.getenv(self.SWITCH) or "").strip().lower() in ("on", "1", "true"))

    # ---- connecting ---------------------------------------------------------------
    def auth_url(self, redirect_uri, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri,
            "response_type": "code", "scope": f"openid email {self.SCOPE}",
            "access_type": "offline", "prompt": "consent", "state": state})

    def finish_connect(self, workspace, code, redirect_uri, user):
        """Save the member's own connection under their key, owned by them."""
        from connectors import registry
        key = registry.personal_key(self.PROVIDER, user)
        r = http.request("POST", TOKEN_URL, retries=1, ok=(400, 401), data={
            "code": code, "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        if r.status_code >= 400:
            raise ConfigError(f"Google refused the code (HTTP {r.status_code})")
        t = r.json()
        if not t.get("refresh_token"):
            raise ConfigError("Google did not return a refresh token")
        granted = set((t.get("scope") or "").split())
        if self.SCOPE not in granted:
            # Granular consent: the member may untick the one permission we need.
            from connectors.base import ConnectRefused
            raise ConnectRefused("missing_scope", f"{self.SCOPE} not granted")
        info = http.request("GET", USERINFO_URL, retries=1, headers={
            "Authorization": f"Bearer {t['access_token']}"}).json() or {}
        email = (info.get("email") or "").lower()
        row = store.get(workspace, key)
        store.connect(workspace, key, email or "Google account", {
            "refresh_token": t["refresh_token"], "access_token": t["access_token"],
            "expires_at": time.time() + int(t.get("expires_in", 3600)) - 60}, user)
        settings = dict((row or {}).get("settings") or {}, owner=user, google=email)
        if row and (row.get("settings") or {}).get("google") != email:
            settings[self.SETTINGS_KEY] = []      # another Google account: pick again
        store.update(workspace, key, settings=settings)

    # ---- tokens and calls ---------------------------------------------------------
    def _access_token(self, workspace, key, force=False):
        with http.refresh_lock(f"{key}:{workspace}"):
            sec = store.secret(workspace, key)
            if not sec:
                raise AuthExpired(f"{self.LABEL} is not connected")
            if not force and sec.get("access_token") and sec.get("expires_at", 0) > time.time():
                return sec["access_token"]
            r = http.request("POST", TOKEN_URL, ok=(400, 401), data={
                "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
                "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
                "refresh_token": sec["refresh_token"], "grant_type": "refresh_token"})
            if r.status_code in (400, 401):
                raise AuthExpired("Google refused the refresh token")
            t = r.json()
            sec.update(access_token=t["access_token"],
                       expires_at=time.time() + int(t.get("expires_in", 3600)) - 60)
            if t.get("refresh_token"):
                sec["refresh_token"] = t["refresh_token"]
            store.update(workspace, key, secret=sec)
            return sec["access_token"]

    def _get(self, ctx, url, params=None, ok=()):
        """One GET with the member's token; a 401 refreshes it once."""
        for attempt in (0, 1):
            token = self._access_token(ctx.workspace, ctx.provider, force=attempt == 1)
            try:
                return http.request("GET", url, params=params, ok=ok,
                                    bucket=http.bucket_key(ctx.provider, ctx.workspace),
                                    rate=RATE, burst=RATE,
                                    headers={"Authorization": f"Bearer {token}"})
            except AuthExpired:
                if attempt == 1:
                    raise

    def revoke(self, workspace, key):
        """Revoke the member's refresh token at Google (and its access tokens)."""
        sec = store.secret(workspace, key) or {}
        if not sec.get("refresh_token"):
            return
        try:
            http.request("POST", REVOKE_URL, retries=1, ok=(400,),
                         data={"token": sec["refresh_token"]})
        except Exception as e:
            log.warning("%s: couldn't revoke a member's token in workspace %s (%s)", self.LABEL,
                        workspace, type(e).__name__)

    def disconnect(self, workspace):
        """(Personal connections are revoked by key: revoke().)"""
