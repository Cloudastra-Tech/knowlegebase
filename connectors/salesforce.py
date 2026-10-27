"""
connectors/salesforce.py  —  sync a workspace's Salesforce Knowledge (Lightning
Knowledge) articles into its index.

Connecting: OAuth 2.0 WEB SERVER flow with PKCE and a refresh token, through an
EXTERNAL CLIENT APP (ECA). Since Spring '26 Salesforce restricts creating new
Connected Apps by default (UI and Metadata API, except by installing a package;
re-enabling takes a request to Salesforce Support) and points new integrations
at External Client Apps. A LOCAL ECA can only be used in the org that created
it, and distributing one to other orgs means a second-generation managed
package, so by default each customer creates a local ECA in their own org
(deploy.md, "Salesforce Knowledge": callback URL
<PUBLIC_URL>/api/integrations/salesforce/callback, scopes `api` and
`refresh_token`, PKCE required, refresh token rotation on) and enters its
consumer key and secret on the card, with their My Domain address; they are
stored with the tokens, encrypted with the workspace's key. A server that ships
a PACKAGED ECA sets SALESFORCE_CLIENT_ID / SALESFORCE_CLIENT_SECRET instead and
the form only asks for the address.
Salesforce's token answer carries no lifetime: the access token is refreshed
when a call says 401, under a per-connection lock, and a rotated refresh token
(rotation makes the old one invalid, and reusing it revokes the new one too) is
saved before the new access token is used. invalid_grant: reconnect.

What is synced: ONLINE (published) versions of Knowledge__kav articles, every
language, one item per article and language ("{KnowledgeArticleId}:{Language}").
Their text is the Summary and every RICH TEXT field of the article
object (found with a describe call, since Lightning Knowledge's body fields are
the org's own custom fields). An admin may narrow it to some article RECORD
TYPES (the picker); with none picked every article is synced. Captured into each
item's acl: its CHANNELS (IsVisibleInApp / Pkb / Csp / Prm, where the org has
them) and DATA CATEGORIES: {"via": "salesforce", "channels": {...},
"data_categories": [...]}. Who may read an article inside Salesforce depends on
profiles, permission sets and data category visibility, none of which map onto
this workspace's members, so under the source policy only an article in the
PUBLIC KNOWLEDGE BASE with no data categories (guest users can read it) is for
everyone; any other is staff (HR and admins); an acl without channels is for no
one (kb/services/permissions.py).

How a sync finds what changed (SOQL through the REST query resource, 2,000
records a batch, nextRecordsUrl):
  FULL (first run, picks changed, every RECONCILE seconds, asked for): every
      Online version; anything known but not listed was archived or deleted and
      goes behind the mass-delete guard.
  INCREMENTAL: Online versions with SystemModstamp after the last run's newest
      (less OVERLAP seconds); then versions ARCHIVED since, and those of their
      articles with no Online version left are deleted (publishing a new version
      archives the old one, which must not delete the article).
Limits: Salesforce counts API requests per org per 24 hours; over it, calls get
403 REQUEST_LIMIT_EXCEEDED (RateLimited here: the sync tries again later).

API references (checked 2026-10-27; Salesforce's documentation pages refuse
automated reads, so these were checked through their published text):
  Spring '26      https://help.salesforce.com/s/articleView?id=release-notes.rn_security_identity_connected_app_creation_disabled.htm&release=260
                  https://www.salesforce.com/blog/spring-26-release-architect-highlights/
                  ("restricts Connected App creation by default across both the UI and
                  Metadata API, except through package installation"; ECAs for new
                  inbound integrations)
  ECAs            https://help.salesforce.com/s/articleView?id=xcloud.external_client_apps.htm
                  https://help.salesforce.com/s/articleView?id=sf.configure_packageable_external_client_apps.htm
                  (local: only in the org that created it; packaged: 2GP for other orgs)
  web server flow https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_web_server_flow.htm
                  (/services/oauth2/authorize, /services/oauth2/token; PKCE code_challenge
                  S256 required; instance_url in the answer)
  refresh         https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_refresh_token_flow.htm
                  (grant_type=refresh_token; rotation returns a new refresh token and
                  invalidates the old; invalid_grant: authorize again)
  revoke          https://help.salesforce.com/s/articleView?id=remoteaccess_revoke_token.htm
                  (POST /services/oauth2/revoke, form encoded `token`; a refresh token
                  revokes its access tokens too)
  query           https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_query.htm
                  (totalSize, done, nextRecordsUrl, records; up to 2,000 a batch)
  errors          https://developer.salesforce.com/docs/platform/api-rest/guide/errorcodes.html
                  (401: session expired or invalid; 403 REQUEST_LIMIT_EXCEEDED)
  limits          https://developer.salesforce.com/docs/platform/salesforce-app-limits-cheatsheet/guide/salesforce-app-limits-platform-api.html
  Knowledge__kav  https://developer.salesforce.com/docs/atlas.en-us.sfFieldRef.meta/sfFieldRef/salesforce_field_reference_Knowledge__kav.htm
                  (Lightning Knowledge's article version, API 39.0+; PublishStatus must be
                  filtered; IsVisibleInPkb: guest users of a public knowledge base)
                  https://developer.salesforce.com/docs/platform/salesforce-soql-sosl/guide/sforce-api-calls-soql-limits.html
"""

import base64
import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, ConnectRefused, Connector, Cursor,
                             Delete, Forbidden, FormError, Item, LimitReached, NotFound,
                             RateLimited, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "salesforce"
API_VERSION = "v62.0"
OBJECT = "Knowledge__kav"
SCOPE = "api refresh_token"
RATE = 5                              # calls a second per connection
RECONCILE = 24 * 60 * 60
OVERLAP = 120
CHANNELS = {"IsVisibleInApp": "app", "IsVisibleInPkb": "pkb", "IsVisibleInCsp": "csp",
            "IsVisibleInPrm": "prm"}
BASE_FIELDS = ("Id", "KnowledgeArticleId", "ArticleNumber", "Title", "UrlName", "Language",
               "PublishStatus", "LastPublishedDate", "SystemModstamp")
# login.salesforce.com, test.salesforce.com, or a My Domain (sandboxes:
# name--sandbox.sandbox.my.salesforce.com).
_LOGIN = re.compile(r"^(?:login|test|[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?(?:\.sandbox)?\.my)"
                    r"\.salesforce\.com$")
_INSTANCE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,120}[a-z0-9])?\.salesforce\.com$")
_ID = re.compile(r"^[a-zA-Z0-9]{15,18}$")


def login_host(value):
    """The sign-in host from what an admin typed: a My Domain address
    ("https://acme.my.salesforce.com"), login.salesforce.com or test.salesforce.com."""
    v = (value or "").strip().lower()
    if not v:
        v = "login.salesforce.com"
    if "//" in v:
        v = urllib.parse.urlsplit(v).hostname or ""
    v = v.split("/")[0]
    if not _LOGIN.match(v):
        raise FormError("Enter your Salesforce My Domain address, like "
                        "https://acme.my.salesforce.com (or login.salesforce.com).")
    return v


def _instance(url):
    """instance_url from Salesforce, checked: https on a salesforce.com host."""
    p = urllib.parse.urlsplit(url or "")
    if p.scheme != "https" or not _INSTANCE.match(p.hostname or "") or p.port:
        raise ConfigError("Salesforce returned an unexpected instance address")
    return f"https://{p.hostname}"


def _quote(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


class Salesforce(Connector):
    PROVIDER = PROVIDER
    LABEL = "Salesforce Knowledge"
    DESCRIPTION = "Published Knowledge articles"
    AUTH = "token"                    # a form (address, the app's key), then a sign-in
    PICKER = "list"
    PICKER_REQUIRED = False
    SETTINGS_KEY = "record_types"
    UNIT = "articles"
    ACL = True

    def can_sign_in(self):
        return True

    def server_app(self):
        return bool(os.getenv("SALESFORCE_CLIENT_ID") and os.getenv("SALESFORCE_CLIENT_SECRET"))

    # ---- connecting ---------------------------------------------------------------
    def connect_start(self, redirect_uri, state, form):
        form = form if isinstance(form, dict) else {"url": form}
        host = login_host(form.get("url"))
        client_id = (form.get("client_id") or "").strip()
        client_secret = (form.get("client_secret") or "").strip()
        if not client_id and self.server_app():
            client_id, client_secret = os.environ["SALESFORCE_CLIENT_ID"], ""
        if not client_id or (not client_secret and not self.server_app()):
            raise FormError("Enter the consumer key and consumer secret of the External Client "
                            "App you created in Salesforce.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        url = f"https://{host}/services/oauth2/authorize?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "scope": SCOPE, "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256"})
        # Kept for the callback only (this process's memory, OAUTH_STATE_SECONDS).
        return url, {"login": host, "client_id": client_id, "client_secret": client_secret,
                     "verifier": verifier}

    def _client(self, sec):
        if sec.get("client_id"):
            return sec["client_id"], sec.get("client_secret") or ""
        return os.environ.get("SALESFORCE_CLIENT_ID", ""), os.environ.get(
            "SALESFORCE_CLIENT_SECRET", "")

    def _token_call(self, host, data):
        r = http.request("POST", f"https://{host}/services/oauth2/token", retries=1,
                         ok=(400, 401), data=data,
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "Accept": "application/json"})
        if r.status_code in (400, 401):
            raise AuthExpired(f"Salesforce refused the {data['grant_type']} grant")
        return r.json()

    def finish_connect(self, workspace, code, redirect_uri, user, step=None):
        step = step or {}
        host = step.get("login")
        if not host:
            raise ConnectRefused("connect_failed", "no sign-in host in the state")
        client_id = step.get("client_id") or os.environ.get("SALESFORCE_CLIENT_ID", "")
        client_secret = step.get("client_secret") or os.environ.get("SALESFORCE_CLIENT_SECRET",
                                                                    "")
        t = self._token_call(host, {"grant_type": "authorization_code", "code": code,
                                    "client_id": client_id, "client_secret": client_secret,
                                    "redirect_uri": redirect_uri,
                                    "code_verifier": step.get("verifier", "")})
        if not t.get("refresh_token"):
            raise ConfigError("Salesforce did not return a refresh token (add the "
                              "refresh_token scope to the app)")
        instance = _instance(t.get("instance_url"))
        org = (urllib.parse.urlsplit(t.get("id") or "").path.split("/") + ["", "", ""])[2]
        sec = {"access_token": t["access_token"], "refresh_token": t["refresh_token"],
               "login": host, "instance_url": instance}
        if step.get("client_id") and step.get("client_id") != os.environ.get(
                "SALESFORCE_CLIENT_ID"):
            sec.update(client_id=client_id, client_secret=client_secret)
        account = urllib.parse.urlsplit(instance).hostname
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, sec, user)
        settings = dict((row or {}).get("settings") or {}, login=host, org=org)
        if row and (row.get("settings") or {}).get("org") != org:
            settings["record_types"] = []         # another org: other record types
        store.update(workspace, PROVIDER, settings=settings)

    def connect_form(self, workspace, form, user):
        raise FormError("Sign in with Salesforce to connect it.")

    # ---- tokens and calls ---------------------------------------------------------
    def _session(self, ws, used=None):
        """(instance URL, access token); refreshed (the rotated refresh token saved
        first) when `used` was refused."""
        with http.refresh_lock(f"{PROVIDER}:{ws}"):
            sec = store.secret(ws, PROVIDER)
            if not sec or not sec.get("access_token"):
                raise AuthExpired("Salesforce is not connected")
            if used is None or sec["access_token"] != used:
                return sec["instance_url"], sec["access_token"]
            client_id, client_secret = self._client(sec)
            t = self._token_call(sec["login"], {"grant_type": "refresh_token",
                                                "refresh_token": sec["refresh_token"],
                                                "client_id": client_id,
                                                "client_secret": client_secret})
            sec.update(access_token=t["access_token"],
                       refresh_token=t.get("refresh_token") or sec["refresh_token"],
                       instance_url=_instance(t.get("instance_url") or sec["instance_url"]))
            store.update(ws, PROVIDER, secret=sec)
            return sec["instance_url"], sec["access_token"]

    def _call(self, instance, token, url):
        if urllib.parse.urlsplit(url).hostname != urllib.parse.urlsplit(instance).hostname:
            raise ConfigError("a link outside the connected Salesforce org")
        r = http.request("GET", url, ok=(401, 403), bucket=http.bucket_key(PROVIDER, instance),
                         rate=RATE, burst=RATE,
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/json"})
        if r.status_code == 403:
            codes = _codes(r)
            if "REQUEST_LIMIT_EXCEEDED" in codes:
                raise RateLimited(f"GET {http.safe_url(url)}: REQUEST_LIMIT_EXCEEDED",
                                  retry_after=3600)
            raise Forbidden(f"GET {http.safe_url(url)}: HTTP 403 {', '.join(codes)}")
        return r

    def _get(self, ws, path):
        """GET a path of the org's REST API (or a nextRecordsUrl); one 401 refreshes."""
        instance, token = self._session(ws)
        url = path if path.startswith("https://") else f"{instance}{path}"
        r = self._call(instance, token, url)
        if r.status_code == 401:
            instance, token = self._session(ws, used=token)
            url = path if path.startswith("https://") else f"{instance}{path}"
            r = self._call(instance, token, url)
            if r.status_code == 401:
                raise AuthExpired(f"GET {http.safe_url(url)}: HTTP 401")
        try:
            return r.json()
        except ValueError:
            raise ConfigError("the answer isn't JSON") from None

    def _query(self, ws, soql):
        data = self._get(ws, f"/services/data/{API_VERSION}/query?"
                             + urllib.parse.urlencode({"q": soql}))
        while True:
            yield from data.get("records") or []
            nxt = data.get("nextRecordsUrl")
            if data.get("done", True) or not nxt:
                return
            data = self._get(ws, nxt)

    # ---- the article object -------------------------------------------------------
    def _describe(self, ws):
        """(fields to select, rich text fields, channel fields, has data categories)."""
        try:
            d = self._get(ws, f"/services/data/{API_VERSION}/sobjects/{OBJECT}/describe")
        except NotFound:
            raise ConfigError("Lightning Knowledge isn't turned on in this org") from None
        names = {f["name"]: f for f in d.get("fields") or []}
        rich = sorted(n for n, f in names.items()
                      if f.get("type") == "textarea" and f.get("extraTypeInfo") == "richtextarea")
        channels = [n for n in CHANNELS if n in names]
        fields = [n for n in BASE_FIELDS if n in names] + channels + rich
        fields += [n for n in ("Summary", "RecordTypeId") if n in names]
        cats = any(c.get("relationshipName") == "DataCategorySelections"
                   for c in d.get("childRelationships") or [])
        return fields, rich, channels, cats

    def list_containers(self, ctx, parent=None):
        rows = self._query(ctx.workspace, "SELECT Id, Name FROM RecordType WHERE SobjectType = "
                                          f"{_quote(OBJECT)} AND IsActive = true ORDER BY Name")
        return [{"id": r["Id"], "name": r.get("Name") or r["Id"], "kind": "record_type"}
                for r in rows]

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            if not _ID.match(p["id"]):
                raise FormError("Pick article record types.")
            p["kind"] = "record_type"
        return out

    # ---- syncing ------------------------------------------------------------------
    def _soql(self, fields, cats, where):
        select = ", ".join(fields)
        if cats:
            select += (", (SELECT DataCategoryGroupName, DataCategoryName FROM "
                       "DataCategorySelections)")
        return f"SELECT {select} FROM {OBJECT} WHERE {where} ORDER BY SystemModstamp"

    def _item(self, instance, r, rich, channels):
        sid = f"{r['KnowledgeArticleId']}:{r.get('Language') or ''}"
        selections = ((r.get("DataCategorySelections") or {}).get("records")) or []
        acl = {"via": PROVIDER, "channels": {CHANNELS[c]: bool(r.get(c)) for c in channels},
               "data_categories": sorted(f"{s.get('DataCategoryGroupName')}:"
                                         f"{s.get('DataCategoryName')}" for s in selections)}
        parts = []
        if (r.get("Summary") or "").strip():
            parts.append(f"<p>{_esc(r['Summary'])}</p>")
        parts += [r[f] for f in rich if (r.get(f) or "").strip()]
        return Item(source_id=sid, title=r.get("Title") or "Untitled",
                    url=f"{instance}/lightning/r/{OBJECT}/{r['Id']}/view",
                    version=r.get("SystemModstamp") or "", kind="article", mime="text/html",
                    modified=r.get("LastPublishedDate") or r.get("SystemModstamp"),
                    parent_id=r.get("RecordTypeId"), acl=acl,
                    attrs={"id": r["Id"], "article": r["KnowledgeArticleId"],
                           "number": r.get("ArticleNumber"), "language": r.get("Language"),
                           "categories": acl["data_categories"]},
                    content="\n".join(parts))

    def changes(self, ctx, cursor):
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        picks = sorted(p["id"] for p in (ctx.settings or {}).get("record_types") or [])
        now = time.time()
        full = (ctx.full or not state or state.get("picks") != picks
                or now - state.get("full_at", 0) > RECONCILE)
        since = None if full else _since(state.get("watermark"))
        full = full or since is None
        fields, rich, channels, cats = self._describe(ws)
        instance, _ = self._session(ws)
        scope = ""
        if picks and "RecordTypeId" in fields:
            scope = f" AND RecordTypeId IN ({', '.join(_quote(p) for p in picks)})"
        newest, seen, n = state.get("watermark") or "", set(), 0
        where = "PublishStatus = 'Online'" + scope + (f" AND SystemModstamp > {since}"
                                                      if since else "")
        for r in self._query(ws, self._soql(fields, cats, where)):
            newest = max(newest, r.get("SystemModstamp") or "")
            sid = f"{r['KnowledgeArticleId']}:{r.get('Language') or ''}"
            if sid in seen:
                continue
            if ctx.max_items and n >= ctx.max_items:
                yield LimitReached(ctx.max_items)
                yield Cursor(cursor)
                return
            seen.add(sid)
            n += 1
            yield Upsert(self._item(instance, r, rich, channels))
        if full:
            yield from ctx.missing(seen)
        else:
            gone = {}
            for r in self._query(ws, f"SELECT KnowledgeArticleId, Language FROM {OBJECT} WHERE "
                                     f"PublishStatus = 'Archived' AND SystemModstamp > {since}"):
                sid = f"{r['KnowledgeArticleId']}:{r.get('Language') or ''}"
                if sid in ctx.known and sid not in seen:
                    gone[sid] = r["KnowledgeArticleId"]
            ids = sorted(set(gone.values()))
            for i in range(0, len(ids), 100):
                chunk = ", ".join(_quote(x) for x in ids[i:i + 100])
                for r in self._query(ws, f"SELECT KnowledgeArticleId, Language FROM {OBJECT} "
                                         f"WHERE PublishStatus = 'Online' AND "
                                         f"KnowledgeArticleId IN ({chunk})"):
                    gone.pop(f"{r['KnowledgeArticleId']}:{r.get('Language') or ''}", None)
            for sid in sorted(gone):
                yield Delete(sid)                 # archived, no newer version online
        yield Cursor(json.dumps({"picks": picks, "watermark": newest,
                                 "full_at": now if full else state.get("full_at", 0)}),
                     full=full)

    def disconnect(self, workspace):
        """Revoke the refresh token (and with it its access tokens) at Salesforce."""
        sec = store.secret(workspace, PROVIDER) or {}
        token = sec.get("refresh_token") or sec.get("access_token")
        if not token or not sec.get("instance_url"):
            return
        try:
            http.request("POST", f"{sec['instance_url']}/services/oauth2/revoke", retries=1,
                         ok=(400,), data={"token": token},
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as e:
            log.warning("Salesforce: couldn't revoke the token of workspace %s (%s); revoke it "
                        "in Salesforce (Setup → Connected Apps OAuth Usage) if it matters",
                        workspace, type(e).__name__)


def _since(watermark):
    """The watermark less OVERLAP seconds, as a SOQL datetime literal."""
    try:
        t = datetime.datetime.fromisoformat(watermark.replace("Z", "+00:00").replace(
            "+0000", "+00:00"))
        return (t - datetime.timedelta(seconds=OVERLAP)).astimezone(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (AttributeError, ValueError):
        return None


def _codes(r):
    try:
        body = r.json()
    except ValueError:
        return []
    body = body if isinstance(body, list) else [body]
    return [str(e.get("errorCode") or e.get("error") or "") for e in body if isinstance(e, dict)]


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


CONNECTOR = Salesforce()
