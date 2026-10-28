"""
connectors/hubspot.py  —  sync a workspace's HubSpot knowledge base articles
into its index.

Connecting, two ways (one card):
  Private app    an access token of a HubSpot private app (Settings →
                 Integrations → Private apps) given the scope
                 cms.knowledge_base.articles.read: for a customer's own account.
                 Sent as Bearer; it doesn't expire.
  OAuth          only when the server has HUBSPOT_CLIENT_ID / HUBSPOT_CLIENT_SECRET
                 (a public app with the same scope, redirect URL
                 <PUBLIC_URL>/api/integrations/hubspot/callback): the admin signs in
                 and picks their account. Access tokens last 30 minutes; the refresh
                 token gets new ones (refreshed under a per-connection lock, a
                 returned refresh token saved before it is used).

The knowledge base API is in BETA at HubSpot (version 2027-03-beta, under
HubSpot's developer beta terms): its paths carry the version, kept in API
below so a later version is a one-line change.

What is synced: PUBLISHED articles (drafts, and articles archived in the
dashboard, are left out), their HTML body turned into text by
connectors/extract.py. Knowledge bases and their categories are the containers:
an admin may pick some; with none picked every knowledge base is synced. Each
article's ACCESS is captured into its acl: {"via": "hubspot", "access_type":
PUBLIC | SSO_LOGIN | ACCESS_GROUP_MEMBERSHIP, "access_group_ids": [...]}. PUBLIC
may be read by everyone in the workspace under the source policy; SSO_LOGIN and
access-group articles are staff (HR and admins: a HubSpot access group is a
list of CONTACTS, not of this workspace's members); anything else is for no one
(kb/services/permissions.py).

How a sync finds what changed:
  FULL (first run, picks changed, every RECONCILE seconds, asked for): every
      published article is listed; anything known but not listed was
      unpublished, archived or deleted and goes behind the mass-delete guard.
  INCREMENTAL: articles with updatedAfter the last run's newest updatedAt (less
      OVERLAP seconds), sorted by updatedAt, in any state, so one unpublished
      since is seen and deleted.
Rate limits: 110 requests every 10 seconds per installing account for public
apps; a 429 is waited out by connectors/http.py. Calls are spaced to RATE a
second.

API references (checked 2026-10-27):
  knowledge base  https://developers.hubspot.com/docs/api-reference/2027-03-beta/cms/knowledge-base/guide
                  (beta; /cms/knowledge-base/2027-03-beta/{knowledge-bases, articles, categories};
                  categories have parentCategoryId; knowledge bases id, name, domain)
  list articles   https://developers.hubspot.com/docs/api-reference/2027-03-beta/cms/knowledge-base/get-articles
                  (GET https://api.hubapi.com/cms/knowledge-base/2027-03-beta/articles; limit
                  up to 100, after, sort=updatedAt, updatedAfter (ISO 8601), state DRAFT |
                  PUBLISHED, archivedInDashboard, includeBody, includeTranslations,
                  knowledgeBaseId; results, paging.next.after; article id, title, url,
                  body, state, accessType PUBLIC | SSO_LOGIN | ACCESS_GROUP_MEMBERSHIP,
                  accessGroupIds, categoryId, knowledgeBaseId, language, updatedAt;
                  scope cms.knowledge_base.articles.read)
  scopes          https://developers.hubspot.com/docs/api/scopes
                  (cms.knowledge_base.articles.read: Service Hub Professional or Enterprise)
  OAuth           https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/oauth/working-with-oauth
                  (https://app.hubspot.com/oauth/authorize: client_id, redirect_uri, scope
                  (space separated), state)
  tokens          https://developers.hubspot.com/docs/api-reference/legacy/authentication/manage-oauth-tokens
                  (POST https://api.hubspot.com/oauth/v3/token, form encoded; authorization_code
                  and refresh_token grants; access tokens expire after 30 minutes (expires_in);
                  hub_id in the response. No revoke endpoint is documented.)
  rate limits     https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
"""

import datetime
import json
import logging
import os
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Delete, Forbidden,
                             FormError, Item, LimitReached, Upsert)
from connectors.zendesk import article_html

log = logging.getLogger("uvicorn.error")

PROVIDER = "hubspot"
AUTH_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubspot.com/oauth/v3/token"
API = "https://api.hubapi.com/cms/knowledge-base/2027-03-beta"
SCOPE = "cms.knowledge_base.articles.read"
RATE = 5                              # calls a second (HubSpot allows 11 for public apps)
PAGE = 100
RECONCILE = 24 * 60 * 60
OVERLAP = 120
RENEW_AT = 0.8


def _since(watermark):
    try:
        t = datetime.datetime.fromisoformat(watermark.replace("Z", "+00:00"))
        return (t - datetime.timedelta(seconds=OVERLAP)).isoformat().replace("+00:00", "Z")
    except (AttributeError, ValueError):
        return None


def access_acl(article):
    return {"via": PROVIDER, "access_type": article.get("accessType"),
            "access_group_ids": [str(g) for g in article.get("accessGroupIds") or []]}


class HubSpot(Connector):
    PROVIDER = PROVIDER
    LABEL = "HubSpot"
    DESCRIPTION = "Knowledge base articles"
    AUTH = "token"
    PICKER = "tree"
    PICKER_REQUIRED = False
    UNIT = "articles"
    ACL = True
    NOTE = "Needs Service Hub Professional or Enterprise. HubSpot's knowledge base API is in beta."

    # ---- connecting ---------------------------------------------------------------
    def can_sign_in(self):
        return bool(os.getenv("HUBSPOT_CLIENT_ID") and os.getenv("HUBSPOT_CLIENT_SECRET"))

    def auth_url(self, redirect_uri, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": os.environ["HUBSPOT_CLIENT_ID"], "redirect_uri": redirect_uri,
            "scope": SCOPE, "state": state})

    def connect_start(self, redirect_uri, state, form):
        return self.auth_url(redirect_uri, state), None

    def _token_call(self, data):
        r = http.request("POST", TOKEN_URL, retries=1, ok=(400, 401), data={
            **data, "client_id": os.environ.get("HUBSPOT_CLIENT_ID", ""),
            "client_secret": os.environ.get("HUBSPOT_CLIENT_SECRET", "")},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code in (400, 401):
            raise AuthExpired(f"HubSpot refused the {data['grant_type']} grant")
        return r.json()

    @staticmethod
    def _tokens(t, old=None):
        life = int(t.get("expires_in") or 1800)
        now = time.time()
        return {"access_token": t["access_token"],
                "refresh_token": t.get("refresh_token") or (old or {}).get("refresh_token"),
                "expires_at": now + life - 60, "renew_at": now + life * RENEW_AT}

    def finish_connect(self, workspace, code, redirect_uri, user, step=None):
        t = self._token_call({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": redirect_uri})
        if not t.get("refresh_token"):
            raise ConfigError("HubSpot did not return a refresh token")
        self._save(workspace, self._tokens(t), str(t.get("hub_id") or ""), user)

    def connect_form(self, workspace, form, user):
        """A private app's access token: checked by listing the knowledge bases."""
        token = (form.get("token") or "").strip()
        if not token:
            raise FormError("Paste a private app's access token (Settings → Integrations → "
                            "Private apps) with the cms.knowledge_base.articles.read scope.")
        try:
            kbs = self._call(token, f"{API}/knowledge-bases", {"limit": PAGE})
        except AuthExpired:
            raise FormError("HubSpot didn't accept that token.") from None
        except Forbidden:
            raise FormError("That token can't read the knowledge base: give the private app "
                            "the cms.knowledge_base.articles.read scope.") from None
        except Exception:
            raise FormError("Couldn't reach HubSpot.") from None
        first = (kbs.get("results") or [{}])[0]
        return self._save(workspace, {"token": token}, first.get("domain") or "", user)

    def _save(self, workspace, sec, account_key, user):
        account = f"HubSpot ({account_key})" if account_key else "HubSpot"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, sec, user)
        settings = dict((row or {}).get("settings") or {}, account=account_key)
        if row and (row.get("settings") or {}).get("account") != account_key:
            settings["containers"] = []           # another account: other knowledge bases
        store.update(workspace, PROVIDER, settings=settings)
        return account

    # ---- tokens and calls ---------------------------------------------------------
    def _access_token(self, workspace, used=None):
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = store.secret(workspace, PROVIDER)
            if not sec:
                raise AuthExpired("HubSpot is not connected")
            if sec.get("token"):
                if used == sec["token"]:
                    raise AuthExpired("HubSpot refused the private app token")
                return sec["token"]
            stale = sec.get("renew_at", 0) <= time.time()
            if not stale and (used is None or sec["access_token"] != used):
                return sec["access_token"]
            if not sec.get("refresh_token"):
                raise AuthExpired("HubSpot refused the token and there is no refresh token")
            sec = self._tokens(self._token_call({"grant_type": "refresh_token",
                                                 "refresh_token": sec["refresh_token"]}), sec)
            store.update(workspace, PROVIDER, secret=sec)
            return sec["access_token"]

    def _call(self, token, url, params=None):
        if not url.startswith(API + "/"):
            raise ConfigError("a link outside HubSpot's knowledge base API")
        r = http.request("GET", url, params=params, bucket=http.bucket_key(PROVIDER, token),
                         rate=RATE, burst=RATE,
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/json"})
        try:
            return r.json()
        except ValueError:
            raise ConfigError("the answer isn't JSON") from None

    def _get(self, ws, url, params=None):
        token = self._access_token(ws)
        try:
            return self._call(token, url, params)
        except AuthExpired:
            return self._call(self._access_token(ws, used=token), url, params)

    def _pages(self, ws, path, params=None):
        params = {"limit": PAGE, **(params or {})}
        while True:
            data = self._get(ws, f"{API}{path}", params)
            yield from data.get("results") or []
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return
            params = {**params, "after": after}

    # ---- picking ------------------------------------------------------------------
    def _categories(self, ws):
        return {str(c["id"]): c for c in self._pages(ws, "/categories")}

    def list_containers(self, ctx, parent=None):
        ws = ctx.workspace
        if not parent:
            return sorted(({"id": f"kb-{k['id']}", "name": k.get("name") or k.get("domain")
                            or "Knowledge base", "kind": "category", "has_children": True}
                           for k in self._pages(ws, "/knowledge-bases")),
                          key=lambda c: c["name"].lower())
        kind, _, pid = parent.partition("-")
        if kind not in ("kb", "category") or not pid.isdigit():
            return []
        if kind == "kb":
            mine = [c for c in self._pages(ws, "/categories", {"knowledgeBaseIds": pid})
                    if not c.get("parentCategoryId")]
        else:
            mine = list(self._pages(ws, "/categories", {"parentCategoryIds": pid}))
        parents = {str(c.get("parentCategoryId")) for c in self._categories(ws).values()
                   if c.get("parentCategoryId")}
        return sorted(({"id": f"category-{c['id']}", "name": c.get("name") or "Untitled",
                        "kind": "section", "has_children": str(c["id"]) in parents}
                       for c in mine), key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            kind, _, pid = p["id"].partition("-")
            if kind not in ("kb", "category") or not pid.isdigit():
                raise FormError("Pick knowledge bases or categories.")
            p["kind"] = "category" if kind == "kb" else "section"
        return out

    @staticmethod
    def _in_picks(article, picks, cats):
        if not picks:
            return True
        if f"kb-{article.get('knowledgeBaseId')}" in picks:
            return True
        seen, cid = set(), str(article["categoryId"]) if article.get("categoryId") else None
        while cid and cid not in seen:
            seen.add(cid)
            if f"category-{cid}" in picks:
                return True
            c = cats.get(cid) or {}
            cid = str(c["parentCategoryId"]) if c.get("parentCategoryId") else None
        return False

    # ---- syncing ------------------------------------------------------------------
    def _item(self, a, cats):
        cat = cats.get(str(a.get("categoryId"))) or {}
        return Item(source_id=f"article-{a['id']}", title=a.get("title") or "Untitled",
                    url=a.get("url") or "", version=a.get("updatedAt") or "", kind="article",
                    mime="text/html", modified=a.get("updatedAt"),
                    parent_id=f"category-{a['categoryId']}" if a.get("categoryId") else None,
                    acl=access_acl(a),
                    attrs={"id": str(a["id"]), "category": cat.get("name"),
                           "language": a.get("language"),
                           "knowledge_base": str(a.get("knowledgeBaseId") or "")},
                    content=article_html(a.get("body") or ""))

    def changes(self, ctx, cursor):
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        picks = sorted(p["id"] for p in (ctx.settings or {}).get("containers") or [])
        full = (ctx.full or not state or state.get("picks") != picks
                or time.time() - state.get("full_at", 0) > RECONCILE)
        since = None if full else _since(state.get("watermark"))
        full = full or since is None
        cats = self._categories(ws)
        params = {"includeTranslations": "false", "sort": "updatedAt"}
        params.update({"state": "PUBLISHED"} if full else {"updatedAfter": since})
        newest, seen, n = state.get("watermark") or "", set(), 0
        for a in self._pages(ws, "/articles", params):
            newest = max(newest, a.get("updatedAt") or "")
            sid = f"article-{a['id']}"
            live = a.get("state") == "PUBLISHED" and not a.get("archivedInDashboard")
            if not live or not self._in_picks(a, set(picks), cats):
                if sid in ctx.known and not full:
                    yield Delete(sid)             # unpublished, archived or moved out
                continue
            if sid in seen:
                continue
            if ctx.max_items and n >= ctx.max_items:
                yield LimitReached(ctx.max_items)
                yield Cursor(cursor)
                return
            seen.add(sid)
            n += 1
            yield Upsert(self._item(a, cats))
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"picks": picks, "watermark": newest,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def disconnect(self, workspace):
        """HubSpot documents no revoke call: the stored tokens are deleted here, and
        an admin uninstalls the app in HubSpot (Settings → Integrations → Connected
        apps) or deletes the private app to cut access at the source."""
        log.info("HubSpot: disconnected workspace %s; uninstall the app in HubSpot to revoke "
                 "its access there", workspace)


CONNECTOR = HubSpot()
