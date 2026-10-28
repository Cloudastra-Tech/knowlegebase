"""
connectors/freshdesk.py  —  sync a workspace's Freshdesk knowledge base
(Solutions) and, if an admin turns them on, its resolved tickets.

Connecting: Freshdesk's API has no OAuth for this; it takes an agent's API key
(Profile settings → View API key) as the Basic username with "X" as the
password. The form asks for the Freshdesk subdomain (acme for acme.freshdesk.com)
and the key; GET /api/v2/agents/me checks it before it is saved (encrypted).
Only https://{subdomain}.freshdesk.com is ever called.

What is synced:
  ARTICLES of the knowledge base (status 2, published; drafts are left out),
  their HTML turned into text by connectors/extract.py. Categories, folders and
  sub-folders are the containers: an admin may pick some; with none picked the
  whole knowledge base is synced. Each article's FOLDER VISIBILITY is captured
  into its acl: {"via": "freshdesk", "visibility": 1-7, ...}. 1 (all users) and
  2 (logged-in users) may be read by everyone in the workspace under the source
  policy; 3 (agents), 4 (selected companies), 5 (bots), 6 / 7 (selected contact /
  company segments) are staff (HR and admins); anything else is for no one
  (kb/services/permissions.py).
  TICKETS, optional and OFF by default (the picker's switch): resolved and closed
  tickets with their description and public replies (private notes stay in
  Freshdesk). They hold customers' personal data, so while they are on the
  connection's access is locked to HR and admins (settings["access_lock"]) and
  each ticket's acl says staff.

How a sync finds what changed:
  articles   Freshdesk's Solutions API has no "changed since" filter, so the
             knowledge base is LISTED (categories → folders → sub-folders →
             articles) at most every ARTICLES_EVERY seconds (FRESHDESK_ARTICLES_EVERY,
             default 3 hours: listing costs a call per folder, and the account's
             whole API allowance is shared), and whenever the picks change or a
             full sync is asked for. Unchanged articles are skipped by their
             updated_at. Anything known but not listed was unpublished or
             deleted: it goes behind the mass-delete guard.
  tickets    GET /api/v2/tickets?updated_since=<watermark>, oldest change first
             (order_by=updated_at, order_type=asc), 100 a page; the watermark is
             the newest updated_at seen. Freshdesk returns at most 300 pages for
             one query: the rest comes on the next run. A ticket no longer
             resolved or closed is deleted. Deleted tickets don't appear there:
             once a day (RECONCILE) the deleted-tickets filter is read too. A
             first sync goes back TICKETS_BACKFILL_DAYS (FRESHDESK_TICKETS_BACKFILL_DAYS,
             default 365).

Rate limits are per ACCOUNT (shared with the customer's other integrations):
3,000 calls an hour on Blossom / Garden, 5,000 on Estate / Forest (older plans
count per minute); a 429 carries Retry-After in seconds, which
connectors/http.py waits out. Calls are spaced to RATE a second.

API references (checked 2026-10-27, https://developers.freshdesk.com/api/):
  authentication  #authentication (Basic "apikey:X", base64; https://domain.freshdesk.com/api/v2)
  rate limits     #ratelimit (per plan, per account; X-Ratelimit-Total / -Remaining;
                  429 with Retry-After seconds; invalid requests count too)
  pagination      #pagination (page, per_page up to 100, default 30; Link header
                  rel="next" while there is a next page)
  agents/me       #me (GET /api/v2/agents/me: the currently authenticated agent)
  categories      #solution_category_attributes (GET /api/v2/solutions/categories)
  folders         #solution_folder_attributes (GET /api/v2/solutions/categories/[id]/folders,
                  GET /api/v2/solutions/folders/[id]/subfolders; visibility 1 All users,
                  2 Logged in users, 3 Agents, 4 Selected companies, 5 Bots, 6 Selected
                  contact segments, 7 Selected company segments; company_ids,
                  contact_segment_ids, company_segment_ids, parent_folder_id)
  articles        #solution_article_attributes (GET /api/v2/solutions/folders/[id]/articles;
                  status 1 draft, 2 published; description (HTML), description_text,
                  folder_id, category_id, hierarchy, tags, updated_at)
  tickets         #list_all_tickets (updated_since, order_by=updated_at, order_type,
                  include=description; filter=deleted; at most 300 pages; status 4
                  Resolved, 5 Closed: #ticket_attributes)
  conversations   #list_all_ticket_notes (GET /api/v2/tickets/[id]/conversations;
                  private, incoming, body_text)
Portal links (not API): articles at /support/solutions/articles/{id} (customers)
or /a/solutions/articles/{id} (agents), tickets at /a/tickets/{id}.
"""

import base64
import datetime
import json
import logging
import re
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Delete, Fetched,
                             FormError, Item, LimitReached, NotFound, Upsert)
from connectors.zendesk import article_html
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

PROVIDER = "freshdesk"
RATE = 0.5                           # calls a second (1,800 an hour: under the smallest plan)
PAGE = 100
ARTICLES_EVERY = 3 * 60 * 60         # seconds between knowledge base listings
RECONCILE = 24 * 60 * 60             # seconds between reads of the deleted-tickets filter
TICKETS_BACKFILL_DAYS = 365
MAX_TICKET_PAGES = 300               # Freshdesk's own cap for one query
MAX_DELETED_PAGES = 20
SYNCED_STATUSES = (4, 5)             # Resolved, Closed
PUBLISHED = 2
STAFF_VISIBILITY = (3, 4, 5, 6, 7)
MAX_CONVERSATIONS = 500
_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def subdomain(value):
    """The subdomain from what an admin typed: "acme", "acme.freshdesk.com" or
    "https://acme.freshdesk.com/a/"."""
    v = (value or "").strip().lower()
    if "//" in v:
        v = urllib.parse.urlsplit(v).hostname or ""
    v = v.split("/")[0]
    if v.endswith(".freshdesk.com"):
        v = v[:-len(".freshdesk.com")]
    if not _SUBDOMAIN.match(v):
        raise FormError("Enter your Freshdesk subdomain: acme for acme.freshdesk.com.")
    return v


def folder_acl(folder):
    """Who may read a folder's articles in the Freshdesk portal (captured; mapped
    by kb/services/permissions.py)."""
    return {"via": PROVIDER, "visibility": folder.get("visibility"),
            "folder": str(folder.get("id")),
            "company_ids": list(folder.get("company_ids") or []),
            "contact_segment_ids": list(folder.get("contact_segment_ids") or []),
            "company_segment_ids": list(folder.get("company_segment_ids") or [])}


def _iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class Freshdesk(Connector):
    PROVIDER = PROVIDER
    LABEL = "Freshdesk"
    DESCRIPTION = "Knowledge base articles; resolved tickets if you turn them on"
    AUTH = "token"
    PICKER = "tree"
    PICKER_REQUIRED = False
    UNIT = "articles"
    ACL = True

    # ---- connecting ---------------------------------------------------------------
    def connect_form(self, workspace, form, user):
        sub = subdomain(form.get("url"))
        key = (form.get("token") or "").strip()
        if not key:
            raise FormError("Paste your Freshdesk API key (Profile settings → View API key).")
        try:
            me = self._me(sub, key)
        except AuthExpired:
            raise FormError("Freshdesk didn't accept that API key.") from None
        except FormError:
            raise
        except Exception:
            raise FormError(f"Couldn't reach {sub}.freshdesk.com.") from None
        contact = me.get("contact") or {}
        account = f"{sub}.freshdesk.com ({contact.get('name') or contact.get('email') or 'agent'})"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, {"api_key": key}, user)
        settings = dict((row or {}).get("settings") or {}, subdomain=sub)
        if row and (row.get("settings") or {}).get("subdomain") != sub:
            settings["containers"] = []           # another account: other folders
        store.update(workspace, PROVIDER, settings=settings)
        return account

    def _me(self, sub, key):
        r = http.request("GET", f"https://{sub}.freshdesk.com/api/v2/agents/me", retries=1,
                         headers={"Authorization": _basic(key), "Accept": "application/json"})
        me = r.json() or {}
        if not me.get("id"):
            raise AuthExpired("not an agent")
        return me

    # ---- calling ------------------------------------------------------------------
    def _base(self, ws):
        sub = ((store.get(ws, PROVIDER) or {}).get("settings") or {}).get("subdomain")
        if not sub:
            raise AuthExpired("Freshdesk is not connected")
        return f"https://{sub}.freshdesk.com"

    def _get(self, ws, url, params=None):
        """One call; returns the response. A 401 means the key was revoked. Only
        this connection's own host is ever called (a Link header can't send us
        elsewhere)."""
        base = self._base(ws)
        if urllib.parse.urlsplit(url).hostname != urllib.parse.urlsplit(base).hostname:
            raise ConfigError("a link outside the connected Freshdesk")
        sec = store.secret(ws, PROVIDER)
        if not sec or not sec.get("api_key"):
            raise AuthExpired("Freshdesk is not connected")
        return http.request("GET", url, params=params, bucket=http.bucket_key(PROVIDER, ws),
                            rate=RATE, burst=5,
                            headers={"Authorization": _basic(sec["api_key"]),
                                     "Accept": "application/json"})

    def _pages(self, ws, path, params=None, limit=None):
        """Every record of a listing, following Link rel="next"."""
        url, params, n = f"{self._base(ws)}{path}", {"per_page": PAGE, **(params or {})}, 0
        while url:
            r = self._get(ws, url, params)
            try:
                yield from r.json() or []
            except ValueError:
                raise ConfigError("the answer isn't JSON") from None
            n += 1
            if limit and n >= limit:
                return
            url, params = (r.links.get("next") or {}).get("url"), None

    # ---- the knowledge base -------------------------------------------------------
    def _categories(self, ws):
        return list(self._pages(ws, "/api/v2/solutions/categories"))

    def _folders(self, ws, category_id):
        return list(self._pages(ws, f"/api/v2/solutions/categories/{category_id}/folders"))

    def _subfolders(self, ws, folder_id):
        """A folder's sub-folders (accounts without Flexible Hierarchy have none:
        their folders carry no sub_folders_count, and the call may not exist)."""
        try:
            return list(self._pages(ws, f"/api/v2/solutions/folders/{folder_id}/subfolders"))
        except NotFound:
            return []

    def _tree(self, ws, picks):
        """[(folder, category)] of every folder (sub-folders too) in the picks, or
        every folder when nothing is picked."""
        out = []

        def walk(folder, category, inside):
            inside = inside or not picks or f"folder-{folder['id']}" in picks
            if inside:
                out.append((folder, category))
            if folder.get("sub_folders_count") == 0:
                return
            for sub in self._subfolders(ws, folder["id"]):
                walk(sub, category, inside)

        for c in self._categories(ws):
            picked = not picks or f"category-{c['id']}" in picks
            for f in self._folders(ws, c["id"]):
                walk(f, c, picked)
        return out

    def list_containers(self, ctx, parent=None):
        ws = ctx.workspace
        if not parent:
            items = [{"id": f"category-{c['id']}", "name": c.get("name") or "Untitled",
                      "kind": "category", "has_children": True} for c in self._categories(ws)]
        else:
            kind, _, pid = parent.partition("-")
            if not pid.isdigit() or kind not in ("category", "folder"):
                return []
            got = self._folders(ws, pid) if kind == "category" else self._subfolders(ws, pid)
            items = [{"id": f"folder-{f['id']}", "name": f.get("name") or "Untitled",
                      "kind": "folder", "has_children": bool(f.get("sub_folders_count"))}
                     for f in got]
        return sorted(items, key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            kind, _, pid = p["id"].partition("-")
            if kind not in ("category", "folder") or not pid.isdigit():
                raise FormError("Pick knowledge base categories or folders.")
            p["kind"] = kind
        return out

    def check_options(self, options, settings):
        tickets = bool((options or {}).get("tickets"))
        return {"tickets": tickets, "access_lock": "restricted" if tickets else None}

    def options_of(self, settings):
        return {"tickets": bool((settings or {}).get("tickets"))}

    def _article_item(self, base, a, folder, category):
        public = folder.get("visibility") in (1, 2)
        url = (f"{base}/support/solutions/articles/{a['id']}" if public
               else f"{base}/a/solutions/articles/{a['id']}")
        return Item(source_id=f"article-{a['id']}", title=a.get("title") or "Untitled",
                    url=url, version=a.get("updated_at") or "", kind="article",
                    mime="text/html", modified=a.get("updated_at"),
                    parent_id=f"folder-{folder['id']}", acl=folder_acl(folder),
                    attrs={"id": str(a["id"]), "folder": folder.get("name"),
                           "category": category.get("name"),
                           "tags": list(a.get("tags") or [])},
                    content=article_html(a.get("description") or ""))

    # ---- tickets ------------------------------------------------------------------
    def _ticket_item(self, base, t):
        return Item(source_id=f"ticket-{t['id']}", title=f"#{t['id']}: {t.get('subject') or ''}",
                    url=f"{base}/a/tickets/{t['id']}", version=t.get("updated_at") or "",
                    kind="ticket", mime="text/markdown", modified=t.get("updated_at"),
                    acl={"via": PROVIDER, "ticket": True},
                    attrs={"id": str(t["id"]), "status": t.get("status"),
                           "priority": t.get("priority"), "tags": list(t.get("tags") or [])},
                    content=t)

    def changes(self, ctx, cursor):
        ws = ctx.workspace
        settings = ctx.settings or {}
        state = json.loads(cursor) if cursor else {}
        picks = sorted(p["id"] for p in settings.get("containers") or [])
        now = time.time()
        every = env_int("FRESHDESK_ARTICLES_EVERY", ARTICLES_EVERY, minimum=60)
        full = (ctx.full or not state or state.get("picks") != picks
                or now - state.get("articles_at", 0) > every)
        base, n, seen = self._base(ws), 0, set()

        def over_limit():
            return ctx.max_items and n >= ctx.max_items

        if full:
            for folder, category in self._tree(ws, set(picks)):
                for a in self._pages(ws, f"/api/v2/solutions/folders/{folder['id']}/articles"):
                    sid = f"article-{a['id']}"
                    if a.get("status") != PUBLISHED or sid in seen:
                        continue
                    if over_limit():
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)
                        return
                    seen.add(sid)
                    n += 1
                    yield Upsert(self._article_item(base, a, folder, category))
            yield from ctx.missing(seen | {s for s in ctx.known if not s.startswith("article-")})

        tickets_at, deleted_at = state.get("tickets_at"), state.get("deleted_at", 0)
        if settings.get("tickets"):
            days = env_int("FRESHDESK_TICKETS_BACKFILL_DAYS", TICKETS_BACKFILL_DAYS, minimum=1)
            since = tickets_at or _iso(now - days * 86400)
            newest = since
            for t in self._pages(ws, "/api/v2/tickets", {
                    "updated_since": since, "order_by": "updated_at", "order_type": "asc",
                    "include": "description"}, limit=MAX_TICKET_PAGES):
                newest = max(newest, t.get("updated_at") or "")
                sid = f"ticket-{t['id']}"
                if t.get("status") not in SYNCED_STATUSES or t.get("spam") or t.get("deleted"):
                    if sid in ctx.known:
                        yield Delete(sid)         # reopened, spam or deleted
                    continue
                if over_limit():
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)
                    return
                n += 1
                yield Upsert(self._ticket_item(base, t))
            tickets_at = newest
            if now - deleted_at > RECONCILE:
                for t in self._pages(ws, "/api/v2/tickets", {"filter": "deleted"},
                                     limit=MAX_DELETED_PAGES):
                    if f"ticket-{t['id']}" in ctx.known:
                        yield Delete(f"ticket-{t['id']}")
                deleted_at = now
        else:
            tickets_at, deleted_at = None, 0
            for sid in sorted(ctx.known):
                if sid.startswith("ticket-"):
                    yield Delete(sid)             # tickets were turned off
        yield Cursor(json.dumps({"picks": picks,
                                 "articles_at": now if full else state.get("articles_at", 0),
                                 "tickets_at": tickets_at, "deleted_at": deleted_at}),
                     full=full)

    def fetch(self, ctx, item):
        if item.kind != "ticket":
            return super().fetch(ctx, item)
        t = item.content or {}
        status = {4: "Resolved", 5: "Closed"}.get(t.get("status"), t.get("status"))
        facts = [("Status", status), ("Type", t.get("type")),
                 ("Tags", ", ".join(t.get("tags") or []) or None),
                 ("Created", (t.get("created_at") or "")[:10] or None),
                 ("Updated", (t.get("updated_at") or "")[:10] or None)]
        parts = ["\n".join(f"{k}: {v}" for k, v in facts if v)]
        if (t.get("description_text") or "").strip():
            parts.append("## Description\n\n" + t["description_text"].strip())
        replies, count = [], 0
        for c in self._pages(ctx.workspace, f"/api/v2/tickets/{t['id']}/conversations"):
            count += 1
            if count > MAX_CONVERSATIONS:
                break
            if c.get("private"):
                continue                          # private notes stay in Freshdesk
            body = (c.get("body_text") or "").strip()
            if body:
                who = "Customer" if c.get("incoming") else "Agent"
                replies.append(f"### {who}, {(c.get('created_at') or '')[:10]}\n\n{body}")
        if replies:
            parts.append("## Conversation\n\n" + "\n\n".join(replies))
        return Fetched("\n\n".join(p for p in parts if p), "text/markdown")


def _basic(key):
    return "Basic " + base64.b64encode(f"{key}:X".encode()).decode()


CONNECTOR = Freshdesk()
