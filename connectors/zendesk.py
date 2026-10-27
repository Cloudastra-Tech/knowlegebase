"""
connectors/zendesk.py  —  sync a workspace's Zendesk Help Center (and, if an
admin turns it on, its solved tickets) into its index.

Connecting, two ways (one card, the same form asks for the Zendesk subdomain):
  API token      an agent's or admin's email address and an API token (Admin
                 Center → Apps and integrations → APIs → Zendesk API), sent as
                 Basic `{email}/token:{api_token}`: for a customer's own instance.
  OAuth          only when the server has ZENDESK_CLIENT_ID / ZENDESK_CLIENT_SECRET:
                 the admin signs in on their Zendesk's consent screen. Serving
                 MANY customers' instances needs a GLOBAL OAuth client, which only
                 Zendesk grants (a local client whose identifier starts with zdg-,
                 submitted from a d3v- sponsored test subdomain through the
                 Marketplace portal): deploy.md, "Zendesk".

What is synced:
  Help Center ARTICLES (published; drafts are left out), their body HTML turned
  into text by connectors/extract.py (code blocks fenced). Categories and
  sections are the containers: an admin may pick some on the card; with none
  picked the whole Help Center is synced. Each article's user segments (who
  may view it in the Help Center) are CAPTURED into its `acl`, not enforced:
  no segment = everyone; "signed_in_users" without group, organisation or tag
  conditions = everyone signed in; anything narrower ("staff", groups,
  organisations, tags, or a segment we can't read) maps to the staff tier.
  TICKETS, optional and OFF by default (the picker's "solved tickets" switch):
  solved and closed tickets with their public comments. They hold customers'
  personal data, so while they are on the connection's access is locked to
  HR and admins (settings["access_lock"], integrations_store.access_of) and
  each ticket's acl says "staff".

How a sync finds what changed:
  articles   FULL (first run, picks changed, every RECONCILE seconds, asked for):
             every article is listed (GET /help_center/articles, cursor pages);
             anything known but not listed was archived or deleted and goes
             behind the mass-delete guard. INCREMENTAL: the incremental article
             export from the last run's end_time (start_time; up to 1,000 a page,
             the next page's start_time is this page's end_time).
  tickets    the cursor-based incremental ticket export (start_time once, then
             the after_cursor), which also returns deleted tickets; at most
             10 export calls a minute (30 with the High Volume add-on), so its
             calls have their own slow bucket. A first sync goes back
             TICKETS_BACKFILL_DAYS (ZENDESK_TICKETS_BACKFILL_DAYS, default 365).

API references (checked 2026-10-27):
  articles        https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/
                  (GET /api/v2/help_center/articles, GET /api/v2/help_center/incremental/articles
                  ?start_time; user_segment_id null / user_segment_ids empty = everyone)
  incremental     https://developer.zendesk.com/documentation/help_center/help-center-api/understanding-incremental-article-exports/
                  (up to 1,000 per page; next start time = the page's end_time; agents and admins)
  sections        https://developer.zendesk.com/api-reference/help_center/help-center-api/sections/
  categories      https://developer.zendesk.com/api-reference/help_center/help-center-api/categories/
  user segments   https://developer.zendesk.com/api-reference/help_center/help-center-api/user_segments/
                  (List: agents and managers; user_type signed_in_users | staff; group_ids,
                  organization_ids, tags, or_tags)
  ticket export   https://developer.zendesk.com/api-reference/ticketing/ticket-management/incremental_exports/
                  (GET /api/v2/incremental/tickets/cursor: start_time at least a minute ago,
                  then cursor; {tickets, after_cursor, end_of_stream}; admins only)
  comments        https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_comments/
                  (GET /api/v2/tickets/{id}/comments, at most 100 a page; public, plain_body)
  pagination      https://developer.zendesk.com/api-reference/introduction/pagination/
                  (page[size] up to 100, meta.has_more, links.next)
  rate limits     https://developer.zendesk.com/api-reference/introduction/rate-limits/
                  (429 + Retry-After seconds; incremental exports 10 / minute)
  auth            https://developer.zendesk.com/api-reference/introduction/security-and-auth/
  OAuth           https://support.zendesk.com/hc/en-us/articles/4408845965210
                  (GET https://{subdomain}.zendesk.com/oauth/authorizations/new: response_type,
                  redirect_uri, client_id, scope, state; POST /oauth/tokens with JSON)
                  https://developer.zendesk.com/api-reference/ticketing/oauth/grant_type_tokens/
                  (refresh_token grant; refreshing ROTATES both tokens; expires_in)
  global client   https://developer.zendesk.com/documentation/marketplace/building-a-marketplace-app/set-up-a-global-oauth-client/
"""

import base64
import json
import logging
import os
import re
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, ConnectRefused, Connector, Cursor,
                             Delete, Fetched, Forbidden, FormError, Item, LimitReached, Upsert)
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

PROVIDER = "zendesk"
# Read-only scopes: Help Center, tickets (only used when tickets are on) and the
# connecting user (users/me). ZENDESK_SCOPES overrides them (a global client's
# allowed scopes are set by Zendesk: asking for more fails with invalid_scope).
SCOPES = "hc:read tickets:read users:read"

RATE = 5                              # calls a second per connection
EXPORT_RATE = 10 / 60                 # incremental exports: 10 a minute
RECONCILE = 24 * 60 * 60              # seconds between full article listings
EXPORT_PAGE = 1000                    # the article export's page size
TICKETS_BACKFILL_DAYS = 365
SYNCED_STATUSES = ("solved", "closed")
MAX_COMMENTS = 500                    # per ticket
_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PRE = re.compile(r"<pre\b[^>]*>(.*?)</pre>", re.S | re.I)


def subdomain(value):
    """The subdomain from what an admin typed: "acme", "acme.zendesk.com" or
    "https://acme.zendesk.com/hc/en-us"."""
    v = (value or "").strip().lower()
    if "//" in v:
        v = urllib.parse.urlsplit(v).hostname or ""
    v = v.split("/")[0]
    if v.endswith(".zendesk.com"):
        v = v[:-len(".zendesk.com")]
    if not _SUBDOMAIN.match(v):
        raise FormError("Enter your Zendesk subdomain: acme for acme.zendesk.com.")
    return v


def article_html(body):
    """An article's HTML for extract.py, with code blocks fenced (```) so
    chunking.py keeps them whole."""
    return _PRE.sub(lambda m: "<pre>\n```\n" + m.group(1).strip("\n") + "\n```\n</pre>",
                    body or "")


def segment_acl(article, segments):
    """Who may view an article in the Help Center, from its user segments."""
    ids = article.get("user_segment_ids")
    if ids is None:
        ids = [article["user_segment_id"]] if article.get("user_segment_id") else []
    if not ids:
        return {"mode": "workspace", "everyone": True, "segments": []}
    found, mode = [], "staff"
    for i in ids:
        seg = segments.get(str(i))
        if not seg:
            found.append({"id": str(i), "unknown": True})
            continue
        entry = {k: seg.get(k) for k in ("name", "user_type", "group_ids", "organization_ids",
                                         "tags", "or_tags")}
        found.append({"id": str(i), **entry})
        narrowed = any(seg.get(k) for k in ("group_ids", "organization_ids", "tags", "or_tags"))
        if seg.get("user_type") == "signed_in_users" and not narrowed:
            mode = "workspace"            # segments are OR-ed: every signed-in user sees it
    return {"mode": mode, "everyone": False, "segments": found}


class Zendesk(Connector):
    PROVIDER = PROVIDER
    LABEL = "Zendesk"
    DESCRIPTION = "Help Center articles; solved tickets if you turn them on"
    AUTH = "token"
    PICKER = "tree"
    PICKER_REQUIRED = False
    UNIT = "articles"
    ACL = True
    NOTE = ""

    # ---- connecting ---------------------------------------------------------------
    def can_sign_in(self):
        return bool(os.getenv("ZENDESK_CLIENT_ID") and os.getenv("ZENDESK_CLIENT_SECRET"))

    def _scopes(self):
        return os.getenv("ZENDESK_SCOPES") or SCOPES

    def connect_start(self, redirect_uri, state, form):
        sub = subdomain(form.get("url") if isinstance(form, dict) else form)
        url = f"https://{sub}.zendesk.com/oauth/authorizations/new?" + urllib.parse.urlencode({
            "response_type": "code", "redirect_uri": redirect_uri,
            "client_id": os.environ["ZENDESK_CLIENT_ID"], "scope": self._scopes(),
            "state": state})
        return url, {"subdomain": sub}

    def finish_connect(self, workspace, code, redirect_uri, user, step=None):
        sub = (step or {}).get("subdomain")
        if not sub:
            raise ConnectRefused("connect_failed", "no subdomain in the sign-in state")
        r = http.request("POST", f"https://{sub}.zendesk.com/oauth/tokens", retries=1,
                         ok=(400, 401, 403), json={
                             "grant_type": "authorization_code", "code": code,
                             "client_id": os.environ.get("ZENDESK_CLIENT_ID", ""),
                             "client_secret": os.environ.get("ZENDESK_CLIENT_SECRET", ""),
                             "redirect_uri": redirect_uri, "scope": self._scopes()})
        if r.status_code >= 400:
            raise ConfigError(f"Zendesk refused the code (HTTP {r.status_code})")
        t = r.json()
        sec = {"access_token": t["access_token"], "refresh_token": t.get("refresh_token"),
               "expires_at": time.time() + int(t["expires_in"]) - 60 if t.get("expires_in")
               else None}
        me = self._me(sub, {"Authorization": f"Bearer {t['access_token']}"})
        self._save(workspace, sub, sec, me, user)

    def connect_form(self, workspace, form, user):
        """An agent's or admin's email and API token: checked with GET /users/me,
        then saved (encrypted)."""
        sub = subdomain(form.get("url"))
        email, token = (form.get("email") or "").strip(), (form.get("token") or "").strip()
        if not email or "@" not in email:
            raise FormError("Enter the email address of the Zendesk agent or admin the token "
                            "belongs to.")
        if not token:
            raise FormError("Paste an API token (Admin Center → Apps and integrations → APIs "
                            "→ Zendesk API).")
        try:
            me = self._me(sub, {"Authorization": _basic(email, token)})
        except AuthExpired:
            raise FormError("Zendesk didn't accept that email and token.") from None
        except FormError:
            raise
        except Exception:
            raise FormError(f"Couldn't reach {sub}.zendesk.com.") from None
        return self._save(workspace, sub, {"email": email, "token": token}, me, user)

    def _me(self, sub, headers):
        r = http.request("GET", f"https://{sub}.zendesk.com/api/v2/users/me", retries=1,
                         headers={**headers, "Accept": "application/json"})
        me = (r.json() or {}).get("user") or {}
        if not me.get("id"):
            raise AuthExpired("anonymous")
        if me.get("role") not in ("admin", "agent"):
            raise FormError("That account isn't a Zendesk agent or admin.")
        return me

    def _save(self, workspace, sub, sec, me, user):
        account = f"{sub}.zendesk.com ({me.get('name') or me.get('email') or 'Zendesk'})"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, sec, user)
        settings = dict((row or {}).get("settings") or {}, subdomain=sub)
        if row and (row.get("settings") or {}).get("subdomain") != sub:
            settings["containers"] = []           # another Help Center: other sections
        store.update(workspace, PROVIDER, settings=settings)
        return account

    # ---- calling ------------------------------------------------------------------
    def _base(self, ws):
        row = store.get(ws, PROVIDER)
        sub = ((row or {}).get("settings") or {}).get("subdomain")
        if not row or not sub:
            raise AuthExpired("Zendesk is not connected")
        return f"https://{sub}.zendesk.com"

    def _auth(self, ws, force=False):
        """The Authorization header: Basic for an API token; Bearer for OAuth,
        refreshed (rotating both tokens) under a lock when it has expired."""
        with http.refresh_lock(f"{PROVIDER}:{ws}"):
            sec = store.secret(ws, PROVIDER)
            if not sec:
                raise AuthExpired("Zendesk is not connected")
            if sec.get("token"):
                return _basic(sec["email"], sec["token"])
            due = sec.get("expires_at") and sec["expires_at"] <= time.time()
            if not (force or due):
                return f"Bearer {sec['access_token']}"
            if not sec.get("refresh_token"):
                raise AuthExpired("the Zendesk token expired and has no refresh token")
            r = http.request("POST", f"{self._base(ws)}/oauth/tokens", retries=1,
                             ok=(400, 401, 403), json={
                                 "grant_type": "refresh_token",
                                 "refresh_token": sec["refresh_token"],
                                 "client_id": os.environ.get("ZENDESK_CLIENT_ID", ""),
                                 "client_secret": os.environ.get("ZENDESK_CLIENT_SECRET", "")})
            if r.status_code >= 400:
                raise AuthExpired("Zendesk refused the refresh token")
            t = r.json()
            sec.update(access_token=t["access_token"],
                       refresh_token=t.get("refresh_token") or sec["refresh_token"],
                       expires_at=time.time() + int(t["expires_in"]) - 60
                       if t.get("expires_in") else None)
            store.update(ws, PROVIDER, secret=sec)
            return f"Bearer {sec['access_token']}"

    def _get(self, ws, url, params=None, export=False):
        """One call. A 401 refreshes an OAuth token once; an API token's 401 means
        it was revoked. Only this connection's own host is ever called."""
        base = self._base(ws)
        if urllib.parse.urlsplit(url).hostname != urllib.parse.urlsplit(base).hostname:
            raise ConfigError("a link outside the connected Zendesk")
        bucket = http.bucket_key(PROVIDER, ws, "export" if export else "api")
        rate = EXPORT_RATE if export else RATE
        for attempt in (0, 1):
            auth = self._auth(ws, force=attempt == 1)
            r = http.request("GET", url, params=params, ok=(401,), bucket=bucket, rate=rate,
                             burst=1 if export else RATE,
                             headers={"Authorization": auth, "Accept": "application/json"})
            if r.status_code != 401:
                try:
                    return r.json()
                except ValueError:
                    raise ConfigError("the answer isn't JSON") from None
            if attempt == 1 or auth.startswith("Basic"):
                raise AuthExpired(f"GET {http.safe_url(url)}: HTTP 401")

    def _pages(self, ws, path, key, params=None):
        """Every record of a cursor-paginated listing (page[size], meta.has_more,
        links.next)."""
        url, params = f"{self._base(ws)}{path}", {"page[size]": 100, **(params or {})}
        while url:
            data = self._get(ws, url, params)
            yield from data.get(key) or []
            more = (data.get("meta") or {}).get("has_more")
            url, params = ((data.get("links") or {}).get("next") if more else None), None

    # ---- picking ------------------------------------------------------------------
    def _sections(self, ws):
        return {str(s["id"]): s for s in self._pages(ws, "/api/v2/help_center/sections",
                                                     "sections")}

    def list_containers(self, ctx, parent=None):
        ws = ctx.workspace
        if not parent:
            return sorted(({"id": f"category-{c['id']}", "name": c.get("name") or "Untitled",
                            "kind": "category", "has_children": True}
                           for c in self._pages(ws, "/api/v2/help_center/categories",
                                                "categories")),
                          key=lambda c: c["name"].lower())
        kind, _, pid = parent.partition("-")
        sections = list(self._sections(ws).values())
        parents = {str(s.get("parent_section_id")) for s in sections if s.get("parent_section_id")}
        if kind == "category":
            mine = [s for s in sections if str(s.get("category_id")) == pid
                    and not s.get("parent_section_id")]
        else:
            mine = [s for s in sections if str(s.get("parent_section_id")) == pid]
        return sorted(({"id": f"section-{s['id']}", "name": s.get("name") or "Untitled",
                        "kind": "section", "has_children": str(s["id"]) in parents}
                       for s in mine), key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            kind = p["id"].split("-")[0]
            if kind not in ("category", "section") or not p["id"].split("-", 1)[1].isdigit():
                raise FormError("Pick Help Center categories or sections.")
            p["kind"] = kind
        return out

    def check_options(self, options, settings):
        tickets = bool((options or {}).get("tickets"))
        return {"tickets": tickets, "access_lock": "restricted" if tickets else None}

    def options_of(self, settings):
        return {"tickets": bool((settings or {}).get("tickets"))}

    @staticmethod
    def _in_picks(section_id, picks, sections):
        """Is a section (or a section above it, or its category) picked?"""
        if not picks:
            return True
        seen, sid = set(), str(section_id) if section_id else None
        while sid and sid not in seen:
            seen.add(sid)
            if f"section-{sid}" in picks:
                return True
            s = sections.get(sid) or {}
            if f"category-{s.get('category_id')}" in picks:
                return True
            sid = str(s["parent_section_id"]) if s.get("parent_section_id") else None
        return False

    # ---- syncing ------------------------------------------------------------------
    def _segments(self, ws):
        try:
            return {str(s["id"]): s for s in self._pages(ws, "/api/v2/help_center/user_segments",
                                                         "user_segments")}
        except Forbidden:
            return {}                     # can't read them: every segmented article is staff

    def _all_articles(self, ws):
        return self._pages(ws, "/api/v2/help_center/articles", "articles")

    def _changed_articles(self, ws, start, out):
        """The incremental article export from `start`; out["next"]: where the next
        run starts (this page's end_time)."""
        url = f"{self._base(ws)}/api/v2/help_center/incremental/articles"
        while True:
            data = self._get(ws, url, {"start_time": int(start)}, export=True)
            got = data.get("articles") or []
            yield from got
            end = data.get("end_time")
            if not end or int(end) <= int(start) or not data.get("next_page") \
                    or len(got) < EXPORT_PAGE:
                out["next"] = int(end or start)
                return
            start = int(end)

    def _article_item(self, ws, a, sections, segments):
        s = sections.get(str(a.get("section_id"))) or {}
        return Item(source_id=f"article-{a['id']}", title=a.get("title") or "Untitled",
                    url=a.get("html_url") or "", version=a.get("updated_at") or "",
                    kind="article", mime="text/html", modified=a.get("updated_at"),
                    parent_id=f"section-{a.get('section_id')}" if a.get("section_id") else None,
                    acl=segment_acl(a, segments),
                    attrs={"id": str(a["id"]), "section": s.get("name"),
                           "category_id": s.get("category_id"), "locale": a.get("locale"),
                           "labels": list(a.get("label_names") or [])},
                    content=article_html(a.get("body")))

    def _tickets(self, ws, state, tickets_from, out):
        """The ticket export from the stored cursor (or `tickets_from`);
        out["cursor"]: where the next run starts."""
        url = f"{self._base(ws)}/api/v2/incremental/tickets/cursor"
        params = ({"cursor": state["ticket_cursor"]} if state.get("ticket_cursor")
                  else {"start_time": int(tickets_from)})
        out["cursor"] = state.get("ticket_cursor")
        while True:
            data = self._get(ws, url, params, export=True)
            yield from data.get("tickets") or []
            if data.get("after_cursor"):
                out["cursor"] = data["after_cursor"]
            if data.get("end_of_stream") or not data.get("after_cursor"):
                return
            params = {"cursor": data["after_cursor"]}

    def _ticket_item(self, base, t):
        return Item(source_id=f"ticket-{t['id']}", title=f"#{t['id']}: {t.get('subject') or ''}",
                    url=f"{base}/agent/tickets/{t['id']}", version=t.get("updated_at") or "",
                    kind="ticket", mime="text/markdown", modified=t.get("updated_at"),
                    acl={"mode": "staff", "reason": "ticket"},
                    attrs={"id": str(t["id"]), "status": t.get("status"),
                           "priority": t.get("priority"), "tags": list(t.get("tags") or [])},
                    content=t)

    def changes(self, ctx, cursor):
        ws = ctx.workspace
        settings = ctx.settings or {}
        state = json.loads(cursor) if cursor else {}
        picks = sorted(p["id"] for p in settings.get("containers") or [])
        full = (ctx.full or not state or state.get("picks") != picks
                or time.time() - state.get("full_at", 0) > RECONCILE)
        sections, segments = self._sections(ws), self._segments(ws)
        started = int(time.time()) - 60
        export = {"next": started}
        if full:
            articles = self._all_articles(ws)
        else:
            articles = self._changed_articles(ws, state.get("articles_at") or started, export)
        seen, n = set(), 0

        def over_limit():
            return ctx.max_items and n >= ctx.max_items

        for a in articles:
            sid = f"article-{a['id']}"
            if a.get("draft") or not self._in_picks(a.get("section_id"), set(picks), sections):
                if sid in ctx.known and not full:
                    yield Delete(sid)             # unpublished or moved out of the picks
                continue
            if sid in seen:
                continue
            if over_limit():
                yield LimitReached(ctx.max_items)
                yield Cursor(cursor)
                return
            seen.add(sid)
            n += 1
            yield Upsert(self._article_item(ws, a, sections, segments))
        if full:
            yield from ctx.missing(seen | {s for s in ctx.known if not s.startswith("article-")})

        ticket_cursor = None
        if settings.get("tickets"):
            days = env_int("ZENDESK_TICKETS_BACKFILL_DAYS", TICKETS_BACKFILL_DAYS, minimum=1)
            base, out = self._base(ws), {}
            for t in self._tickets(ws, state, started - days * 86400, out):
                sid = f"ticket-{t['id']}"
                if t.get("status") not in SYNCED_STATUSES:
                    if sid in ctx.known:
                        yield Delete(sid)         # deleted, or reopened
                    continue
                if over_limit():
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)
                    return
                n += 1
                yield Upsert(self._ticket_item(base, t))
            ticket_cursor = out.get("cursor")
        else:
            for sid in sorted(ctx.known):
                if sid.startswith("ticket-"):
                    yield Delete(sid)             # tickets were turned off
        yield Cursor(json.dumps({"picks": picks, "articles_at": export["next"],
                                 "ticket_cursor": ticket_cursor,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def fetch(self, ctx, item):
        if item.kind != "ticket":
            return super().fetch(ctx, item)
        t = item.content or {}
        facts = [("Status", t.get("status")), ("Priority", t.get("priority")),
                 ("Type", t.get("type")), ("Tags", ", ".join(t.get("tags") or []) or None),
                 ("Created", (t.get("created_at") or "")[:10] or None),
                 ("Updated", (t.get("updated_at") or "")[:10] or None)]
        parts = ["\n".join(f"{k}: {v}" for k, v in facts if v)]
        comments, count = [], 0
        for c in self._pages(ctx.workspace, f"/api/v2/tickets/{t['id']}/comments", "comments"):
            count += 1
            if count > MAX_COMMENTS:
                break
            if not c.get("public"):
                continue                          # internal notes stay in Zendesk
            body = (c.get("plain_body") or c.get("body") or "").strip()
            if body:
                comments.append(f"### Comment, {(c.get('created_at') or '')[:10]}\n\n{body}")
        if comments:
            parts.append("## Conversation\n\n" + "\n\n".join(comments))
        return Fetched("\n\n".join(p for p in parts if p), "text/markdown")


def _basic(email, token):
    return "Basic " + base64.b64encode(f"{email}/token:{token}".encode()).decode()


CONNECTOR = Zendesk()
