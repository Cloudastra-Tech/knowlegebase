"""
connectors/coda.py  —  sync a workspace's Coda docs (pages as Markdown) into its index.

Coda is now "Superhuman Docs (formerly Coda)"; its API is the same v1 API at
https://docs.superhuman.com/apis/v1 (coda.io/apis/v1 serves the same spec).
Read-only, with an API TOKEN of the user who connects (Account settings ->
API settings -> Generate API token; a read-only token is enough): only the docs
that user can open are synced. There is no OAuth for third-party apps.

What is synced (nothing until an admin picks on the card):
  containers  DOCS, picked from the docs the token's user has opened (GET /docs);
  pages       every page of a picked doc, exported as Markdown (beginPageContentExport,
              then its status until `complete`, then the downloadLink). Embed pages
              (an iframe of another site) have no text and are skipped.

How a sync finds what changed (the cursor: {docs: {id: updatedAt}, picks, full_at}):
  FULL (first run, the picks changed, every RECONCILE seconds, asked for)
      every page of every picked doc; anything known but not listed was deleted
      and goes behind the mass-delete guard.
  INCREMENTAL
      GET /docs/{id} per picked doc: a doc whose updatedAt hasn't moved is left
      alone; one that moved has its pages listed again (a page whose updatedAt
      moved is exported again by skip-if-unchanged, a page gone from the
      listing is deleted). A picked doc that is gone (404) loses its pages.

Permissions: a doc's SHARING, where Coda shows it to the token
(GET /docs/{id}/acl/permissions): email principals -> users, domain ->
domains, anyone -> everyone; the doc's owner is always a reader. Coda groups,
workspace-wide sharing and internal (support) access have no counterpart here
and are dropped (never more people). Sharing the token can't read (403: not a
doc the user may share): {"unavailable": true}, for no one under the source
policy (kb/services/permissions.py), and while any picked doc's sharing can't
be read the connection doesn't offer the source policy (source_available).

Rate limits: per user across all docs, "subject to change", answered with 429
(connectors/http.py waits Retry-After or backs off). Calls are spaced at RATE a
second per token.

API references (checked 2026-10-27, https://coda.io/developers/apis/v1, whose
spec https://coda.io/apis/v1/openapi.json names the base path
https://docs.superhuman.com/apis/v1):
  auth           Authorization: Bearer <token>; GET /whoami (name, loginId)
  docs           GET /docs (isOwner, query, workspaceId, folderId, limit, pageToken ->
                 items, nextPageToken; Doc: id, name, browserLink, owner (email),
                 updatedAt), GET /docs/{docId}
  pages          GET /docs/{docId}/pages (Page: id, name, browserLink, contentType canvas |
                 embed | syncPage, isHidden, parent, updatedAt)
  export         POST /docs/{docId}/pages/{pageId}/export {"outputFormat": "markdown"} -> 202
                 {id, status}; GET .../export/{requestId} -> {status inProgress | failed |
                 complete, downloadLink (expires soon), error}
  sharing        GET /docs/{docId}/acl/permissions (items: {principal: {type email | group |
                 domain | workspace | anyone | internalAccess, email?, domain?}, access})
  rate limits    "Rate Limiting" in the spec's introduction (429, per user)
"""

import hashlib
import json
import logging
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Delete, Fetched,
                             Forbidden, FormError, Item, LimitReached, NotFound, Skipped,
                             Transient, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "coda"
API = "https://docs.superhuman.com/apis/v1"
RATE = 4                               # calls a second per token
PAGE = 100
RECONCILE = 24 * 60 * 60
EXPORT_POLLS = 10                      # status checks of one page export
EXPORT_WAIT = 1.0                      # seconds before the first, doubling to 8


class Coda(Connector):
    PROVIDER = PROVIDER
    LABEL = "Coda"
    DESCRIPTION = "Pages of the docs you pick, as Markdown"
    AUTH = "token"
    PICKER = "list"
    PICKER_REQUIRED = True
    UNIT = "pages"
    ACL = True
    SOURCE_POLICY_NOTE = ("Coda only shows a doc's sharing to people who may share it, so "
                          "following it is offered once every picked doc's sharing was read.")

    # ---- connecting ---------------------------------------------------------------
    def connect_form(self, workspace, form, user):
        """A Coda API token: checked with GET /whoami, then saved (encrypted)."""
        token = (form.get("token") or "").strip()
        if not token:
            raise FormError("Paste a Coda API token (Account settings → API settings → "
                            "Generate API token).")
        try:
            me = self._call(token, "GET", f"{API}/whoami").json() or {}
        except (AuthExpired, Forbidden):
            raise FormError("Coda didn't accept that token.") from None
        except Exception:
            raise FormError("Couldn't reach Coda.") from None
        login = (me.get("loginId") or "").lower()
        account = login or me.get("name") or "Coda"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, {"token": token}, user)
        settings = dict((row or {}).get("settings") or {}, account=login)
        if row and (row.get("settings") or {}).get("account") != login:
            settings["containers"] = []           # another user: other docs
            settings.pop("sharing", None)
        store.update(workspace, PROVIDER, settings=settings)
        return account

    def source_available(self, settings):
        """Is "as shared in Coda" on offer? Only once a sync read the sharing of every
        picked doc (kb/services/permissions.py policies)."""
        return (settings or {}).get("sharing") == "readable"

    # ---- calls --------------------------------------------------------------------
    def _call(self, token, method, url, **kwargs):
        return http.request(method, url, bucket=http.bucket_key(PROVIDER, token), rate=RATE,
                            burst=RATE, headers={"Authorization": f"Bearer {token}",
                                                 "Accept": "application/json"}, **kwargs)

    def _token(self, ws):
        sec = store.secret(ws, PROVIDER)
        if not sec or not sec.get("token"):
            raise AuthExpired("Coda is not connected")
        return sec["token"]

    def _get(self, ws, path, params=None):
        r = self._call(self._token(ws), "GET", f"{API}{path}", params=params)
        try:
            return r.json() or {}
        except ValueError:
            raise ConfigError("the answer isn't JSON") from None

    def _items(self, ws, path, params=None):
        params = {**(params or {}), "limit": PAGE}
        while True:
            body = self._get(ws, path, params)
            yield from body.get("items") or []
            token = body.get("nextPageToken")
            if not token:
                return
            params = {**params, "pageToken": token}

    # ---- picking ------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        return sorted(({"id": d["id"], "name": d.get("name") or d["id"], "kind": "document",
                        "url": d.get("browserLink")} for d in self._items(ctx.workspace, "/docs")),
                      key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            if not p["id"].replace("-", "").replace("_", "").isalnum():
                raise FormError("Pick Coda docs.")
            p["kind"] = "document"
        return out

    # ---- syncing ------------------------------------------------------------------
    def _sharing(self, ws, doc):
        """The doc's acl: {"via", "doc", "users", "domains", "anyone", "groups"}, or
        {"unavailable": true} when the token can't read its sharing."""
        try:
            perms = list(self._items(ws, f"/docs/{doc['id']}/acl/permissions"))
        except (Forbidden, NotFound):
            return {"via": PROVIDER, "doc": doc["id"], "unavailable": True}
        users, domains, groups, anyone = set(), set(), set(), False
        for p in perms:
            who = p.get("principal") or {}
            if p.get("access") == "none":
                continue
            if who.get("type") == "email" and who.get("email"):
                users.add(who["email"].lower())
            elif who.get("type") == "domain" and who.get("domain"):
                domains.add(who["domain"].lower())
            elif who.get("type") == "anyone":
                anyone = True
            elif who.get("type") in ("group", "workspace"):
                groups.add(f"{who['type']}:{who.get('groupId') or who.get('workspaceId') or ''}")
        if doc.get("owner"):
            users.add(doc["owner"].lower())
        return {"via": PROVIDER, "doc": doc["id"], "users": sorted(users),
                "domains": sorted(domains), "anyone": anyone, "groups": sorted(groups)}

    def changes(self, ctx, cursor):
        picks = (ctx.settings or {}).get("containers") or []
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        ids = sorted(p["id"] for p in picks)
        sig = hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16]
        full = (ctx.full or not state or state.get("picks") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        before = state.get("docs") or {}
        docs, seen, n, unreadable = {}, set(), 0, 0
        for doc_id in ids:
            try:
                doc = self._get(ws, f"/docs/{urllib.parse.quote(doc_id, safe='')}")
            except NotFound:
                if not full:                      # gone: its pages go (guarded)
                    for sid in sorted(ctx.known):
                        if (ctx.parents.get(sid) or "") == doc_id:
                            yield Delete(sid)
                continue
            docs[doc_id] = doc.get("updatedAt") or ""
            acl = self._sharing(ws, doc)
            unreadable += bool(acl.get("unavailable"))
            if not full and before.get(doc_id) == docs[doc_id]:
                continue                          # nothing in it changed
            listed = set()
            for page in self._items(ws, f"/docs/{doc_id}/pages"):
                sid = f"{doc_id}:{page['id']}"
                listed.add(sid)
                if page.get("contentType") == "embed":
                    yield Skipped(sid, "embed", page.get("name") or "", page.get("browserLink")
                                  or "")
                    continue
                if ctx.max_items and n >= ctx.max_items:
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)
                    return
                n += 1
                yield Upsert(Item(
                    source_id=sid, title=f"{doc.get('name') or 'Coda doc'}: "
                                         f"{page.get('name') or 'Untitled page'}",
                    url=page.get("browserLink") or "", version=page.get("updatedAt") or "",
                    kind="page", mime="text/markdown", modified=page.get("updatedAt"),
                    parent_id=doc_id, acl=acl, attrs={"doc": doc_id, "page": page["id"]}))
            seen |= listed
            if not full:                          # this doc was listed whole
                for sid in sorted(ctx.known):
                    if ctx.parents.get(sid) == doc_id and sid not in listed:
                        yield Delete(sid)
        if full:
            yield from ctx.missing(seen)
        sharing = "unreadable" if unreadable else "readable"
        if (ctx.settings or {}).get("sharing") != sharing:
            row = store.get(ws, ctx.provider) or {}
            store.update(ws, ctx.provider, settings=dict(row.get("settings") or {},
                                                         sharing=sharing))
        yield Cursor(json.dumps({"picks": sig, "docs": docs,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def fetch(self, ctx, item):
        """The page as Markdown: start an export, wait for it, download it."""
        ws, a = ctx.workspace, item.attrs or {}
        token = self._token(ws)
        base = f"{API}/docs/{a['doc']}/pages/{a['page']}/export"
        started = self._call(token, "POST", base, json={"outputFormat": "markdown"}).json() or {}
        wait, status = EXPORT_WAIT, started
        for _ in range(EXPORT_POLLS):
            if status.get("status") == "complete" and status.get("downloadLink"):
                break
            if status.get("status") == "failed":
                raise ConfigError(f"Coda couldn't export a page ({status.get('error') or ''})")
            http.sleep(wait)
            wait = min(wait * 2, 8)
            status = self._call(token, "GET", f"{base}/{started.get('id')}").json() or {}
        else:
            raise Transient("Coda's page export didn't finish in time")
        link = status["downloadLink"]
        same_host = urllib.parse.urlsplit(link).netloc == urllib.parse.urlsplit(API).netloc
        r = http.request("GET", link, bucket=http.bucket_key(PROVIDER, token), rate=RATE,
                         burst=RATE,
                         headers={"Authorization": f"Bearer {token}"} if same_host else {})
        return Fetched(r.text, "text/markdown")

    def disconnect(self, workspace):
        """An API token can only be deleted by its owner in Coda (Account settings ->
        API settings); ours is deleted with the connection."""
        log.info("Coda: disconnected workspace %s; delete the API token in Coda to revoke it",
                 workspace)


CONNECTOR = Coda()
