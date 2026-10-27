"""
connectors/notion.py  —  sync a workspace's Notion pages into its index.

Read-only. WHAT can be synced is chosen inside Notion itself: on the "Connect
Notion" screen the admin picks the pages / databases to share, and only those
(plus their sub-pages and database rows) are visible to us. The Integrations
screen can narrow that further (settings["pages"]: only those pages and what's
under them); with nothing picked there, everything shared is synced.

Each page's text (headings, lists, to-dos, toggles, quotes, callouts, code,
tables, database-row properties) becomes <workspace docs>/connectors/notion/<page id>.txt.

How a sync finds what changed: Notion's search, newest edit first, stopping at
the last run's watermark (an INCREMENTAL run). Search can't tell what was
unshared or deleted, so every RECONCILE seconds (and on the first run) a FULL
listing runs instead, and pages known but not listed are deleted.

API version 2025-09-03 (databases have data sources; a database row's parent is
a data source). Access tokens are refreshed with the refresh token Notion gave
at connect (it hands out a new one each time, stored before use); calls are
spaced per token (Notion: about 3 requests a second per connection).

Notion has no API for a page's permissions: an item's acl only records that
the page was shared with the integration. Who may ask is the connection's
access level.

Needs NOTION_CLIENT_ID and NOTION_CLIENT_SECRET (a PUBLIC integration at
notion.so/my-integrations, redirect URI .../api/integrations/notion/callback,
capability: Read content).
"""

import base64
import datetime
import json
import os
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, Connector, ConnectorError, Cursor, Fetched, Item,
                             LimitReached, Upsert, Delete)

PROVIDER = "notion"
LABEL = "Notion"
API = "https://api.notion.com/v1"
VERSION = "2025-09-03"
MAX_BLOCK_DEPTH = 3                    # nested blocks (toggles, lists) to follow
RATE = 3                               # requests a second per token
RECONCILE = 6 * 60 * 60                # seconds between full listings (finds deletes)
OVERLAP = 120                          # seconds re-checked before the watermark


def _plain(rich):
    return "".join(x.get("plain_text", "") for x in rich or [])


def _title(page):
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return _plain(prop.get("title")) or "Untitled"
    return "Untitled"


def _parent(obj):
    """(kind, id) of a page's / database's parent: page, database, data_source or workspace."""
    p = obj.get("parent") or {}
    kind = p.get("type") or "workspace"
    return kind.removesuffix("_id"), p.get(kind) if kind != "workspace" else None


def _is_row(page):
    return _parent(page)[0] in ("database", "data_source")


def _property_text(prop):
    """Readable value of a database property (for database rows)."""
    t = prop.get("type")
    v = prop.get(t)
    if t in ("rich_text", "title"):
        return _plain(v)
    if t in ("number", "url", "email", "phone_number", "checkbox"):
        return "" if v is None else str(v)
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "multi_select":
        return ", ".join(o.get("name", "") for o in v or [])
    if t == "date":
        return (v or {}).get("start") or ""
    if t == "people":
        return ", ".join(p.get("name", "") for p in v or [])
    if t == "formula":
        return str((v or {}).get((v or {}).get("type"), "") or "")
    return ""


def _ts(iso):
    try:
        return datetime.datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _item(page):
    kind, parent = _parent(page)
    return Item(source_id=page["id"], title=_title(page), url=page.get("url") or "",
                version=page.get("last_edited_time") or "", kind="row" if _is_row(page) else "page",
                mime="text/plain", modified=page.get("last_edited_time"), parent_id=parent,
                acl={"source": "shared_with_integration"},
                attrs={"parent_kind": kind, "properties": page.get("properties") or {}})


class Notion(Connector):
    PROVIDER = PROVIDER
    LABEL = LABEL
    DESCRIPTION = "Pages, sub-pages and database rows"
    AUTH = "oauth"
    PICKER = "list"
    SETTINGS_KEY = "pages"
    UNIT = "pages"
    SETUP_ENV = "NOTION_CLIENT_ID / NOTION_CLIENT_SECRET"

    def configured(self):
        return bool(os.getenv("NOTION_CLIENT_ID") and os.getenv("NOTION_CLIENT_SECRET"))

    def auth_url(self, redirect_uri, state):
        return f"{API}/oauth/authorize?" + urllib.parse.urlencode({
            "client_id": os.environ["NOTION_CLIENT_ID"],
            "response_type": "code",
            "owner": "user",
            "redirect_uri": redirect_uri,
            "state": state,
        })

    def _basic(self):
        return base64.b64encode(f"{os.environ.get('NOTION_CLIENT_ID', '')}:"
                                f"{os.environ.get('NOTION_CLIENT_SECRET', '')}".encode()).decode()

    def _token_call(self, body, ok=()):
        return http.request("POST", f"{API}/oauth/token", retries=1, ok=ok, json=body,
                            headers={"Authorization": f"Basic {self._basic()}",
                                     "Content-Type": "application/json",
                                     "Notion-Version": VERSION})

    def finish_connect(self, workspace, code, redirect_uri, user):
        t = self._token_call({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": redirect_uri}).json()
        owner = ((t.get("owner") or {}).get("user") or {})
        email = (owner.get("person") or {}).get("email")
        account = t.get("workspace_name") or "Notion workspace"
        if email:
            account = f"{account} ({email})"
        store.connect(workspace, PROVIDER, account, {
            "access_token": t["access_token"],
            "refresh_token": t.get("refresh_token"),
            "workspace_id": t.get("workspace_id"),
        }, user)

    # ---- tokens and calls -------------------------------------------------------------
    def _secret(self, workspace):
        sec = store.secret(workspace, PROVIDER)
        if not sec or not sec.get("access_token"):
            raise AuthExpired("Notion is not connected")
        return sec

    def _refresh(self, workspace, used):
        """Swap the refresh token for new tokens (once, under a lock: Notion rotates
        refresh tokens, so a second thread must use the new one). Returns the
        access token to use now."""
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = self._secret(workspace)
            if sec["access_token"] != used:          # another thread just refreshed it
                return sec["access_token"]
            if not sec.get("refresh_token"):
                raise AuthExpired("Notion refused the token and there is no refresh token")
            r = self._token_call({"grant_type": "refresh_token",
                                  "refresh_token": sec["refresh_token"]}, ok=(400, 401))
            if r.status_code in (400, 401):
                raise AuthExpired("Notion refused the refresh token")
            t = r.json()
            sec.update(access_token=t["access_token"],
                       refresh_token=t.get("refresh_token") or sec["refresh_token"])
            store.update(workspace, PROVIDER, secret=sec)   # saved before it's used
            return sec["access_token"]

    def _call(self, workspace, method, path, **kwargs):
        """One Notion API call, spaced per token, retried on 429 / 5xx (connectors/http.py);
        a 401 refreshes the token once."""
        token = self._secret(workspace)["access_token"]
        for attempt in (0, 1):
            try:
                return http.request(method, f"{API}{path}",
                                    bucket=http.bucket_key(PROVIDER, workspace), rate=RATE,
                                    burst=RATE, headers={"Authorization": f"Bearer {token}",
                                                         "Notion-Version": VERSION,
                                                         "Content-Type": "application/json"},
                                    **kwargs).json()
            except AuthExpired:
                if attempt == 1:
                    raise
                token = self._refresh(workspace, token)

    def _search(self, workspace, kind="page"):
        """Everything of `kind` shared with the integration, newest edit first."""
        cursor = None
        while True:
            body = {"page_size": 100, "filter": {"property": "object", "value": kind},
                    "sort": {"timestamp": "last_edited_time", "direction": "descending"}}
            if cursor:
                body["start_cursor"] = cursor
            data = self._call(workspace, "POST", "/search", json=body)
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")

    # ---- picking ---------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        """The shared pages at the top of what Notion shared with us (sub-pages come
        with them)."""
        pages = [p for p in self._search(ctx.workspace)
                 if not p.get("archived") and not p.get("in_trash") and not _is_row(p)]
        ids = {p["id"] for p in pages}
        return sorted(({"id": p["id"], "name": _title(p), "kind": "page", "url": p.get("url")}
                       for p in pages if _parent(p)[1] not in ids),
                      key=lambda c: c["name"].lower())

    # ---- syncing ---------------------------------------------------------------------
    def _scope(self, ctx):
        """in_scope(page) for the picked pages (everything when none are picked)."""
        picked = {str(p["id"]) for p in (ctx.settings or {}).get("pages") or []}
        if not picked:
            return lambda page: True
        parents = dict(ctx.parents)
        db_parent = {}

        def parent_of(kind, pid):
            if kind in ("database", "data_source"):
                if pid not in db_parent:
                    try:
                        obj = self._call(ctx.workspace, "GET",
                                         f"/{'data_sources' if kind == 'data_source' else 'databases'}/{pid}")
                    except ConnectorError:
                        obj = {}
                    pk, pp = _parent(obj)
                    if pk == "database":            # a data source's parent is its database
                        pk, pp = parent_of("database", pp)
                    db_parent[pid] = (pk, pp)
                return db_parent[pid]
            return "page", parents.get(pid)

        def in_scope(page):
            node, kind, pid = page["id"], *_parent(page)
            for _ in range(50):
                if node in picked or pid in picked:
                    return True
                if not pid:
                    return False
                node = pid
                kind, pid = parent_of(kind, pid)
                if kind == "page" and pid is None:
                    pid = parents.get(node)
            return False

        def remember(page):
            parents[page["id"]] = _parent(page)[1]
        in_scope.remember = remember
        return in_scope

    def changes(self, ctx, cursor):
        state = json.loads(cursor) if cursor else {}
        full = ctx.full or not state or time.time() - state.get("full_at", 0) > RECONCILE
        watermark = state.get("watermark") or ""
        in_scope = self._scope(ctx)
        pages = []
        for page in self._search(ctx.workspace):
            if not full and watermark and _ts(page.get("last_edited_time")) < _ts(watermark) - OVERLAP:
                break                                 # older than the last run: done
            pages.append(page)
            if hasattr(in_scope, "remember"):
                in_scope.remember(page)
        newest = max([watermark] + [p.get("last_edited_time") or "" for p in pages],
                     key=_ts)
        seen, n = set(), 0
        for page in pages:
            if page.get("archived") or page.get("in_trash") or not in_scope(page):
                if page["id"] in ctx.known:
                    yield Delete(page["id"])
                continue
            if ctx.max_items and n >= ctx.max_items:
                yield LimitReached(ctx.max_items)
                yield Cursor(json.dumps({"watermark": newest, "full_at": state.get("full_at", 0)}))
                return
            seen.add(page["id"])
            n += 1
            yield Upsert(_item(page))
        if full:
            yield from ctx.missing(seen)
            yield Cursor(json.dumps({"watermark": newest, "full_at": time.time()}), full=True)
        else:
            yield Cursor(json.dumps({"watermark": newest, "full_at": state.get("full_at", 0)}))

    def fetch(self, ctx, item):
        lines = []
        if item.kind == "row":
            for name, prop in ((item.attrs or {}).get("properties") or {}).items():
                if prop.get("type") != "title":
                    value = _property_text(prop)
                    if value:
                        lines.append(f"{name}: {value}")
            lines.append("")
        lines += self._blocks_text(ctx.workspace, item.source_id)
        return Fetched("\n".join(lines), "text/plain")

    def _blocks_text(self, workspace, block_id, depth=0):
        lines, cursor = [], None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            try:
                data = self._call(workspace, "GET", f"/blocks/{block_id}/children", params=params)
            except (http.ConfigError, http.Forbidden, http.NotFound):
                # Some blocks can't be read even though the page is shared: synced
                # blocks whose original lives on an unshared page, linked databases,
                # newer block types (AI, meeting notes). Skip that part, keep the rest.
                return [line for line in lines if line.strip()]
            for b in data.get("results", []):
                t = b.get("type")
                body = b.get(t) or {}
                text = _plain(body.get("rich_text"))
                if t and t.startswith("heading_"):
                    lines.append("#" * int(t[-1]) + " " + text)
                elif t in ("bulleted_list_item", "toggle"):
                    lines.append("- " + text)
                elif t == "numbered_list_item":
                    lines.append("1. " + text)
                elif t == "to_do":
                    lines.append(("[x] " if body.get("checked") else "[ ] ") + text)
                elif t in ("quote", "callout"):
                    lines.append("> " + text)
                elif t == "code":
                    lines.append(text)
                elif t == "table_row":
                    lines.append(" | ".join(_plain(c) for c in body.get("cells", [])))
                elif t in ("bookmark", "embed", "link_preview"):
                    lines.append(body.get("url", ""))
                elif t == "child_database":
                    lines.append(f"(database: {body.get('title', '')})")
                elif t == "child_page":
                    pass                       # synced as its own page
                elif text:
                    lines.append(text)          # paragraph and anything else with text
                if b.get("has_children") and t != "child_page" and depth < MAX_BLOCK_DEPTH:
                    lines += self._blocks_text(workspace, b["id"], depth + 1)
            if not data.get("has_more"):
                return [line for line in lines if line.strip()]
            cursor = data.get("next_cursor")

    def disconnect(self, workspace):
        """Nothing to revoke from here: to remove our access on Notion's side, the
        admin removes the integration in Notion (Settings -> Connections)."""


CONNECTOR = Notion()
