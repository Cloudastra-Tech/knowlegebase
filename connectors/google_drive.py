"""
connectors/google_drive.py  —  sync a workspace's Google Drive into its index.

Read-only: we only ever LIST and DOWNLOAD files (scope drive.readonly).
What gets synced: ONLY what the admin picks — folders / Shared Drives (their
sub-folders included) and/or single files — nothing until something is picked.
Inside them: Google Docs, Slides (as text) and Sheets (as CSV), PDFs, Word,
PowerPoint, Excel, Markdown, HTML and plain-text files (connectors/extract.py).

How a sync finds what changed (connectors/base.py, changes()):
  first run, or the picks changed   list every file under the picks (a FULL
                                    listing: anything known but not listed is
                                    deleted), after taking a changes.list start
                                    page token, so nothing edited meanwhile is lost
  every run after that              changes.list from the stored page token: only
                                    files that changed since. A removed or trashed
                                    file is deleted; a file moved out of the picks
                                    too. A FOLDER that moved or went away means
                                    the picked tree changed shape: list everything
                                    again.
The cursor is JSON: the page token, the picks it was made for, and the folder
ids inside the picks (to tell whether a changed file is in scope).

Permissions: each file's Drive permissions (users, groups, domains, anyone)
are captured into the item's `acl` for per-user filtering later (PL-10.2). They
are NOT enforced: who may ask is still the connection's access level.

Needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (an OAuth client of type
"Web application" in Google Cloud, redirect URI .../api/integrations/google_drive/callback).
"""

import hashlib
import json
import logging
import os
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Delete, Fetched,
                             Item, LimitReached, Skipped, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "google_drive"
SCOPES = "openid email https://www.googleapis.com/auth/drive.readonly"
MAX_BYTES = 20 * 1024 * 1024          # files bigger than this are skipped (and listed as such)
RATE = 10                             # calls a second per token (Drive allows far more per user)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
API = "https://www.googleapis.com/drive/v3"
FILES_URL = f"{API}/files"
DRIVES_URL = f"{API}/drives"

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"
FOLDER = "application/vnd.google-apps.folder"
PDF = "application/pdf"
TEXT = "text/plain"
# Google's own formats are exported; everything else is downloaded as it is.
EXPORT = {DOC: TEXT, SHEET: "text/csv", SLIDES: TEXT}
DOWNLOAD = (PDF, TEXT, "text/markdown", "text/html", "text/csv",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
SUPPORTED = tuple(EXPORT) + DOWNLOAD
_WANTED = " or ".join(f"mimeType = '{m}'" for m in SUPPORTED)
_FILE_FIELDS = ("id, name, mimeType, modifiedTime, webViewLink, size, parents, trashed, "
                "permissions(type, role, emailAddress, domain), owners(emailAddress)")


def _acl(f):
    """Who can read the file in Drive, as far as the listing says. Files in Shared
    Drives come without their permissions in a listing: marked partial."""
    perms = f.get("permissions")
    acl = {"users": sorted({p["emailAddress"].lower() for p in perms or []
                            if p.get("type") == "user" and p.get("emailAddress")}
                           | {o["emailAddress"].lower() for o in f.get("owners") or []
                              if o.get("emailAddress")}),
           "groups": sorted({f"google:{p['emailAddress'].lower()}" for p in perms or []
                             if p.get("type") == "group" and p.get("emailAddress")}),
           "domains": sorted({p["domain"].lower() for p in perms or []
                              if p.get("type") == "domain" and p.get("domain")}),
           "anyone": any(p.get("type") == "anyone" for p in perms or [])}
    if perms is None:
        acl["partial"] = True
    return acl


def _item(f):
    return Item(source_id=f["id"], title=f.get("name") or "Untitled",
                url=f.get("webViewLink") or "", version=f.get("modifiedTime") or "",
                kind="file", mime=f.get("mimeType") or "", modified=f.get("modifiedTime"),
                size=int(f["size"]) if f.get("size") else None,
                parent_id=(f.get("parents") or [None])[0], acl=_acl(f))


def _roots_signature(roots):
    return hashlib.sha256(json.dumps(sorted((r.get("kind", "folder"), str(r["id"]))
                                            for r in roots)).encode()).hexdigest()[:16]


class GoogleDrive(Connector):
    PROVIDER = PROVIDER
    LABEL = "Google Drive"
    DESCRIPTION = "Docs, Sheets, Slides, PDFs, Office and text files"
    AUTH = "oauth"
    PICKER = "tree"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "folders"
    UNIT = "files"
    SETUP_ENV = "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET"
    ACL = True

    def configured(self):
        return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))

    def auth_url(self, redirect_uri, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "access_type": "offline",        # we need a refresh token for background sync
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        })

    def finish_connect(self, workspace, code, redirect_uri, user):
        """Exchange the OAuth code for tokens and save the (encrypted) connection."""
        r = http.request("POST", TOKEN_URL, retries=1, data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        tokens = r.json()
        if not tokens.get("refresh_token"):
            raise ConfigError("Google did not return a refresh token")
        info = http.request("GET", USERINFO_URL, retries=1, headers={
            "Authorization": f"Bearer {tokens['access_token']}"}).json()
        store.connect(workspace, PROVIDER, info.get("email", "Google account"), {
            "refresh_token": tokens["refresh_token"],
            "access_token": tokens["access_token"],
            "expires_at": time.time() + int(tokens.get("expires_in", 3600)) - 60,
        }, user)

    # ---- tokens and calls -------------------------------------------------------------
    def _access_token(self, workspace, force=False):
        """A valid access token, refreshed (under a lock) when it has expired."""
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = store.secret(workspace, PROVIDER)
            if not sec:
                raise AuthExpired("Google Drive is not connected")
            if not force and sec.get("access_token") and sec.get("expires_at", 0) > time.time():
                return sec["access_token"], sec["refresh_token"]
            r = http.request("POST", TOKEN_URL, ok=(400, 401), data={
                "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
                "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
                "refresh_token": sec["refresh_token"],
                "grant_type": "refresh_token",
            })
            if r.status_code in (400, 401):       # invalid_grant: revoked, expired, or reset
                raise AuthExpired("Google refused the refresh token")
            t = r.json()
            sec.update(access_token=t["access_token"],
                       expires_at=time.time() + int(t.get("expires_in", 3600)) - 60)
            if t.get("refresh_token"):            # Google may hand out a new one
                sec["refresh_token"] = t["refresh_token"]
            store.update(workspace, PROVIDER, secret=sec)
            return sec["access_token"], sec["refresh_token"]

    def _call(self, workspace, method, url, **kwargs):
        """One Drive API call; on a 401, refresh the token once and try again."""
        for attempt in (0, 1):
            token, refresh = self._access_token(workspace, force=attempt == 1)
            try:
                return http.request(method, url, bucket=http.bucket_key(PROVIDER, refresh),
                                    rate=RATE, burst=RATE,
                                    headers={"Authorization": f"Bearer {token}"}, **kwargs)
            except AuthExpired:
                if attempt == 1:
                    raise

    def _query(self, workspace, q, fields, order="name"):
        """Every result of one files.list query, a page at a time."""
        page = None
        while True:
            params = {"q": q, "pageSize": 200, "orderBy": order,
                      "fields": f"nextPageToken, files({fields})",
                      "includeItemsFromAllDrives": "true", "supportsAllDrives": "true",
                      "corpora": "allDrives"}
            if page:
                params["pageToken"] = page
            data = self._call(workspace, "GET", FILES_URL, params=params).json()
            yield from data.get("files", [])
            page = data.get("nextPageToken")
            if not page:
                return

    # ---- picking ---------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        """What can be picked inside `parent` (None = top level: Shared Drives + the
        root of My Drive): its sub-folders and its supported files."""
        ws = ctx.workspace
        out = []
        if parent is None:
            try:
                data = self._call(ws, "GET", DRIVES_URL,
                                  params={"pageSize": 100, "fields": "drives(id, name)"}).json()
                out += [{"id": d["id"], "name": d["name"], "kind": "drive", "has_children": True}
                        for d in data.get("drives", [])]
            except http.Forbidden:
                pass                       # no Shared Drives for this account
        where = f"trashed = false and '{parent or 'root'}' in parents"
        items = list(self._query(ws, f"{where} and (mimeType = '{FOLDER}' or {_WANTED})",
                                 "id, name, mimeType"))
        out += [{"id": f["id"], "name": f["name"], "kind": "folder", "has_children": True}
                for f in items if f["mimeType"] == FOLDER]
        out += [{"id": f["id"], "name": f["name"], "kind": "file", "mimeType": f["mimeType"]}
                for f in items if f["mimeType"] != FOLDER]
        return out

    # ---- syncing ---------------------------------------------------------------------
    def _start_token(self, workspace):
        return self._call(workspace, "GET", f"{API}/changes/startPageToken",
                          params={"supportsAllDrives": "true"}).json()["startPageToken"]

    def changes(self, ctx, cursor):
        roots = (ctx.settings or {}).get("folders") or []
        if not roots:                       # nothing picked: nothing is synced
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        sig = _roots_signature(roots)
        state = json.loads(cursor) if cursor else None
        if ctx.full or not state or state.get("roots") != sig:
            yield from self._full(ctx, roots, sig)
            return
        folders = set(state.get("folders") or [])
        files = {str(r["id"]) for r in roots if r.get("kind") == "file"}
        pinned = {str(r["id"]) for r in roots if r.get("kind") != "file"}
        page, new_token, relist = state["token"], None, False
        while page:
            data = self._call(ctx.workspace, "GET", f"{API}/changes", params={
                "pageToken": page, "pageSize": 500, "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true", "includeRemoved": "true",
                "fields": f"nextPageToken, newStartPageToken, changes(fileId, removed, "
                          f"file({_FILE_FIELDS}))"}).json()
            for ch in data.get("changes", []):
                fid, f = ch.get("fileId"), ch.get("file")
                if not fid:
                    continue                # a Shared Drive itself changed
                gone = ch.get("removed") or not f or f.get("trashed")
                if (f and f.get("mimeType") == FOLDER) or fid in folders:
                    # The picked tree changed shape if a folder in it moved out or went
                    # away, or one moved in (its files didn't change, so they aren't
                    # in this feed): list everything again. A rename changes nothing.
                    inside = bool(set((f or {}).get("parents") or []) & folders)
                    was = fid in folders
                    if (was and fid not in pinned and (gone or not inside)) or \
                            (not was and not gone and inside):
                        relist = True
                    continue
                if gone:
                    if fid in ctx.known:
                        yield Delete(fid)
                    continue
                in_scope = fid in files or bool(set(f.get("parents") or []) & folders)
                if in_scope and f.get("mimeType") in SUPPORTED:
                    yield Upsert(_item(f))
                elif fid in ctx.known:      # moved out of the picks, or no longer readable
                    yield Delete(fid)
            page = data.get("nextPageToken")
            new_token = data.get("newStartPageToken") or new_token
        if relist:
            yield from self._full(ctx, roots, sig)
            return
        yield Cursor(json.dumps({"token": new_token or state["token"], "roots": sig,
                                 "folders": sorted(folders)}))

    def _full(self, ctx, roots, sig):
        """Every supported file under the picks; deletes for anything known that
        isn't there any more, unless the listing stopped at the limit."""
        ws = ctx.workspace
        token = self._start_token(ws)          # first: changes from now on aren't lost
        seen, folders, n = set(), set(), 0
        queue = [str(r["id"]) for r in roots if r.get("kind") != "file"]
        for r in roots:
            if r.get("kind") != "file":
                continue
            try:
                f = self._call(ws, "GET", f"{FILES_URL}/{r['id']}",
                               params={"fields": _FILE_FIELDS, "supportsAllDrives": "true"}).json()
            except (http.NotFound, http.Forbidden):
                continue                       # deleted or no longer shared: dropped below
            if not f.get("trashed") and f.get("mimeType") in SUPPORTED:
                seen.add(f["id"])
                n += 1
                yield Upsert(_item(f))
        while queue:
            folder = queue.pop(0)
            if folder in folders:
                continue
            folders.add(folder)
            q = f"'{folder}' in parents and trashed = false and ({_WANTED} or mimeType = '{FOLDER}')"
            try:
                listing = list(self._query(ws, q, _FILE_FIELDS, order="modifiedTime desc"))
            except (http.NotFound, http.Forbidden):
                if folder in {str(r["id"]) for r in roots}:
                    raise ConfigError("a picked folder is gone or no longer shared") from None
                continue
            for f in listing:
                if f["mimeType"] == FOLDER:
                    queue.append(f["id"])
                    continue
                if f["id"] in seen:
                    continue
                if ctx.max_items and n >= ctx.max_items:
                    # Stopped early: what wasn't listed isn't deleted either.
                    yield LimitReached(ctx.max_items)
                    yield Cursor(json.dumps({"token": token, "roots": sig,
                                             "folders": sorted(folders)}))
                    return
                seen.add(f["id"])
                n += 1
                if int(f.get("size") or 0) > MAX_BYTES:
                    yield Skipped(f["id"], "too_large", f.get("name", ""), f.get("webViewLink", ""))
                else:
                    yield Upsert(_item(f))
        yield from ctx.missing(seen)
        yield Cursor(json.dumps({"token": token, "roots": sig, "folders": sorted(folders)}),
                     full=True)

    def fetch(self, ctx, item):
        """The file's content: Google formats exported as text / CSV, the rest as is."""
        if item.size and item.size > MAX_BYTES:
            return None
        if item.mime in EXPORT:
            r = self._call(ctx.workspace, "GET", f"{FILES_URL}/{item.source_id}/export",
                           params={"mimeType": EXPORT[item.mime]}, timeout=60)
            return Fetched(r.content, EXPORT[item.mime])
        r = self._call(ctx.workspace, "GET", f"{FILES_URL}/{item.source_id}",
                       params={"alt": "media", "supportsAllDrives": "true"}, timeout=60)
        return Fetched(r.content, item.mime)

    def disconnect(self, workspace):
        """Revoke Google's access (our side is removed by the caller)."""
        sec = store.secret(workspace, PROVIDER)
        if sec and sec.get("refresh_token"):
            try:
                http.request("POST", REVOKE_URL, retries=1, params={"token": sec["refresh_token"]})
            except Exception as e:
                log.warning("Google Drive: couldn't revoke the token of workspace %s (%s); "
                            "remove the app in the Google account if it matters",
                            workspace, type(e).__name__)


CONNECTOR = GoogleDrive()
