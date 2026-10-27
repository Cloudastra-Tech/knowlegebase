"""
connectors/dropbox.py  —  sync a workspace's Dropbox folders into its index.

Read-only: OAuth 2 code flow with token_access_type=offline, so Dropbox hands
back a short-lived access token (4 hours) and a refresh token that doesn't
expire and isn't rotated; scopes account_info.read, files.metadata.read,
files.content.read and sharing.read. A Dropbox Business user whose team has a
separate team root gets the TEAM SPACE: every call carries
Dropbox-API-Path-Root {".tag": "root", "root": <root_namespace_id>}.

What is synced (nothing until an admin picks FOLDERS on the card): the files
under them, sub-folders included, that connectors/extract.py reads (PDF,
Word, PowerPoint, Excel, HTML, Markdown, text, CSV), each up to MAX_BYTES.

How a sync finds what changed, per picked folder:
  FULL (first run, picks changed, every RECONCILE seconds, a cursor Dropbox
  `reset`, asked for)
      /files/list_folder (recursive) and its /continue pages: every file. Known
      files not listed go behind the mass-delete guard. The last page's cursor
      is kept.
  INCREMENTAL
      /files/list_folder/continue from that cursor: only what changed. A
      `deleted` entry carries a path, not an id: every known file at or under
      that path is deleted.
  /files/list_folder/longpoll (notify.dropboxapi.com) could wake a sync as
  soon as something changes; not used yet: the 30-minute schedule polls.

Permissions are captured into each file's acl: for a file in a shared folder
(sharing_info.parent_shared_folder_id), /sharing/list_folder_members (read
once per shared folder per run): members' emails, groups (with their
group_external_id when the team syncs groups from an identity provider) and
pending invitees; a file in no shared folder is its owner's only (the
connected account). Members who can only traverse the folder, or have no
access, are left out. {"via": "dropbox", ...}: kb/services/permissions.py.
Sharing changes don't show in the change feed: the daily full run reads them
again.

App status: a new Dropbox app is in DEVELOPMENT and can link up to 500
users, but once it links 50 it has two weeks to be approved for PRODUCTION
before linking freezes (unlinking users doesn't unfreeze it): apply in the App
Console before the 50th workspace connects (deploy.md, "Dropbox").

Rate limits: 429 with Retry-After (connectors/http.py waits it out); calls
are spaced per token.

Needs DROPBOX_APP_KEY and DROPBOX_APP_SECRET: a "Scoped access", "Full
Dropbox" app in the App Console with the scopes above and the redirect URI
<PUBLIC_URL>/api/integrations/dropbox/callback.

API references (checked 2026-10-27):
  OAuth           https://docs.dropboxapi.com/dropbox-api/docs/get-started/authorization
                  (GET https://www.dropbox.com/oauth2/authorize: token_access_type=offline,
                  scope space separated; POST https://api.dropboxapi.com/oauth2/token
                  form-encoded; expires_in 14400; refresh tokens are reusable, not
                  rotated)
  OAuth guide     https://docs.dropboxapi.com/dropbox-api/docs/oauth
  endpoints       https://docs.dropboxapi.com/dropbox-api/api-reference/user-endpoints/
                  (files/list_folder (+ /continue, /longpoll), files/download
                  (Dropbox-API-Arg, path may be an id), sharing/list_folder_members,
                  users/get_current_account, auth/token/revoke)
  spec            https://github.com/dropbox/dropbox-api-spec (files.stone, sharing.stone:
                  deleted entries have no id; `reset` invalidates a cursor)
  path root       https://docs.dropboxapi.com/dropbox-api/docs/technical-reference/path-root-header-modes
  errors          https://docs.dropboxapi.com/dropbox-api/docs/error-handling
                  (401 expired_access_token, 409 endpoint errors, 429 Retry-After)
  production      https://docs.dropboxapi.com/dropbox-api/docs/developer-resources/developer-guide
                  ("once your app links 50 Dropbox users, you will have two weeks to apply
                  for and receive production status approval")
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
                             FormError, Item, LimitReached, MissingScope, Skipped, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "dropbox"
AUTH_URL = "https://www.dropbox.com/oauth2/authorize"
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
SCOPES = "account_info.read files.metadata.read files.content.read sharing.read"
RATE = 5                               # calls a second per token
MAX_BYTES = 20 * 1024 * 1024           # files bigger than this are skipped
RECONCILE = 24 * 60 * 60               # seconds between full listings
PAGE = 2000                            # list_folder's largest (approximate) page
NO_READ = ("traverse", "no_access")    # access types that can't open the files


class Dropbox(Connector):
    PROVIDER = PROVIDER
    LABEL = "Dropbox"
    DESCRIPTION = "PDFs, Office and text files from the folders you pick"
    AUTH = "oauth"
    PICKER = "tree"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "folders"
    UNIT = "files"
    SETUP_ENV = "DROPBOX_APP_KEY / DROPBOX_APP_SECRET"
    ACL = True

    def configured(self):
        return bool(os.getenv("DROPBOX_APP_KEY") and os.getenv("DROPBOX_APP_SECRET"))

    # ---- connecting ---------------------------------------------------------------
    def auth_url(self, redirect_uri, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": os.environ["DROPBOX_APP_KEY"], "response_type": "code",
            "redirect_uri": redirect_uri, "state": state, "token_access_type": "offline",
            "scope": SCOPES})

    def _token_call(self, data):
        r = http.request("POST", TOKEN_URL, retries=1, ok=(400, 401), data={
            **data, "client_id": os.environ.get("DROPBOX_APP_KEY", ""),
            "client_secret": os.environ.get("DROPBOX_APP_SECRET", "")})
        if r.status_code in (400, 401):
            raise AuthExpired(f"Dropbox refused the {data['grant_type']} grant")
        return r.json()

    def finish_connect(self, workspace, code, redirect_uri, user):
        t = self._token_call({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": redirect_uri})
        if not t.get("refresh_token"):
            raise ConfigError("Dropbox did not return a refresh token")
        sec = {"access_token": t["access_token"], "refresh_token": t["refresh_token"],
               "expires_at": time.time() + int(t.get("expires_in") or 14400) - 60}
        me = self._rpc(None, "users/get_current_account", None, token=sec["access_token"])
        root = me.get("root_info") or {}
        if root.get("root_namespace_id") and root.get("root_namespace_id") != \
                root.get("home_namespace_id"):
            sec["root"] = root["root_namespace_id"]          # the team space
        sec["email"] = (me.get("email") or "").lower()
        team = (me.get("team") or {}).get("name")
        who = me.get("email") or (me.get("name") or {}).get("display_name") or "Dropbox"
        account = f"{team} ({who})" if team else who
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, sec, user)
        if row and (row.get("settings") or {}).get("account_id") != me.get("account_id"):
            store.update(workspace, PROVIDER, settings=dict(
                row.get("settings") or {}, folders=[], account_id=me.get("account_id")))
        elif not row:
            store.update(workspace, PROVIDER, settings={"account_id": me.get("account_id")})

    # ---- tokens and calls ---------------------------------------------------------
    def _secret(self, workspace):
        sec = store.secret(workspace, PROVIDER)
        if not sec or not sec.get("refresh_token"):
            raise AuthExpired("Dropbox is not connected")
        return sec

    def _access_token(self, workspace, force=False):
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = self._secret(workspace)
            if not force and sec.get("access_token") and sec.get("expires_at", 0) > time.time():
                return sec
            t = self._token_call({"grant_type": "refresh_token",
                                  "refresh_token": sec["refresh_token"]})
            sec.update(access_token=t["access_token"],
                       expires_at=time.time() + int(t.get("expires_in") or 14400) - 60)
            store.update(workspace, PROVIDER, secret=sec)
            return sec

    def _send(self, workspace, url, token=None, headers=None, **kwargs):
        """One call; a 401 refreshes the token once. 409s (endpoint errors) come back."""
        for attempt in (0, 1):
            sec = {"access_token": token} if workspace is None else \
                self._access_token(workspace, force=attempt == 1)
            h = {"Authorization": f"Bearer {sec['access_token']}", **(headers or {})}
            if sec.get("root"):
                h["Dropbox-API-Path-Root"] = json.dumps({".tag": "root", "root": sec["root"]})
            try:
                r = http.request("POST", url, bucket=http.bucket_key(PROVIDER, workspace or token),
                                 rate=RATE, burst=RATE, ok=(409,), headers=h, **kwargs)
            except AuthExpired as e:
                if attempt == 1 or workspace is None:
                    raise
                log.info("Dropbox: token refused for workspace %s, refreshing (%s)", workspace, e)
                continue
            if r.status_code == 409:
                try:
                    err = r.json()
                except ValueError:
                    err = {}
                e = ConfigError(f"Dropbox: {err.get('error_summary') or 'endpoint error'}")
                e.dropbox = err.get("error") or {}
                e.summary = err.get("error_summary") or ""
                raise e
            return r
        raise AuthExpired("Dropbox refused the refreshed token")

    def _rpc(self, workspace, route, args, token=None):
        return self._send(workspace, f"{API}/{route}", token=token,
                          json=args if args is not None else None).json()

    # ---- picking ------------------------------------------------------------------
    def _listing(self, workspace, args):
        """Every page of a listing: yields (entries, cursor at the end)."""
        data = self._rpc(workspace, "files/list_folder", args)
        while True:
            yield data.get("entries") or [], data.get("cursor")
            if not data.get("has_more"):
                return
            data = self._rpc(workspace, "files/list_folder/continue", {"cursor": data["cursor"]})

    def list_containers(self, ctx, parent=None):
        out = []
        for entries, _ in self._listing(ctx.workspace, {"path": parent or "", "limit": PAGE}):
            out += [{"id": e["id"], "name": e.get("name") or e["id"], "kind": "folder",
                     "path": e.get("path_lower"), "has_children": True}
                    for e in entries if e.get(".tag") == "folder" and e.get("id")]
        return sorted(out, key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        paths = {str(p.get("id")): str(p.get("path") or "")[:1000]
                 for p in picked[:200] if isinstance(p, dict)}
        for p in out:
            if p["kind"] != "folder" or not p["id"].startswith("id:"):
                raise FormError("Pick Dropbox folders.")
            if paths.get(p["id"]):
                p["path"] = paths[p["id"]]
        return out

    # ---- permissions --------------------------------------------------------------
    def _folder_members(self, workspace, shared_folder_id):
        users, groups, invitees = set(), set(), set()
        try:
            data = self._rpc(workspace, "sharing/list_folder_members",
                             {"shared_folder_id": shared_folder_id, "limit": 1000})
        except ConfigError:
            return {"via": "dropbox", "users": [], "partial": True}
        while True:
            for u in data.get("users") or []:
                if (u.get("access_type") or {}).get(".tag") in NO_READ:
                    continue
                email = (u.get("user") or {}).get("email")
                if email:
                    users.add(email.lower())
            for g in data.get("groups") or []:
                if (g.get("access_type") or {}).get(".tag") in NO_READ:
                    continue
                info = g.get("group") or {}
                groups.add(f"dropbox:ext:{info['group_external_id']}"
                           if info.get("group_external_id")
                           else f"dropbox:group:{info.get('group_id')}")
            for i in data.get("invitees") or []:
                email = (i.get("invitee") or {}).get("email")
                if email:
                    invitees.add(email.lower())
            if not data.get("cursor"):
                break
            data = self._rpc(workspace, "sharing/list_folder_members/continue",
                             {"cursor": data["cursor"]})
        return {"via": "dropbox", "shared_folder": shared_folder_id, "users": sorted(users),
                "groups": sorted(groups), "invitees": sorted(invitees)}

    # ---- syncing ------------------------------------------------------------------
    def changes(self, ctx, cursor):
        picks = sorted({str(p["id"]) for p in (ctx.settings or {}).get("folders") or []})
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        sig = hashlib.sha256(json.dumps(picks).encode()).hexdigest()[:16]
        full = (ctx.full or not state or state.get("folders") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        for attempt in (0, 1):
            try:
                yield from self._run(ctx, picks, sig, state, full or attempt == 1, cursor)
                return
            except ConfigError as e:
                tag = (getattr(e, "dropbox", None) or {}).get(".tag")
                if tag == "reset" and attempt == 0:
                    log.info("Dropbox: a cursor of workspace %s was reset; listing again", ws)
                    continue
                if tag == "path" or getattr(e, "summary", "").startswith("path/not_found"):
                    raise ConfigError("a picked Dropbox folder is gone") from None
                raise

    def _run(self, ctx, picks, sig, state, full, cursor):
        ws = ctx.workspace
        owner = (self._secret(ws).get("email") or "").lower()
        members = {}
        cursors = {} if full else dict(state.get("cursors") or {})
        new_cursors, seen, n = {}, set(), 0
        dropped = set()                            # a move is a delete AND a new entry
        by_path = {}                               # path_lower -> source id, of known files
        for sid, row in ctx.items.items():
            path = ((row or {}).get("attrs") or {}).get("path")
            if path:
                by_path[path] = sid
        for folder in picks:
            if cursors.get(folder):
                pages = self._continue(ws, cursors[folder])
            else:
                pages = self._listing(ws, {"path": folder, "recursive": True, "limit": PAGE,
                                           "include_mounted_folders": True})
            last = None
            for entries, last in pages:
                for e in entries:
                    tag = e.get(".tag")
                    path = e.get("path_lower") or ""
                    if tag == "deleted":
                        for p, sid in by_path.items():
                            if (p == path or p.startswith(path + "/")) and sid in ctx.known:
                                dropped.add(sid)
                        continue
                    if tag != "file" or not e.get("id"):
                        continue
                    sid = e["id"]
                    name = e.get("name") or "Untitled"
                    if extract.kind_of("", name) is None or e.get("is_downloadable") is False:
                        if sid in ctx.known:              # renamed to a type we don't read
                            dropped.add(sid)
                        continue
                    if ctx.max_items and n >= ctx.max_items:
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)              # deletes nothing; tries again
                        return
                    seen.add(sid)
                    n += 1
                    if (e.get("size") or 0) > MAX_BYTES:
                        yield Skipped(sid, "too_large", name)
                        continue
                    shared = (e.get("sharing_info") or {}).get("parent_shared_folder_id") \
                        or e.get("parent_shared_folder_id")
                    if shared:
                        if shared not in members:
                            members[shared] = self._folder_members(ws, shared)
                        acl = members[shared]
                    else:
                        acl = {"via": "dropbox", "users": [owner] if owner else [],
                               "owner_only": True}
                    yield Upsert(Item(
                        source_id=sid, title=name,
                        url="https://www.dropbox.com/home" + urllib.parse.quote(
                            (e.get("path_display") or "").rsplit("/", 1)[0])
                            + "?preview=" + urllib.parse.quote(name),
                        version=e.get("rev") or e.get("content_hash") or "", kind="file",
                        mime="", size=e.get("size"), modified=e.get("server_modified"),
                        parent_id=folder, acl=acl, attrs={"path": path}))
            new_cursors[folder] = last
        for sid in sorted(dropped - seen):
            yield Delete(sid)
        if full:
            yield from ctx.missing(seen | dropped)
        yield Cursor(json.dumps({"folders": sig, "cursors": new_cursors,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def _continue(self, workspace, cursor):
        while True:
            data = self._rpc(workspace, "files/list_folder/continue", {"cursor": cursor})
            yield data.get("entries") or [], data.get("cursor")
            cursor = data.get("cursor")
            if not data.get("has_more"):
                return

    def fetch(self, ctx, item):
        if item.size and item.size > MAX_BYTES:
            return None
        try:
            r = self._send(ctx.workspace, f"{CONTENT}/files/download", timeout=60, headers={
                # ASCII only (json escapes the rest as \\uXXXX, as Dropbox asks)
                "Dropbox-API-Arg": json.dumps({"path": item.source_id})})
        except ConfigError as e:
            if getattr(e, "summary", "").startswith("path/not_found"):
                raise http.NotFound("Dropbox: the file is gone") from None
            raise
        return Fetched(r.content, "")

    def disconnect(self, workspace):
        """Revoke the token at Dropbox: that disables its refresh token too."""
        try:
            self._send(workspace, f"{API}/auth/token/revoke")
        except (AuthExpired, MissingScope):
            pass                                     # already revoked
        except Exception as e:
            log.warning("Dropbox: couldn't revoke the token of workspace %s (%s); remove the "
                        "app in Dropbox (Settings -> Connected apps) if it matters",
                        workspace, type(e).__name__)


CONNECTOR = Dropbox()
