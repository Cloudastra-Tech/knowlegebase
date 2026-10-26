"""
connectors/sharepoint.py  —  sync SharePoint Online and OneDrive files into an index.

Read-only, through Microsoft Graph, as the APPLICATION (no user's token): a
multi-tenant Entra app with one application permission, Sites.Read.All, which a
tenant admin approves for their whole organisation (admin consent). What gets
synced: ONLY what the admin picks on the card — whole sites (every document
library in them, including ones added later) or single libraries ("drives";
a OneDrive is a personal site's drive) — nothing until something is picked.
Files go through connectors/extract.py (Word, PowerPoint, Excel, PDF, HTML,
Markdown, text); others aren't listed.

Connecting (two trips through Microsoft, one callback):
  1. the admin signs in (OpenID Connect, /organizations): the ID token, taken
     straight from the token endpoint, says which tenant they belong to (`tid`);
  2. if the app has no consent in that tenant yet, the browser goes on to the
     admin consent screen FOR THAT TENANT. Microsoft's answer names a tenant, but
     a redirect can be forged ("never use the tenant ID value ... to authenticate
     or authorize", https://learn.microsoft.com/en-us/entra/identity-platform/v2-admin-consent),
     so it must be the one the admin signed in to; then an app-only token
     (client credentials) is asked for, which only works once consent exists.
  The connection keeps only the tenant id: app tokens are fetched with the
  server's client secret when needed (they come without a refresh token).

How a sync finds what changed: per library, `GET /drives/{id}/root/delta`.
  FULL (first run, the picks or the libraries under them changed, every
  RECONCILE seconds, a 410 Gone for an expired delta token, or asked for)
      the delta from scratch lists every file; anything known but not listed
      is deleted. Each library's final @odata.deltaLink is the cursor.
  INCREMENTAL  each library's deltaLink: only what changed since. A `deleted`
      facet deletes. Items are tracked by id (Graph's advice).

Permissions are CAPTURED, not enforced (who may ask is the connection's access
level): GET /drives/{d}/items/{i}/permissions for a file that is new, changed,
or flagged with a sharing change (Prefer: hierarchicalsharing), and for every
file on a full run. Users by email (else entra:user:<id>), Entra groups as
entra:<object id>, SharePoint site groups as sharepoint:<site>:<id>,
organisation links as `organization`, anonymous links as `anyone`. A folder's
sharing change isn't reported for the files under it: the next full run
catches it (worst case RECONCILE).

Throttling: 429 / 503 with Retry-After are waited out and retried by
connectors/http.py (per tenant bucket); requests carry the decorated
User-Agent SharePoint asks ISVs for
(https://learn.microsoft.com/en-us/sharepoint/dev/general-development/how-to-avoid-getting-throttled-or-blocked-in-sharepoint-online).

Not built (follow-up): change notifications (webhooks) on /drives/{id}/root to
sync sooner; polling stays the safety net either way. Group expansion
(GroupMember.Read.All) for enforcement. Sites.Selected for admins who grant
per site.

Needs SHAREPOINT_CLIENT_ID and SHAREPOINT_CLIENT_SECRET: an app registration
in Entra ID, "Accounts in any organizational directory (multitenant)", Web
redirect URI <PUBLIC_URL>/api/integrations/sharepoint/callback, Microsoft Graph
APPLICATION permission Sites.Read.All (and the delegated openid / profile /
email for the sign-in).

API references (checked 2026-10-27):
  sign-in (auth code)   https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow
  admin consent         https://learn.microsoft.com/en-us/entra/identity-platform/v2-admin-consent
  client credentials    https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow
  AADSTS codes          https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes
  getAllSites           https://learn.microsoft.com/en-us/graph/api/site-getallsites
  site drives           https://learn.microsoft.com/en-us/graph/api/drive-list
  delta                 https://learn.microsoft.com/en-us/graph/api/driveitem-delta
  permissions           https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions
  download              https://learn.microsoft.com/en-us/graph/api/driveitem-get-content
  throttling            https://learn.microsoft.com/en-us/graph/throttling
"""

import base64
import hashlib
import json
import logging
import os
import time
import urllib.parse

import integrations_store as store
from connectors import extract, http
from connectors.base import (AuthExpired, ConfigError, ConnectRefused, Connector, Cursor,
                             Delete, Fetched, FormError, Item, LimitReached, MissingScope,
                             NextStep, NotFound, Skipped, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "sharepoint"
LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_DEFAULT = "https://graph.microsoft.com/.default"
SIGN_IN_SCOPES = "openid profile email"
USER_AGENT = "ISV|Cloudastra|KnowledgeAssistant/1.0"
RATE = 8                               # calls a second per tenant
MAX_BYTES = 20 * 1024 * 1024           # files bigger than this are skipped
RECONCILE = 24 * 60 * 60               # seconds between full listings (deletes, permissions)
CONSENT_TRIES = 3                      # consent can take a moment to reach the token service
# Token-endpoint answers that mean "this tenant hasn't (or no longer has) consented
# to the app" (reference-error-codes): the admin has to approve it again.
NO_CONSENT = ("AADSTS65001", "AADSTS700016", "AADSTS500011", "AADSTS90094")
PICK_KINDS = ("site", "onedrive", "drive")


def _claims(id_token):
    """The ID token's claims. Not signature-checked: it came straight from the
    token endpoint over TLS, which OpenID Connect Core 3.1.3.7 accepts in place
    of the signature for a confidential client
    (https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation)."""
    try:
        body = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (AttributeError, IndexError, ValueError):
        return {}


def _sid(drive, item):
    return f"{drive}:{item}"


def _acl(perms, site):
    """Who can read a file, from its permissions (grantedToV2 / grantedToIdentitiesV2
    and sharing links)."""
    users, groups = set(), set()
    anyone = organization = False
    for p in perms:
        scope = (p.get("link") or {}).get("scope")
        anyone |= scope == "anonymous"
        organization |= scope == "organization"
        for who in [p.get("grantedToV2")] + list(p.get("grantedToIdentitiesV2") or []):
            for key, ident in (who or {}).items():
                if not isinstance(ident, dict):
                    continue
                if key in ("user", "siteUser"):
                    if ident.get("email"):
                        users.add(ident["email"].lower())
                    elif key == "user" and ident.get("id"):
                        users.add(f"entra:user:{ident['id']}")
                elif key == "group" and ident.get("id"):
                    groups.add(f"entra:{ident['id']}")
                elif key == "siteGroup" and ident.get("id"):
                    groups.add(f"sharepoint:{site}:{ident['id']}")
    return {"users": sorted(users), "groups": sorted(groups), "anyone": anyone,
            "organization": organization}


class SharePoint(Connector):
    PROVIDER = PROVIDER
    LABEL = "SharePoint & OneDrive"
    DESCRIPTION = "Office files, PDFs and text from the sites and libraries you pick"
    NOTE = ("Needs a Microsoft 365 admin: they approve read-only access for the "
            "organisation when connecting.")
    AUTH = "oauth"
    PICKER = "tree"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "containers"
    UNIT = "files"
    SETUP_ENV = "SHAREPOINT_CLIENT_ID / SHAREPOINT_CLIENT_SECRET"
    ACL = True

    def configured(self):
        return bool(os.getenv("SHAREPOINT_CLIENT_ID") and os.getenv("SHAREPOINT_CLIENT_SECRET"))

    # ---- connecting -----------------------------------------------------------------
    def auth_url(self, redirect_uri, state):
        """Step 1: the admin signs in, so we learn their tenant from Microsoft itself."""
        return f"{LOGIN}/organizations/oauth2/v2.0/authorize?" + urllib.parse.urlencode({
            "client_id": os.environ["SHAREPOINT_CLIENT_ID"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": SIGN_IN_SCOPES,
            "state": state,
            "prompt": "select_account",
        })

    def _consent_url(self, tenant, redirect_uri, state):
        """Step 2: the tenant's admin consent screen (static /.default scope: every
        application permission the registration lists)."""
        return f"{LOGIN}/{tenant}/v2.0/adminconsent?" + urllib.parse.urlencode({
            "client_id": os.environ["SHAREPOINT_CLIENT_ID"],
            "scope": GRAPH_DEFAULT,
            "redirect_uri": redirect_uri,
            "state": state,
        })

    def callback_code(self, params, step=None):
        """Step 1 brings a code; step 2 brings admin_consent=True and the tenant."""
        if step:
            return params.get("tenant", "") if params.get("admin_consent", "").lower() == "true" \
                else ""
        return params.get("code", "")

    def _app_token(self, tenant):
        """An app-only Graph token for the tenant: {"access_token", "expires_at"}.
        AuthExpired when the tenant hasn't consented (or took it back)."""
        r = http.request("POST", f"{LOGIN}/{tenant}/oauth2/v2.0/token", retries=1,
                         ok=(400, 401), data={
                             "client_id": os.environ.get("SHAREPOINT_CLIENT_ID", ""),
                             "client_secret": os.environ.get("SHAREPOINT_CLIENT_SECRET", ""),
                             "scope": GRAPH_DEFAULT, "grant_type": "client_credentials"})
        if r.status_code >= 400:
            try:
                err = r.json()
            except ValueError:
                err = {}
            text = f"{err.get('error', '')} {err.get('error_description', '')}"
            if err.get("error") == "invalid_client":         # the server's secret, not them
                raise ConfigError("Microsoft refused the app's client secret")
            consent = any(code in text for code in NO_CONSENT)
            e = AuthExpired("no admin consent in the tenant" if consent else
                            f"Microsoft refused the app token ({err.get('error') or r.status_code})")
            e.consent = consent
            raise e
        t = r.json()
        return {"access_token": t["access_token"],
                "expires_at": time.time() + int(t.get("expires_in", 3600)) - 60}

    def finish_connect(self, workspace, code, redirect_uri, user, step=None):
        if step is None:
            r = http.request("POST", f"{LOGIN}/organizations/oauth2/v2.0/token", retries=1,
                             ok=(400, 401), data={
                                 "client_id": os.environ.get("SHAREPOINT_CLIENT_ID", ""),
                                 "client_secret": os.environ.get("SHAREPOINT_CLIENT_SECRET", ""),
                                 "code": code, "redirect_uri": redirect_uri,
                                 "grant_type": "authorization_code", "scope": SIGN_IN_SCOPES})
            if r.status_code >= 400:
                raise ConfigError(f"Microsoft refused the sign-in code (HTTP {r.status_code})")
            claims = _claims(r.json().get("id_token"))
            tenant = claims.get("tid")
            if not tenant:
                raise ConnectRefused("connect_failed", "no tenant in the ID token")
            who = claims.get("preferred_username") or claims.get("email") or ""
            try:
                token = self._app_token(tenant)
            except AuthExpired as e:
                if not getattr(e, "consent", False):
                    raise
                raise NextStep(lambda state: self._consent_url(tenant, redirect_uri, state),
                               {"tenant": tenant, "who": who}) from None
            return self._save(workspace, tenant, who, token, user)
        if code != step.get("tenant"):
            raise ConnectRefused("wrong_account",
                                 "admin consent came back for another tenant than the sign-in")
        for attempt in range(CONSENT_TRIES):
            try:
                token = self._app_token(code)
                break
            except AuthExpired as e:
                if not getattr(e, "consent", False) or attempt == CONSENT_TRIES - 1:
                    raise ConnectRefused("admin_consent_required", str(e)) from None
                http.sleep(5 * (attempt + 1))
        return self._save(workspace, code, step.get("who") or "", token, user)

    def _save(self, workspace, tenant, who, token, user):
        """Check the app can list sites (the permission was granted, not just the
        consent screen passed), then save the connection."""
        try:
            self._graph(None, "GET", f"{GRAPH}/sites/getAllSites", token=token["access_token"],
                        tenant=tenant, params={"$top": 1})
        except MissingScope:
            raise ConnectRefused("missing_scope", "Sites.Read.All not granted") from None
        domain = who.split("@")[-1] if "@" in who else ""
        account = f"{domain} ({who})" if domain else (who or f"tenant {tenant[:8]}")
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, {"tenant": tenant, **token}, user)
        if row and (row.get("settings") or {}).get("tenant") != tenant:
            store.update(workspace, PROVIDER,                 # another organisation
                         settings=dict(row.get("settings") or {}, containers=[], tenant=tenant))
        elif not row:
            store.update(workspace, PROVIDER, settings={"tenant": tenant})

    # ---- calls ----------------------------------------------------------------------
    def _access_token(self, workspace, force=False):
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = store.secret(workspace, PROVIDER)
            if not sec or not sec.get("tenant"):
                raise AuthExpired("SharePoint is not connected")
            if not force and sec.get("access_token") and sec.get("expires_at", 0) > time.time():
                return sec["access_token"], sec["tenant"]
            sec.update(self._app_token(sec["tenant"]))
            store.update(workspace, PROVIDER, secret=sec)
            return sec["access_token"], sec["tenant"]

    def _graph(self, workspace, method, url, token=None, tenant=None, headers=None,
               container=False, **kwargs):
        """One Graph call; a 401 fetches a new app token once. `container`: a
        listing the app's permission should always allow, so a 403 there means
        the permission is missing, not that one item is private."""
        for attempt in (0, 1):
            if workspace is not None:
                token, tenant = self._access_token(workspace, force=attempt == 1)
            try:
                return http.request(method, url, bucket=http.bucket_key(PROVIDER, tenant),
                                    rate=RATE, burst=RATE, headers={
                                        "Authorization": f"Bearer {token}",
                                        "User-Agent": USER_AGENT, **(headers or {})}, **kwargs)
            except AuthExpired:
                if attempt == 1 or workspace is None:
                    raise
            except http.Forbidden:
                if container or workspace is None:
                    raise MissingScope(f"{method} {http.safe_url(url)}: HTTP 403") from None
                raise

    def _json(self, ws, url, **kwargs):
        return self._graph(ws, "GET", url, **kwargs).json()

    def _all(self, ws, url, params=None):
        """Every item of a paged Graph collection (@odata.nextLink is a whole URL)."""
        while url:
            data = self._json(ws, url, params=params, container=True)
            yield from data.get("value") or []
            url, params = data.get("@odata.nextLink"), None

    # ---- picking --------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        """Top level: the tenant's sites (OneDrives are personal sites); inside a
        site: its document libraries."""
        ws = ctx.workspace
        if parent is None:
            sites = [s for s in self._all(ws, f"{GRAPH}/sites/getAllSites") if s.get("id")]
            return sorted(({"id": s["id"], "name": s.get("displayName") or s.get("name")
                            or s.get("webUrl") or s["id"],
                            "kind": "onedrive" if s.get("isPersonalSite") else "site",
                            "url": s.get("webUrl"), "has_children": True} for s in sites),
                          key=lambda c: (c["kind"] == "onedrive", c["name"].lower()))
        return [{"id": d["id"], "name": d.get("name") or "Documents", "kind": "drive",
                 "url": d.get("webUrl"), "site": parent}
                for d in self._all(ws, f"{GRAPH}/sites/{parent}/drives")]

    def check_containers(self, picked):
        out = super().check_containers(picked)
        sites = {str(p.get("id")): str(p.get("site") or "")[:300]
                 for p in picked[:200] if isinstance(p, dict)}
        for p in out:
            if p["kind"] not in PICK_KINDS:
                raise FormError("Pick sites, OneDrives or document libraries.")
            if p["kind"] == "drive" and sites.get(p["id"]):
                p["site"] = sites[p["id"]]          # its site, for the permission ids
        return out

    # ---- syncing --------------------------------------------------------------------
    def _drives(self, ws, picks):
        """[(drive id, site id)] under the picks; a picked site that's gone stops the run."""
        out = {}
        for p in picks:
            try:
                if p["kind"] == "drive":
                    d = self._json(ws, f"{GRAPH}/drives/{p['id']}", container=True)
                    out[d["id"]] = p.get("site") or ""
                else:
                    for d in self._all(ws, f"{GRAPH}/sites/{p['id']}/drives"):
                        out[d["id"]] = p["id"]
            except NotFound:
                raise ConfigError(f"a picked {p['kind']} is gone") from None
        return sorted(out.items())

    def _permissions(self, ws, drive, item, site):
        try:
            perms = list(self._all(ws, f"{GRAPH}/drives/{drive}/items/{item}/permissions"))
        except (http.NotFound, MissingScope):
            return {"partial": True}
        return _acl(perms, site)

    def changes(self, ctx, cursor):
        picks = (ctx.settings or {}).get("containers") or []
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        drives = self._drives(ws, picks)
        sig = hashlib.sha256(json.dumps(drives).encode()).hexdigest()[:16]
        full = (ctx.full or not state or state.get("drives") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        for attempt in (0, 1):
            try:
                yield from self._run(ctx, drives, sig, state, full or attempt == 1, cursor)
                return
            except NotFound as e:
                if getattr(e, "status", None) != 410 or attempt == 1:
                    raise
                log.info("SharePoint: a delta token of workspace %s expired; listing again", ws)

    def _run(self, ctx, drives, sig, state, full, cursor):
        ws = ctx.workspace
        links = {} if full else dict(state.get("links") or {})
        new_links, seen, n = {}, set(), 0
        for drive, site in drives:
            url = links.get(drive) or f"{GRAPH}/drives/{drive}/root/delta"
            while url:
                data = self._json(ws, url, container=True,
                                  headers={"Prefer": "hierarchicalsharing"})
                for it in data.get("value") or []:
                    sid = _sid(drive, it["id"])
                    if "deleted" in it:
                        if sid in ctx.known:
                            yield Delete(sid)
                        continue
                    f = it.get("file")
                    if f is None or "root" in it or "folder" in it:
                        continue
                    name = it.get("name") or "Untitled"
                    if extract.kind_of(f.get("mimeType"), name) is None:
                        if sid in ctx.known:              # renamed to a type we don't read
                            yield Delete(sid)
                        continue
                    if ctx.max_items and n >= ctx.max_items:
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)              # deletes nothing; tries again
                        return
                    seen.add(sid)
                    n += 1
                    version = it.get("lastModifiedDateTime") or it.get("eTag") or ""
                    size = it.get("size")
                    if size and size > MAX_BYTES:
                        yield Skipped(sid, "too_large", name, it.get("webUrl") or "")
                        continue
                    known = ctx.items.get(sid) or {}
                    if full or "shared" in it or ctx.known.get(sid) != version:
                        acl = self._permissions(ws, drive, it["id"], site)
                    else:
                        acl = known.get("acl")
                    yield Upsert(Item(
                        source_id=sid, title=name, url=it.get("webUrl") or "", version=version,
                        kind="file", mime=f.get("mimeType") or "", size=size,
                        modified=it.get("lastModifiedDateTime"),
                        parent_id=_sid(drive, (it.get("parentReference") or {}).get("id") or ""),
                        acl=acl, attrs={"drive": drive, "item": it["id"], "site": site}))
                url = data.get("@odata.nextLink")
                if not url:
                    new_links[drive] = data.get("@odata.deltaLink")
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"drives": sig, "links": new_links,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def fetch(self, ctx, item):
        """The file's bytes (Graph answers 302 to a pre-authenticated download URL,
        which requests follows)."""
        if item.size and item.size > MAX_BYTES:
            return None
        a = item.attrs or {}
        r = self._graph(ctx.workspace, "GET",
                        f"{GRAPH}/drives/{a['drive']}/items/{a['item']}/content", timeout=60)
        return Fetched(r.content, item.mime)

    def disconnect(self, workspace):
        """Nothing to revoke from here: the tenant's admin removes the app under
        Entra ID -> Enterprise applications if it should lose its consent."""


CONNECTOR = SharePoint()
