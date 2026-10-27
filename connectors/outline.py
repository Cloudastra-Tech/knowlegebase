"""
connectors/outline.py  —  sync a workspace's Outline wiki into its index.

Per workspace: an admin pastes the wiki's address and an API token (Outline:
Settings -> API & Apps) on the Integrations screen; the token is stored
encrypted (secret_box.py, like every connection's tokens). They then pick which
collections to sync; nothing is synced until they do.

Each document's Markdown becomes <workspace docs>/connectors/outline/<id>.txt.
How a sync finds what changed: documents.list per collection, newest update
first, stopping at the last run's watermark (an INCREMENTAL run). Every
RECONCILE seconds (and on the first run) a FULL listing runs instead, and
documents known but not listed (deleted, archived, moved to another
collection) are deleted.

The wiki's address is typed in by an admin, so every call goes through
url_guard (https, public addresses only): the server can't be pointed at its
own network. Optional Cloudflare Access service-token headers are kept with the
token, for wikis behind Cloudflare Access.

Permissions: each document records its collection and the collection's
default permission (acl), for per-user filtering later (PL-10.2). Who may ask
is the connection's access level.

THE OLDER LIVE TOOL. The server can also be given one wiki in .env
(OUTLINE_URL / OUTLINE_API_TOKEN): the assistant then searches it live, for
the default workspace only (agent.py, kb/services/agents.outline_on). When a
workspace has this synced connection, the synced copy is used and the live
tool is not offered: one source, under the connection's access level.
"""

import datetime
import json
import time
from urllib.parse import urlsplit

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, FormError, Item,
                             LimitReached, Upsert)

PROVIDER = "outline"
RATE = 5                       # calls a second per token
PAGE = 100
RECONCILE = 6 * 60 * 60        # seconds between full listings (finds deletes)
OVERLAP = 120                  # seconds re-checked before the watermark


def _base(url):
    """The wiki's address as typed ("https://wiki.acme.com/", ".../api") -> its root."""
    url = (url or "").strip().rstrip("/")
    if url.endswith("/api"):
        url = url[:-4]
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname or parts.query or parts.fragment:
        raise FormError("Enter the wiki's address, like https://wiki.example.com.")
    return url


def _ts(iso):
    try:
        return datetime.datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class Outline(Connector):
    PROVIDER = PROVIDER
    LABEL = "Outline"
    DESCRIPTION = "Wiki documents from the collections you pick"
    AUTH = "token"
    PICKER = "list"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "collections"
    UNIT = "documents"

    def _post(self, base, secret, method, body=None):
        headers = {"Authorization": f"Bearer {secret['token']}",
                   "Content-Type": "application/json", "Accept": "application/json"}
        if secret.get("cf_client_id") and secret.get("cf_client_secret"):
            headers["CF-Access-Client-Id"] = secret["cf_client_id"]
            headers["CF-Access-Client-Secret"] = secret["cf_client_secret"]
        r = http.request("POST", f"{base}/api/{method}", json=body or {}, headers=headers,
                         guard=http.guard_public, rate=RATE, burst=RATE,
                         bucket=http.bucket_key(PROVIDER, secret["token"]))
        if r.status_code >= 300:           # a redirect: a sign-in page in front of the API
            raise ConfigError(f"{method}: redirected (is it behind a sign-in page?)")
        try:
            return r.json()
        except ValueError:
            raise ConfigError(f"{method}: the answer isn't JSON") from None

    def _conn(self, workspace):
        row = store.get(workspace, PROVIDER)
        sec = store.secret(workspace, PROVIDER)
        if not row or not sec or not sec.get("token"):
            raise AuthExpired("Outline is not connected")
        base = (row.get("settings") or {}).get("base_url")
        if not base:
            raise ConfigError("no wiki address")
        return base, sec

    def connect_form(self, workspace, form, user):
        """Check the address and token by asking the wiki who they belong to, then
        save the connection (the token encrypted)."""
        base = _base(form.get("url"))
        token = (form.get("token") or "").strip()
        if not token:
            raise FormError("Paste an API token from Outline (Settings → API & Apps).")
        secret = {"token": token}
        if form.get("cf_client_id") and form.get("cf_client_secret"):
            secret.update(cf_client_id=form["cf_client_id"].strip(),
                          cf_client_secret=form["cf_client_secret"].strip())
        try:
            info = self._post(base, secret, "auth.info").get("data") or {}
        except AuthExpired:
            raise FormError("Outline didn't accept that token.") from None
        except ConfigError as e:
            raise FormError(f"Couldn't use that address: {e}") from None
        except Exception:
            raise FormError("Couldn't reach Outline at that address.") from None
        team = (info.get("team") or {}).get("name") or urlsplit(base).hostname
        who = (info.get("user") or {}).get("email") or (info.get("user") or {}).get("name")
        account = f"{team} ({who})" if who else team
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, secret, user)
        settings = dict((row or {}).get("settings") or {}, base_url=base)
        if row and (row.get("settings") or {}).get("base_url") != base:
            settings["collections"] = []       # another wiki: its collections differ
        store.update(workspace, PROVIDER, settings=settings)
        return account

    def list_containers(self, ctx, parent=None):
        base, sec = self._conn(ctx.workspace)
        out, offset = [], 0
        while True:
            data = self._post(base, sec, "collections.list", {"limit": PAGE, "offset": offset})
            rows = data.get("data") or []
            out += [{"id": c["id"], "name": c.get("name") or "Untitled", "kind": "collection",
                     "permission": c.get("permission")} for c in rows]
            if len(rows) < PAGE:
                return sorted(out, key=lambda c: c["name"].lower())
            offset += PAGE

    def _documents(self, base, sec, collection):
        offset = 0
        while True:
            data = self._post(base, sec, "documents.list", {
                "collectionId": collection, "sort": "updatedAt", "direction": "DESC",
                "limit": PAGE, "offset": offset})
            rows = data.get("data") or []
            yield from rows
            if len(rows) < PAGE:
                return
            offset += PAGE

    def changes(self, ctx, cursor):
        picked = (ctx.settings or {}).get("collections") or []
        state = json.loads(cursor) if cursor else {}
        if not picked:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        base, sec = self._conn(ctx.workspace)
        ids = sorted(str(c["id"]) for c in picked)
        full = (ctx.full or not state or state.get("collections") != ids
                or time.time() - state.get("full_at", 0) > RECONCILE)
        marks = {} if full else dict(state.get("watermarks") or {})
        permission = {str(c["id"]): c.get("permission") for c in picked}
        seen, n, new_marks = set(), 0, {}
        for collection in ids:
            mark = marks.get(collection)
            newest = mark or ""
            try:
                docs = self._documents(base, sec, collection)
                for doc in docs:
                    updated = doc.get("updatedAt") or ""
                    if mark and _ts(updated) < _ts(mark) - OVERLAP:
                        break
                    if _ts(updated) > _ts(newest):
                        newest = updated
                    if ctx.max_items and n >= ctx.max_items:
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)            # the next run tries again from here
                        return
                    seen.add(doc["id"])
                    n += 1
                    yield Upsert(Item(
                        source_id=doc["id"], title=doc.get("title") or "Untitled",
                        url=base + (doc.get("url") or f"/doc/{doc['id']}"),
                        version=updated, kind="document", mime="text/markdown",
                        modified=updated, parent_id=doc.get("parentDocumentId") or collection,
                        acl={"collection": collection,
                             "collection_permission": permission.get(collection)},
                        attrs={"collection": collection}, content=doc.get("text") or ""))
            except http.NotFound:
                raise ConfigError("a picked collection is gone") from None
            new_marks[collection] = newest
        if full:                # an incremental run can't see deletes: the next full one does
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"collections": ids, "watermarks": new_marks,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)


CONNECTOR = Outline()


def has_sync(workspace):
    """Does this workspace sync an Outline wiki (so the live tool isn't needed)?"""
    row = store.get(workspace, PROVIDER)
    return bool(row and row.get("status") != "disconnected")
