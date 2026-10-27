"""
connectors/box.py  —  sync a workspace's Box folders into its index.

OAuth 2.0 user authentication (the connecting user's view of Box). Access
tokens last about an hour (expires_in); a refresh token lasts 60 days and is
SINGLE-USE: every refresh returns a new pair, saved under a per-connection
lock before the new access token is used. In an enterprise with "Disable
unpublished apps by default" on, an admin enables the app first (Admin Console
→ Integrations → Platform Apps Manager). Box's scopes page says downloading
needs "Read and write all files and folders" (root_readwrite): the app is
configured with it, and the sync only ever reads. Server authentication (JWT
or Client Credentials Grant, which always need an admin's authorisation) is a
follow-up for enterprises that want a service account instead of a user.

What is synced (nothing until an admin picks FOLDERS on the card): the files
under them, sub-folders included, that connectors/extract.py reads, each up to
MAX_BYTES.

How a sync finds what changed:
  FULL (first run, picks changed, every RECONCILE seconds, a folder changed,
  asked for)
      the picked folders are walked (GET /folders/{id}/items, marker paging,
      1,000 a page); known files not found go behind the mass-delete guard. The
      user's event stream position is taken (stream_position=now) BEFORE the
      walk, so nothing that changes meanwhile is missed.
  INCREMENTAL
      the user's event stream (GET /events, stream_type=changes) from the kept
      position, 500 a page, events deduplicated by event_id (Box may send one
      twice). A file event re-reads that file (GET /files/{id}): gone, trashed
      or moved out of the picks deletes it, otherwise it's upserted with its
      collaborations read again. An event on a FOLDER (moved, renamed, trashed,
      its collaborators changed) makes the run a full one: the shape of the
      picked tree may have changed. Box keeps user events two weeks to two
      months: a sync that stops for longer starts with a full run (the daily
      reconcile already does).

Permissions are captured into each file's acl: the ACCEPTED collaborations of
the file and of every folder above it (path_collection), as logins (emails)
and Box group ids, the owner's login, and the file's or a walked folder's
shared link access (open | company | collaborators). Box's docs don't say
whether a folder's collaborations list includes those inherited from its
parents, so every ancestor is read (cached per run): never fewer people than
Box shows, the mapping (kb/services/permissions.py) then drops what doesn't
match. {"via": "box", ...}.

Rate limits: 1,000 requests a minute per user; 429 with retry-after
(connectors/http.py waits it out); calls are spaced per token.

Needs BOX_CLIENT_ID and BOX_CLIENT_SECRET: a Custom App with User
Authentication (OAuth 2.0) in the Box Developer Console, redirect URI
<PUBLIC_URL>/api/integrations/box/callback, scope "Read and write all files
and folders stored in Box".

API references (checked 2026-10-27):
  authorize       https://developer.box.com/reference/get-authorize/
  token           https://developer.box.com/reference/post-oauth2-token/
  refresh tokens  https://developer.box.com/guides/authentication/tokens/refresh/
                  (valid 60 days; can be used only once)
  revoke          https://developer.box.com/reference/post-oauth2-revoke/
  scopes          https://developer.box.com/guides/api-calls/permissions-and-errors/scopes/
                  (root_readwrite: "Necessary to download files and folders")
  authorization   https://developer.box.com/guides/authorization/
  users/me        https://developer.box.com/reference/get-users-me/
  folder items    https://developer.box.com/reference/get-folders-id-items/
                  (usemarker, marker, next_marker, limit up to 1000, fields)
  file            https://developer.box.com/reference/resources/file/
  download        https://developer.box.com/reference/get-files-id-content/
                  (302 to a download URL; 202 + Retry-After when not ready)
  events          https://developer.box.com/reference/get-events/
                  (stream_position=now, stream_type=changes, limit up to 500,
                  next_stream_position, event_id for duplicates)
  user events     https://developer.box.com/guides/events/user-events/for-user/
  collaborations  https://developer.box.com/reference/get-folders-id-collaborations/
                  https://developer.box.com/reference/get-files-id-collaborations/
  rate limits     https://developer.box.com/guides/api-calls/permissions-and-errors/rate-limits/
  errors          https://developer.box.com/guides/api-calls/permissions-and-errors/common-errors/
"""

import hashlib
import json
import logging
import os
import time
import urllib.parse

import integrations_store as store
from connectors import extract, http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Delete, Fetched,
                             Forbidden, FormError, Item, LimitReached, NotFound, Skipped,
                             Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "box"
AUTH_URL = "https://account.box.com/api/oauth2/authorize"
TOKEN_URL = "https://api.box.com/oauth2/token"
REVOKE_URL = "https://api.box.com/oauth2/revoke"
API = "https://api.box.com/2.0"
RATE = 10                              # calls a second per token (1,000 a minute)
MAX_BYTES = 20 * 1024 * 1024
RECONCILE = 24 * 60 * 60
PAGE = 1000
EVENTS_PAGE = 500
FILE_FIELDS = ("id,type,name,etag,sha1,size,modified_at,path_collection,shared_link,owned_by,"
               "parent,item_status")


def _rank(access):
    return {"open": 2, "company": 1}.get(access or "", 0)


class Box(Connector):
    PROVIDER = PROVIDER
    LABEL = "Box"
    DESCRIPTION = "PDFs, Office and text files from the folders you pick"
    AUTH = "oauth"
    PICKER = "tree"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "folders"
    UNIT = "files"
    SETUP_ENV = "BOX_CLIENT_ID / BOX_CLIENT_SECRET"
    NOTE = ("If your Box admin blocks unpublished apps, they enable it first (Admin Console → "
            "Integrations → Platform Apps Manager).")
    ACL = True

    def configured(self):
        return bool(os.getenv("BOX_CLIENT_ID") and os.getenv("BOX_CLIENT_SECRET"))

    # ---- connecting ---------------------------------------------------------------
    def auth_url(self, redirect_uri, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": os.environ["BOX_CLIENT_ID"],
            "redirect_uri": redirect_uri, "state": state})

    def _token_call(self, data):
        r = http.request("POST", TOKEN_URL, retries=1, ok=(400, 401), data={
            **data, "client_id": os.environ.get("BOX_CLIENT_ID", ""),
            "client_secret": os.environ.get("BOX_CLIENT_SECRET", "")})
        if r.status_code in (400, 401):
            raise AuthExpired(f"Box refused the {data['grant_type']} grant")
        t = r.json()
        return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token"),
                "expires_at": time.time() + int(t.get("expires_in") or 3600) - 60}

    def finish_connect(self, workspace, code, redirect_uri, user):
        sec = self._token_call({"grant_type": "authorization_code", "code": code,
                                "redirect_uri": redirect_uri})
        if not sec.get("refresh_token"):
            raise ConfigError("Box did not return a refresh token")
        me = self._api(None, "GET", "/users/me", token=sec["access_token"],
                       params={"fields": "id,name,login,enterprise"}).json()
        enterprise = (me.get("enterprise") or {}).get("name")
        who = me.get("login") or me.get("name") or "Box"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, f"{enterprise} ({who})" if enterprise else who,
                      {**sec, "login": (me.get("login") or "").lower()}, user)
        if row and (row.get("settings") or {}).get("user_id") != me.get("id"):
            store.update(workspace, PROVIDER, settings=dict(row.get("settings") or {},
                                                            folders=[], user_id=me.get("id")))
        elif not row:
            store.update(workspace, PROVIDER, settings={"user_id": me.get("id")})

    # ---- tokens and calls ---------------------------------------------------------
    def _access_token(self, workspace, used=None):
        """A valid access token; refreshed (once, under a lock: the refresh token is
        single-use, so the new one is saved before anything else) when expired or
        when `used` was refused."""
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = store.secret(workspace, PROVIDER)
            if not sec or not sec.get("access_token"):
                raise AuthExpired("Box is not connected")
            fresh = sec.get("expires_at", 0) > time.time()
            if fresh and (used is None or sec["access_token"] != used):
                return sec["access_token"]
            if not sec.get("refresh_token"):
                raise AuthExpired("Box refused the token and there is no refresh token")
            sec.update(self._token_call({"grant_type": "refresh_token",
                                         "refresh_token": sec["refresh_token"]}))
            store.update(workspace, PROVIDER, secret=sec)
            return sec["access_token"]

    def _api(self, workspace, method, path, token=None, **kwargs):
        """One API call; a 401 refreshes the token once."""
        url = path if path.startswith("https://") else f"{API}{path}"
        for attempt in (0, 1):
            if workspace is not None:
                token = self._access_token(workspace, used=token if attempt else None)
            try:
                return http.request(method, url, bucket=http.bucket_key(PROVIDER, token),
                                    rate=RATE, burst=RATE,
                                    headers={"Authorization": f"Bearer {token}"}, **kwargs)
            except AuthExpired:
                if attempt == 1 or workspace is None:
                    raise

    def _json(self, workspace, path, **params):
        return self._api(workspace, "GET", path, params=params).json()

    def _items(self, workspace, folder, fields):
        """Every entry of a folder (marker paging)."""
        marker = None
        while True:
            params = {"fields": fields, "limit": PAGE, "usemarker": "true"}
            if marker:
                params["marker"] = marker
            data = self._json(workspace, f"/folders/{folder}/items", **params)
            yield from data.get("entries") or []
            marker = data.get("next_marker")
            if not marker:
                return

    # ---- picking ------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        out = [{"id": e["id"], "name": e.get("name") or e["id"], "kind": "folder",
                "has_children": True}
               for e in self._items(ctx.workspace, parent or "0", "id,type,name")
               if e.get("type") == "folder"]
        return sorted(out, key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            if p["kind"] != "folder" or not p["id"].isdigit():
                raise FormError("Pick Box folders.")
        return out

    # ---- permissions --------------------------------------------------------------
    def _collaborations(self, workspace, kind, item_id, cache):
        """({logins}, {group ids}) of an item's accepted collaborations; None when
        they can't be read."""
        key = f"{kind}:{item_id}"
        if key in cache:
            return cache[key]
        users, groups, marker = set(), set(), None
        try:
            while True:
                params = {"limit": PAGE, "fields": "accessible_by,status,role,invite_email"}
                if marker:
                    params["marker"] = marker
                data = self._json(workspace, f"/{kind}s/{item_id}/collaborations", **params)
                for c in data.get("entries") or []:
                    if c.get("status") != "accepted":
                        continue                       # pending / rejected invites can't read
                    who = c.get("accessible_by") or {}
                    if who.get("type") == "user" and who.get("login"):
                        users.add(who["login"].lower())
                    elif who.get("type") == "group" and who.get("id"):
                        groups.add(f"box:group:{who['id']}")
                marker = data.get("next_marker")
                if not marker:
                    break
        except (Forbidden, NotFound):
            cache[key] = None
            return None
        cache[key] = (users, groups)
        return cache[key]

    def _acl(self, workspace, f, cache, links):
        users = {((f.get("owned_by") or {}).get("login") or "").lower()} - {""}
        groups, partial = set(), False
        chain = [("folder", p["id"]) for p in (f.get("path_collection") or {}).get("entries") or []
                 if p.get("id") and p["id"] != "0"] + [("file", f["id"])]
        for kind, item_id in chain:
            got = self._collaborations(workspace, kind, item_id, cache)
            if got is None:
                partial = True
                continue
            users |= got[0]
            groups |= got[1]
        access = (f.get("shared_link") or {}).get("access")
        for kind, item_id in chain[:-1]:
            if _rank(links.get(item_id)) > _rank(access):
                access = links[item_id]
        acl = {"via": "box", "users": sorted(users), "groups": sorted(groups),
               "shared_link": access}
        if partial:
            acl["partial"] = True
        return acl

    # ---- syncing ------------------------------------------------------------------
    def changes(self, ctx, cursor):
        picks = sorted({str(p["id"]) for p in (ctx.settings or {}).get("folders") or []})
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        state = json.loads(cursor) if cursor else {}
        sig = hashlib.sha256(json.dumps(picks).encode()).hexdigest()[:16]
        full = (ctx.full or not state or state.get("folders") != sig
                or not state.get("position")
                or time.time() - state.get("full_at", 0) > RECONCILE)
        if not full:
            events = yield from self._incremental(ctx, picks, state)
            if events is not None:
                yield Cursor(json.dumps({**state, "position": events}))
                return
            log.info("Box: a folder changed for workspace %s; listing everything again",
                     ctx.workspace)
        yield from self._full(ctx, picks, sig, cursor)

    def _in_scope(self, f, picks):
        ids = {p.get("id") for p in (f.get("path_collection") or {}).get("entries") or []}
        return bool(ids & set(picks))

    def _upsert(self, ctx, f, cache, links):
        name = f.get("name") or "Untitled"
        if extract.kind_of("", name) is None:
            return None
        if (f.get("size") or 0) > MAX_BYTES:
            return Skipped(f["id"], "too_large", name, f"https://app.box.com/file/{f['id']}")
        version = f.get("sha1") or f.get("etag") or ""
        acl = self._acl(ctx.workspace, f, cache, links)
        return Upsert(Item(
            source_id=f["id"], title=name, url=f"https://app.box.com/file/{f['id']}",
            version=version, kind="file", mime="", size=f.get("size"),
            modified=f.get("modified_at"), parent_id=(f.get("parent") or {}).get("id"),
            acl=acl))

    def _full(self, ctx, picks, sig, cursor):
        ws = ctx.workspace
        position = self._json(ws, "/events", stream_position="now",
                              stream_type="changes").get("next_stream_position")
        cache, links, seen, n = {}, {}, set(), 0
        queue, walked = list(picks), set()
        while queue:
            folder = queue.pop(0)
            if folder in walked:
                continue
            walked.add(folder)
            try:
                entries = list(self._items(ws, folder, FILE_FIELDS))
            except NotFound:
                if folder in picks:
                    raise ConfigError("a picked Box folder is gone") from None
                continue
            for e in entries:
                if e.get("type") == "folder":
                    links[e["id"]] = (e.get("shared_link") or {}).get("access")
                    queue.append(e["id"])
                    continue
                if e.get("type") != "file":
                    continue
                if extract.kind_of("", e.get("name")) is None:
                    continue
                if ctx.max_items and n >= ctx.max_items:
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)              # deletes nothing; tries again
                    return
                seen.add(e["id"])
                n += 1
                change = self._upsert(ctx, e, cache, links)
                if change:
                    yield change
        yield from ctx.missing(seen)
        yield Cursor(json.dumps({"folders": sig, "position": position, "full_at": time.time()}),
                     full=True)

    def _incremental(self, ctx, picks, state):
        """Apply the event stream since the kept position. Returns the new position,
        or None when a folder changed (the caller lists everything instead). Its
        changes are only yielded when no folder changed."""
        ws = ctx.workspace
        position, files, seen_events = state["position"], {}, set()
        while True:
            data = self._json(ws, "/events", stream_position=position, stream_type="changes",
                              limit=EVENTS_PAGE)
            entries = data.get("entries") or []
            for ev in entries:
                if ev.get("event_id") in seen_events:
                    continue
                seen_events.add(ev.get("event_id"))
                src = ev.get("source") or {}
                if src.get("type") == "folder":
                    return None
                if src.get("type") == "file" and src.get("id"):
                    files[src["id"]] = ev.get("event_type")
            position = data.get("next_stream_position") or position
            if len(entries) < EVENTS_PAGE or not data.get("chunk_size"):
                break
        cache, links = {}, {}
        for file_id in sorted(files):
            try:
                f = self._json(ws, f"/files/{file_id}", fields=FILE_FIELDS)
            except (NotFound, Forbidden):
                f = None
            if not f or f.get("item_status", "active") != "active" or \
                    not self._in_scope(f, picks):
                if file_id in ctx.known or file_id in ctx.items:
                    yield Delete(file_id)
                continue
            change = self._upsert(ctx, f, cache, links)
            if change:
                yield change
        return position

    def fetch(self, ctx, item):
        if item.size and item.size > MAX_BYTES:
            return None
        r = self._api(ctx.workspace, "GET", f"/files/{item.source_id}/content", timeout=60,
                      ok=(202,))
        if r.status_code == 202:                 # not ready yet: the next sync takes it
            log.info("Box: file %s isn't ready to download yet", item.source_id)
            return None
        return Fetched(r.content, "")

    def disconnect(self, workspace):
        """Revoke the tokens at Box."""
        sec = store.secret(workspace, PROVIDER)
        token = (sec or {}).get("refresh_token") or (sec or {}).get("access_token")
        if not token:
            return
        try:
            http.request("POST", REVOKE_URL, retries=1, ok=(400,), data={
                "client_id": os.environ.get("BOX_CLIENT_ID", ""),
                "client_secret": os.environ.get("BOX_CLIENT_SECRET", ""), "token": token})
        except Exception as e:
            log.warning("Box: couldn't revoke the token of workspace %s (%s); remove the app "
                        "in Box (Account Settings -> Apps) if it matters",
                        workspace, type(e).__name__)


CONNECTOR = Box()
