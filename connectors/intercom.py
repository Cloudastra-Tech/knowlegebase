"""
connectors/intercom.py  —  sync a workspace's Intercom Help Center articles
(and, if an admin turns them on, its closed conversations) into its index.

Connecting, two ways (one card):
  Access token   of a PRIVATE app in the customer's own Intercom workspace
                 (Developer Hub → your app → Authentication). Private apps need no
                 review by Intercom: the way to start.
  OAuth          only when the server has INTERCOM_CLIENT_ID / INTERCOM_CLIENT_SECRET:
                 a PUBLIC app, which Intercom reviews before any other workspace can
                 install it (listed or unlisted), with the "Read articles" (and, for
                 conversations, "Read conversations") permissions and the redirect URL
                 <PUBLIC_URL>/api/integrations/intercom/callback. The admin signs in on
                 their REGION's Intercom (US, EU or Australia: the form asks, because
                 signing in on another region's host can fail).
Intercom's OAuth answer has no refresh token or expiry (token_type Bearer,
`token` / `access_token`); a 401 later means the app was uninstalled or the
token revoked: reconnect. GET /me says which REGION the workspace is hosted in
(app.region), and every call goes to that region's API host.

What is synced:
  ARTICLES (state published; drafts are left out), their HTML body turned into
  text by connectors/extract.py. Help Centers and their collections are the
  containers: an admin may pick some; with none picked every published article
  is synced (also ones in no collection). Captured into each article's acl:
  {"via": "intercom", "kind": "article", "help_center_live": bool, ...}. Intercom's
  API does NOT expose an article's audience targeting (who may see it in the
  Help Center is set per article in Intercom's UI), so an article's readers
  can't be known: Intercom offers no "as shared at the source" policy, and the
  acl maps to staff (kb/services/permissions.py) if met under one anyway.
  CONVERSATIONS, optional and OFF by default (the picker's switch): CLOSED
  conversations with their customer and teammate replies (internal notes stay
  in Intercom). They hold customers' personal data, so while they are on the
  connection's access is locked to HR and admins (settings["access_lock"]).

How a sync finds what changed:
  articles   GET /articles returns articles NEWEST UPDATE FIRST (updated_at, Unix
             seconds). FULL (first run, picks changed, every RECONCILE seconds,
             asked for): every page; anything known but not listed was deleted
             and goes behind the mass-delete guard. INCREMENTAL: pages until an
             article older than the last run's newest updated_at (less OVERLAP);
             an article unpublished since (state draft) is deleted.
  conversations  POST /conversations/search for state = closed AND updated_at >
             the watermark (cursor pages, starting_after); a conversation reopened
             since is deleted. A first sync goes back CONVERSATIONS_BACKFILL_DAYS
             (INTERCOM_CONVERSATIONS_BACKFILL_DAYS, default 365); every RECONCILE the
             whole window is searched again and a known conversation not found
             (deleted in Intercom) goes behind the mass-delete guard.
Rate limits: 10,000 calls a minute per app (private apps share one 10,000 per
workspace), 25,000 per workspace, counted in 10-second windows; a 429 says when
the window ends in X-RateLimit-Reset (Unix seconds), which connectors/http.py
waits for. Calls are spaced to RATE a second per token.

API references (checked 2026-10-27; API version 2.14, sent as Intercom-Version):
  OpenAPI         https://github.com/intercom/Intercom-OpenAPI/blob/main/descriptions/2.14/api.intercom.io.yaml
                  (article_list_item: id, title, body (HTML), state published | draft,
                  updated_at, url, parent_id, parent_ids, parent_type; collection: id, name,
                  parent_id, help_center_id; help_center: id, display_name, website_turned_on,
                  url; admin_with_app.app.region; conversation_part: part_type (comment, note,
                  ...), body, created_at, author {type, name}; starting_after_paging)
  list articles   https://developers.intercom.com/docs/references/rest-api/api.intercom.io/articles/listarticles
                  (GET /articles, page, per_page up to 250; "descending order on the
                  updated_at attribute"; pages.page / total_pages)
  collections     https://developers.intercom.com/docs/references/rest-api/api.intercom.io/help-center/listallcollections
                  (GET /help_center/collections, per_page up to 250; read_articles_scope;
                  servers api.intercom.io, api.eu.intercom.io, api.au.intercom.io)
  search          https://developers.intercom.com/docs/references/rest-api/api.intercom.io/conversations/searchconversations
                  (POST /conversations/search; operators > < on updated_at (Unix); per_page
                  up to 150)
                  https://developers.intercom.com/docs/build-an-integration/learn-more/rest-apis/pagination-sorting-search
                  (pagination.starting_after = pages.next.starting_after)
  conversation    https://developers.intercom.com/docs/references/rest-api/api.intercom.io/conversations/retrieveconversation
                  (GET /conversations/{id}; display_as=plaintext; at most the 500 most
                  recent parts)
  OAuth           https://developers.intercom.com/docs/build-an-integration/learn-more/authentication/setting-up-oauth
                  (https://app{.eu|.au}.intercom.com/oauth: client_id, state, redirect_uri;
                  POST https://api.intercom.io/auth/eagle/token: code, client_id,
                  client_secret -> token, access_token, token_type Bearer)
  regions         https://developers.intercom.com/building-apps/docs/rest-apis
  rate limits     https://developers.intercom.com/docs/references/rest-api/errors/rate-limiting
  app review      https://developers.intercom.com/building-apps/docs/listing-your-app
  audience        https://www.intercom.com/help/en/articles/2982784-control-who-can-see-your-articles
                  (audience targeting is set per article in Intercom, not by the API)
"""

import datetime
import json
import logging
import os
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Delete, Fetched,
                             FormError, Item, LimitReached, Upsert)
from connectors.zendesk import article_html
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

PROVIDER = "intercom"
VERSION = "2.14"
TOKEN_URL = "https://api.intercom.io/auth/eagle/token"
HOSTS = {"US": "https://api.intercom.io", "EU": "https://api.eu.intercom.io",
         "AU": "https://api.au.intercom.io"}
SIGN_IN = {"US": "https://app.intercom.com/oauth", "EU": "https://app.eu.intercom.com/oauth",
           "AU": "https://app.au.intercom.com/oauth"}
RATE = 10                             # calls a second per token (Intercom allows ~160)
PAGE = 250                            # articles / collections a page (Intercom's maximum)
SEARCH_PAGE = 150                     # conversations a page (Intercom's maximum)
RECONCILE = 24 * 60 * 60
OVERLAP = 120
CONVERSATIONS_BACKFILL_DAYS = 365
SPOKEN_PARTS = ("comment", "open", "close", "assign_and_reopen", "quick_reply")


def region(value):
    v = (value or "US").strip().upper()
    v = {"AUSTRALIA": "AU", "EUROPE": "EU", "USA": "US"}.get(v, v)
    if v not in HOSTS:
        raise FormError("Pick the region your Intercom workspace is hosted in: US, EU or "
                        "Australia.")
    return v


class Intercom(Connector):
    PROVIDER = PROVIDER
    LABEL = "Intercom"
    DESCRIPTION = "Help Center articles; closed conversations if you turn them on"
    AUTH = "token"
    PICKER = "tree"
    PICKER_REQUIRED = False
    UNIT = "articles"
    ACL = False
    SOURCE_POLICY_NOTE = ("Intercom's API doesn't say who each article's audience targeting "
                          "lets see it, so it can't follow Intercom's own sharing.")

    # ---- connecting ---------------------------------------------------------------
    def can_sign_in(self):
        return bool(os.getenv("INTERCOM_CLIENT_ID") and os.getenv("INTERCOM_CLIENT_SECRET"))

    def connect_start(self, redirect_uri, state, form):
        where = region(form.get("url") if isinstance(form, dict) else form)
        url = SIGN_IN[where] + "?" + urllib.parse.urlencode({
            "client_id": os.environ["INTERCOM_CLIENT_ID"], "state": state,
            "redirect_uri": redirect_uri})
        return url, {"region": where}

    def finish_connect(self, workspace, code, redirect_uri, user, step=None):
        r = http.request("POST", TOKEN_URL, retries=1, ok=(400, 401), json={
            "code": code, "client_id": os.environ.get("INTERCOM_CLIENT_ID", ""),
            "client_secret": os.environ.get("INTERCOM_CLIENT_SECRET", "")})
        if r.status_code >= 400:
            raise ConfigError(f"Intercom refused the code (HTTP {r.status_code})")
        t = r.json()
        token = t.get("access_token") or t.get("token")
        if not token:
            raise ConfigError("Intercom returned no token")
        self._save(workspace, token, self._me(token), user, oauth=True)

    def connect_form(self, workspace, form, user):
        token = (form.get("token") or "").strip()
        if not token:
            raise FormError("Paste your private app's access token (Intercom Developer Hub → "
                            "your app → Authentication).")
        try:
            me = self._me(token)
        except AuthExpired:
            raise FormError("Intercom didn't accept that access token.") from None
        except FormError:
            raise
        except Exception:
            raise FormError("Couldn't reach Intercom.") from None
        return self._save(workspace, token, me, user)

    def _me(self, token):
        """Who the token is, and where its workspace is hosted. api.intercom.io
        routes to the right region by itself; later calls go to it directly."""
        r = http.request("GET", f"{HOSTS['US']}/me", retries=1, headers=_headers(token))
        me = r.json() or {}
        if not me.get("id"):
            raise AuthExpired("no admin behind the token")
        return me

    def _save(self, workspace, token, me, user, oauth=False):
        app = me.get("app") or {}
        where = (app.get("region") or "US").upper()
        where = where if where in HOSTS else "US"
        account = f"{app.get('name') or 'Intercom'} ({me.get('email') or me.get('name') or 'admin'})"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, {"access_token": token, "oauth": oauth}, user)
        settings = dict((row or {}).get("settings") or {}, region=where,
                        app=app.get("id_code") or "")
        if row and (row.get("settings") or {}).get("app") != settings["app"]:
            settings["containers"] = []           # another workspace: other collections
        store.update(workspace, PROVIDER, settings=settings)
        return account

    # ---- calling ------------------------------------------------------------------
    def _host(self, ws):
        row = store.get(ws, PROVIDER)
        if not row:
            raise AuthExpired("Intercom is not connected")
        return HOSTS.get(((row.get("settings") or {}).get("region") or "US"), HOSTS["US"])

    def _call(self, ws, method, path, params=None, body=None):
        sec = store.secret(ws, PROVIDER)
        if not sec or not sec.get("access_token"):
            raise AuthExpired("Intercom is not connected")
        token = sec["access_token"]
        r = http.request(method, f"{self._host(ws)}{path}", params=params, json=body,
                         bucket=http.bucket_key(PROVIDER, token), rate=RATE, burst=RATE,
                         headers=_headers(token))
        try:
            return r.json()
        except ValueError:
            raise ConfigError("the answer isn't JSON") from None

    def _pages(self, ws, path, params=None):
        """Every record of a numbered-page listing (pages.page / total_pages)."""
        page = 1
        while True:
            data = self._call(ws, "GET", path, {"per_page": PAGE, "page": page, **(params or {})})
            yield from data.get("data") or []
            pages = data.get("pages") or {}
            if not pages.get("total_pages") or page >= int(pages["total_pages"]):
                return
            page += 1

    # ---- picking ------------------------------------------------------------------
    def _help_centers(self, ws):
        return {str(h["id"]): h for h in
                self._call(ws, "GET", "/help_center/help_centers").get("data") or []}

    def _collections(self, ws):
        return {str(c["id"]): c for c in self._pages(ws, "/help_center/collections")}

    def list_containers(self, ctx, parent=None):
        ws = ctx.workspace
        if not parent:
            return sorted(({"id": f"hc-{h['id']}", "name": h.get("display_name")
                            or h.get("identifier") or "Help Center", "kind": "category",
                            "has_children": True}
                           for h in self._help_centers(ws).values()),
                          key=lambda c: c["name"].lower())
        kind, _, pid = parent.partition("-")
        cols = list(self._collections(ws).values())
        parents = {str(c.get("parent_id")) for c in cols if c.get("parent_id")}
        if kind == "hc":
            mine = [c for c in cols if str(c.get("help_center_id")) == pid
                    and not c.get("parent_id")]
        else:
            mine = [c for c in cols if str(c.get("parent_id")) == pid]
        return sorted(({"id": f"collection-{c['id']}", "name": c.get("name") or "Untitled",
                        "kind": "section", "has_children": str(c["id"]) in parents}
                       for c in mine), key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            kind, _, pid = p["id"].partition("-")
            if kind not in ("hc", "collection") or not pid.isdigit():
                raise FormError("Pick Help Centers or collections.")
            p["kind"] = "category" if kind == "hc" else "section"
        return out

    def check_options(self, options, settings):
        on = bool((options or {}).get("conversations"))
        return {"conversations": on, "access_lock": "restricted" if on else None}

    def options_of(self, settings):
        return {"conversations": bool((settings or {}).get("conversations"))}

    @staticmethod
    def _parents(article):
        ids = article.get("parent_ids")
        if ids is None:
            ids = [article["parent_id"]] if article.get("parent_id") else []
        return [str(i) for i in ids]

    def _place(self, article, cols):
        """(help center id, every collection the article is in, with those above)."""
        found, hc = set(), None
        for cid in self._parents(article):
            seen = set()
            while cid and cid not in seen:
                seen.add(cid)
                found.add(cid)
                c = cols.get(cid) or {}
                hc = hc or (str(c["help_center_id"]) if c.get("help_center_id") else None)
                cid = str(c["parent_id"]) if c.get("parent_id") else None
        return hc, found

    # ---- syncing ------------------------------------------------------------------
    def _article_item(self, a, cols, centers):
        hc, inside = self._place(a, cols)
        center = centers.get(hc or "") or {}
        first = cols.get((self._parents(a) or [""])[0]) or {}
        updated = str(a.get("updated_at") or "")
        acl = {"via": PROVIDER, "kind": "article", "state": a.get("state"),
               "help_center": hc, "help_center_live": bool(center.get("website_turned_on")),
               "collections": sorted(inside), "audience": "not_exposed"}
        return Item(source_id=f"article-{a['id']}", title=a.get("title") or "Untitled",
                    url=a.get("url") or "", version=updated, kind="article", mime="text/html",
                    modified=_iso(a.get("updated_at")),
                    parent_id=f"collection-{first['id']}" if first.get("id") else None,
                    acl=acl, attrs={"id": str(a["id"]), "collection": first.get("name"),
                                    "help_center": center.get("display_name")},
                    content=article_html(a.get("body") or ""))

    def _conversations(self, ws, since):
        """Closed conversations updated after `since` (Unix seconds), oldest pages
        first as Intercom returns them."""
        body = {"query": {"operator": "AND", "value": [
            {"field": "state", "operator": "=", "value": "closed"},
            {"field": "updated_at", "operator": ">", "value": int(since)}]},
            "pagination": {"per_page": SEARCH_PAGE}}
        while True:
            data = self._call(ws, "POST", "/conversations/search", body=body)
            yield from data.get("conversations") or []
            after = ((data.get("pages") or {}).get("next") or {}).get("starting_after")
            if not after:
                return
            body = {**body, "pagination": {"per_page": SEARCH_PAGE, "starting_after": after}}

    def _conversation_item(self, ws, c):
        source = c.get("source") or {}
        title = c.get("title") or source.get("subject") or f"Conversation {c['id']}"
        settings = (store.get(ws, PROVIDER) or {}).get("settings") or {}
        app, where = settings.get("app") or "", settings.get("region") or "US"
        # The teammate inbox's link (Intercom's web app, not its API).
        url = (f"{SIGN_IN.get(where, SIGN_IN['US']).removesuffix('/oauth')}/a/inbox/{app}/inbox/"
               f"conversation/{c['id']}" if app else "")
        return Item(source_id=f"conversation-{c['id']}", title=f"#{c['id']}: {title}",
                    url=url, version=str(c.get("updated_at") or ""), kind="ticket",
                    mime="text/markdown", modified=_iso(c.get("updated_at")),
                    acl={"via": PROVIDER, "kind": "conversation"},
                    attrs={"id": str(c["id"]), "state": c.get("state")}, content=c)

    def changes(self, ctx, cursor):
        ws = ctx.workspace
        settings = ctx.settings or {}
        state = json.loads(cursor) if cursor else {}
        picks = sorted(p["id"] for p in settings.get("containers") or [])
        now = time.time()
        full = (ctx.full or not state or state.get("picks") != picks
                or now - state.get("full_at", 0) > RECONCILE)
        stop = None if full else state.get("watermark")
        full = full or stop is None
        cols, centers = self._collections(ws), self._help_centers(ws)
        pick_set = set(picks)
        newest, seen, n = state.get("watermark") or 0, set(), 0

        def picked(a):
            if not pick_set:
                return True
            hc, inside = self._place(a, cols)
            return f"hc-{hc}" in pick_set or bool({f"collection-{c}" for c in inside} & pick_set)

        def over_limit():
            return ctx.max_items and n >= ctx.max_items

        done = False
        page = 1
        while not done:
            data = self._call(ws, "GET", "/articles", {"per_page": PAGE, "page": page})
            for a in data.get("data") or []:
                updated = int(a.get("updated_at") or 0)
                if stop is not None and updated < int(stop) - OVERLAP:
                    done = True                   # newest first: the rest is older
                    break
                newest = max(newest, updated)
                sid = f"article-{a['id']}"
                if a.get("state") != "published" or not picked(a):
                    if sid in ctx.known and not full:
                        yield Delete(sid)         # unpublished or moved out of the picks
                    continue
                if sid in seen:
                    continue
                if over_limit():
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)
                    return
                seen.add(sid)
                n += 1
                yield Upsert(self._article_item(a, cols, centers))
            pages = data.get("pages") or {}
            if not pages.get("total_pages") or page >= int(pages["total_pages"]):
                done = True
            page += 1
        if full:
            yield from ctx.missing(seen | {s for s in ctx.known if not s.startswith("article-")})

        conv = {k: state.get(k) for k in ("conversations_from", "conversations_at")}
        if settings.get("conversations"):
            days = env_int("INTERCOM_CONVERSATIONS_BACKFILL_DAYS", CONVERSATIONS_BACKFILL_DAYS,
                           minimum=1)
            start = conv.get("conversations_from") or int(now - days * 86400)
            since = start if full or not conv.get("conversations_at") else max(
                start, conv["conversations_at"] - OVERLAP)
            latest, found = conv.get("conversations_at") or start, set()
            for c in self._conversations(ws, since):
                latest = max(latest, int(c.get("updated_at") or 0))
                sid = f"conversation-{c['id']}"
                if over_limit():
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)
                    return
                found.add(sid)
                n += 1
                yield Upsert(self._conversation_item(ws, c))
            if full:
                for sid in sorted(ctx.known):
                    if sid.startswith("conversation-") and sid not in found:
                        yield Delete(sid)         # deleted or reopened in Intercom
            conv = {"conversations_from": start, "conversations_at": latest}
        else:
            conv = {}
            for sid in sorted(ctx.known):
                if sid.startswith("conversation-"):
                    yield Delete(sid)             # conversations were turned off
        yield Cursor(json.dumps({"picks": picks, "watermark": newest,
                                 "full_at": now if full else state.get("full_at", 0), **conv}),
                     full=full)

    def fetch(self, ctx, item):
        if item.kind != "ticket":
            return super().fetch(ctx, item)
        c = self._call(ctx.workspace, "GET", f"/conversations/{item.attrs['id']}",
                       {"display_as": "plaintext"})
        if c.get("state") != "closed":
            return None                           # reopened since it was listed
        source = c.get("source") or {}
        facts = [("State", c.get("state")),
                 ("Created", (_iso(c.get("created_at")) or "")[:10] or None),
                 ("Updated", (_iso(c.get("updated_at")) or "")[:10] or None)]
        parts = ["\n".join(f"{k}: {v}" for k, v in facts if v)]
        lines = []
        if (source.get("body") or "").strip():
            lines.append(f"### {_who(source.get('author'))}, "
                         f"{(_iso(c.get('created_at')) or '')[:10]}\n\n{source['body'].strip()}")
        for p in (c.get("conversation_parts") or {}).get("conversation_parts") or []:
            if p.get("part_type") not in SPOKEN_PARTS or not (p.get("body") or "").strip():
                continue                          # internal notes, assignments, events
            lines.append(f"### {_who(p.get('author'))}, {(_iso(p.get('created_at')) or '')[:10]}"
                         f"\n\n{p['body'].strip()}")
        if lines:
            parts.append("## Conversation\n\n" + "\n\n".join(lines))
        return Fetched("\n\n".join(p for p in parts if p), "text/markdown")

    def disconnect(self, workspace):
        """Intercom documents no revoke call for these tokens: the stored token is
        deleted here; an admin uninstalls the app in Intercom (Settings → Apps) or
        revokes the private app's token to cut access at the source."""
        log.info("Intercom: disconnected workspace %s; uninstall the app in Intercom to "
                 "revoke its access there", workspace)


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json",
            "Intercom-Version": VERSION}


def _iso(seconds):
    try:
        return datetime.datetime.fromtimestamp(int(seconds), datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _who(author):
    a = author or {}
    kind = {"admin": "Teammate", "bot": "Bot", "user": "Customer", "lead": "Customer",
            "contact": "Customer"}.get(a.get("type"), "Someone")
    return kind if kind == "Customer" else f"{a.get('name') or kind}"


CONNECTOR = Intercom()
