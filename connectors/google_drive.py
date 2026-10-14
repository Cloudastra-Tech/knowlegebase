"""
connectors/google_drive.py  —  sync a workspace's Google Drive into its index.

Read-only: we only ever LIST and DOWNLOAD files (scope drive.readonly).
What gets synced: Google Docs (exported as text), Google Sheets (as CSV text),
PDFs and plain-text files the connected account can see, up to MAX_FILES.

Files land in  <workspace docs>/connectors/google_drive/  next to a
manifest.json (file -> title, Drive link), which ingest.py uses so answers can
cite the Drive file by name with a link.

Needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (an OAuth client of type
"Web application" in Google Cloud, redirect URI .../api/integrations/google_drive/callback).
"""

import json
import os
import time
import urllib.parse

import requests

import integrations_store as store
import workspaces

PROVIDER = "google_drive"
SCOPES = "openid email https://www.googleapis.com/auth/drive.readonly"
MAX_FILES = 500
MAX_BYTES = 20 * 1024 * 1024          # skip files bigger than 20 MB

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
FILES_URL = "https://www.googleapis.com/drive/v3/files"

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
PDF = "application/pdf"
TEXT = "text/plain"


def configured():
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def auth_url(redirect_uri, state):
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


def finish_connect(workspace, code, redirect_uri, user):
    """Exchange the OAuth code for tokens and save the (encrypted) connection."""
    r = requests.post(TOKEN_URL, timeout=20, data={
        "code": code,
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    r.raise_for_status()
    tokens = r.json()
    if not tokens.get("refresh_token"):
        raise RuntimeError("Google did not return a refresh token. Remove the app's access "
                           "in your Google account settings and connect again.")
    info = requests.get(USERINFO_URL, timeout=20,
                        headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    store.connect(workspace, PROVIDER, info.get("email", "Google account"), {
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens["access_token"],
        "expires_at": time.time() + int(tokens.get("expires_in", 3600)) - 60,
    }, user)


def _access_token(workspace):
    sec = store.secret(workspace, PROVIDER)
    if not sec:
        raise RuntimeError("Google Drive is not connected.")
    if sec.get("access_token") and sec.get("expires_at", 0) > time.time():
        return sec["access_token"]
    r = requests.post(TOKEN_URL, timeout=20, data={
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": sec["refresh_token"],
        "grant_type": "refresh_token",
    })
    if r.status_code == 400:
        raise RuntimeError("Google access was revoked or expired. Reconnect Google Drive.")
    r.raise_for_status()
    t = r.json()
    sec.update(access_token=t["access_token"],
               expires_at=time.time() + int(t.get("expires_in", 3600)) - 60)
    store.update(workspace, PROVIDER, secret=sec)
    return sec["access_token"]


def _list_files(token):
    q = (f"trashed = false and (mimeType = '{DOC}' or mimeType = '{SHEET}' "
         f"or mimeType = '{PDF}' or mimeType = '{TEXT}')")
    files, page = [], None
    while len(files) < MAX_FILES:
        params = {"q": q, "pageSize": 200, "orderBy": "modifiedTime desc",
                  "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink, size)",
                  "includeItemsFromAllDrives": "true", "supportsAllDrives": "true"}
        if page:
            params["pageToken"] = page
        r = requests.get(FILES_URL, params=params, timeout=30,
                         headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        data = r.json()
        files += data.get("files", [])
        page = data.get("nextPageToken")
        if not page:
            break
    return files[:MAX_FILES]


def _download(token, f):
    """Return (extension, bytes) for one Drive file, or None to skip it."""
    headers = {"Authorization": f"Bearer {token}"}
    if f["mimeType"] in (DOC, SHEET):
        export = TEXT if f["mimeType"] == DOC else "text/csv"
        r = requests.get(f"{FILES_URL}/{f['id']}/export", params={"mimeType": export},
                         headers=headers, timeout=60)
        ext = ".txt"
    else:
        if int(f.get("size") or 0) > MAX_BYTES:
            return None
        r = requests.get(f"{FILES_URL}/{f['id']}", params={"alt": "media", "supportsAllDrives": "true"},
                         headers=headers, timeout=60)
        ext = ".pdf" if f["mimeType"] == PDF else ".txt"
    if r.status_code in (403, 404):          # e.g. export too large, no access
        return None
    r.raise_for_status()
    body = r.content
    if ext == ".txt":
        # A title line so the text carries the document's name into the index.
        body = f"{f['name']}\n\n".encode() + body
    return ext, body


def sync(workspace):
    """Bring this workspace's Drive copy up to date. Returns (count, changed)."""
    token = _access_token(workspace)
    folder = workspaces.connector_dir(workspace, PROVIDER)
    os.makedirs(folder, exist_ok=True)
    manifest_path = os.path.join(folder, "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        manifest = {}

    changed, keep = False, {}
    for f in _list_files(token):
        known = next(((k, v) for k, v in manifest.items() if v.get("id") == f["id"]), None)
        if known and known[1].get("modified") == f["modifiedTime"] and \
                os.path.exists(os.path.join(folder, known[0])):
            keep[known[0]] = known[1]
            continue
        got = _download(token, f)
        if not got:
            continue
        ext, body = got
        name = f"{f['id']}{ext}"
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(body)
        keep[name] = {"id": f["id"], "title": f["name"], "url": f.get("webViewLink"),
                      "modified": f["modifiedTime"], "provider": "Google Drive"}
        changed = True

    for name in set(manifest) - set(keep):          # removed or no longer shared
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
        changed = True

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(keep, fh, indent=1)
    return len(keep), changed


def disconnect(workspace):
    """Revoke Google's access, delete the synced copies and the connection."""
    try:
        sec = store.secret(workspace, PROVIDER)
        if sec and sec.get("refresh_token"):
            requests.post(REVOKE_URL, params={"token": sec["refresh_token"]}, timeout=15)
    except Exception:
        pass                                          # still remove our side
    folder = workspaces.connector_dir(workspace, PROVIDER)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
    store.remove(workspace, PROVIDER)
