"""
connectors/notion.py  —  sync a workspace's Notion pages into its index.

Read-only. WHAT gets synced is chosen inside Notion itself: on the "Connect
Notion" screen the admin picks the pages / databases to share, and only those
(plus their sub-pages and database rows) are visible to us. To change the
selection, connect again (Notion shows the picker again).

Each page's text (headings, lists, to-dos, toggles, quotes, callouts, code,
tables, database-row properties) lands in
<workspace docs>/connectors/notion/<page id>.txt with a manifest.json entry
(title + Notion link) so answers cite "Onboarding Guide · Notion" with a link.

Needs NOTION_CLIENT_ID and NOTION_CLIENT_SECRET (a PUBLIC integration at
notion.so/my-integrations, redirect URI .../api/integrations/notion/callback,
capability: Read content).
"""

import base64
import json
import os
import time
import urllib.parse

import requests

import integrations_store as store
import workspaces

PROVIDER = "notion"
LABEL = "Notion"
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MAX_PAGES = 1000
MAX_BLOCK_DEPTH = 3                    # nested blocks (toggles, lists) to follow
REQUEST_GAP = 0.34                     # Notion allows ~3 requests / second
_last_call = [0.0]


def configured():
    return bool(os.getenv("NOTION_CLIENT_ID") and os.getenv("NOTION_CLIENT_SECRET"))


def auth_url(redirect_uri, state):
    return f"{API}/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": os.environ["NOTION_CLIENT_ID"],
        "response_type": "code",
        "owner": "user",
        "redirect_uri": redirect_uri,
        "state": state,
    })


def finish_connect(workspace, code, redirect_uri, user):
    basic = base64.b64encode(
        f"{os.environ['NOTION_CLIENT_ID']}:{os.environ['NOTION_CLIENT_SECRET']}".encode()).decode()
    r = requests.post(f"{API}/oauth/token", timeout=20,
                      headers={"Authorization": f"Basic {basic}", "Content-Type": "application/json",
                               "Notion-Version": VERSION},
                      json={"grant_type": "authorization_code", "code": code,
                            "redirect_uri": redirect_uri})
    r.raise_for_status()
    t = r.json()
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


def _token(workspace):
    sec = store.secret(workspace, PROVIDER)
    if not sec:
        raise RuntimeError("Notion is not connected.")
    return sec["access_token"]


def _call(method, path, token, **kw):
    """One Notion API call, spaced to the rate limit, retrying on 429 / 5xx."""
    for attempt in range(5):
        wait = REQUEST_GAP - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        r = requests.request(method, f"{API}{path}", timeout=30, headers={
            "Authorization": f"Bearer {token}", "Notion-Version": VERSION,
            "Content-Type": "application/json"}, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After", 1 + attempt)))
            continue
        if r.status_code == 401:
            raise RuntimeError("Notion access was revoked. Reconnect Notion.")
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def _plain(rich):
    return "".join(x.get("plain_text", "") for x in rich or [])


def _title(page):
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return _plain(prop.get("title")) or "Untitled"
    return "Untitled"


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


def _blocks_text(token, block_id, depth=0):
    lines, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        try:
            data = _call("GET", f"/blocks/{block_id}/children", token, params=params)
        except requests.HTTPError as e:
            # Some blocks can't be read even though the page is shared: synced
            # blocks whose original lives on an unshared page, linked databases,
            # newer block types (AI, meeting notes). Skip that part, keep the rest.
            if e.response is not None and e.response.status_code in (400, 403, 404):
                return [l for l in lines if l.strip()]
            raise
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
                lines += _blocks_text(token, b["id"], depth + 1)
        if not data.get("has_more"):
            return [l for l in lines if l.strip()]
        cursor = data.get("next_cursor")


def _shared_pages(token):
    """Every page the integration was given access to (sub-pages and database
    rows included — Notion's search returns them all)."""
    pages, cursor = [], None
    while len(pages) < MAX_PAGES:
        body = {"page_size": 100, "filter": {"property": "object", "value": "page"}}
        if cursor:
            body["start_cursor"] = cursor
        data = _call("POST", "/search", token, json=body)
        pages += [p for p in data.get("results", [])
                  if not p.get("archived") and not p.get("in_trash")]
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return pages[:MAX_PAGES]


def _page_text(token, page, title):
    lines = [title, ""]
    if (page.get("parent") or {}).get("type") == "database_id":
        for name, prop in (page.get("properties") or {}).items():
            if prop.get("type") != "title":
                value = _property_text(prop)
                if value:
                    lines.append(f"{name}: {value}")
        lines.append("")
    lines += _blocks_text(token, page["id"])
    return "\n".join(lines)


def sync(workspace):
    """Bring this workspace's Notion copy up to date. Returns (count, changed)."""
    token = _token(workspace)
    folder = workspaces.connector_dir(workspace, PROVIDER)
    os.makedirs(folder, exist_ok=True)
    manifest_path = os.path.join(folder, "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        manifest = {}

    changed, keep = False, {}
    for page in _shared_pages(token):
        name = f"{page['id']}.txt"
        title = _title(page)
        known = manifest.get(name)
        if known and known.get("modified") == page.get("last_edited_time") and \
                os.path.exists(os.path.join(folder, name)):
            if known.get("title") != title or known.get("url") != page.get("url"):
                known = dict(known, title=title, url=page.get("url"))
                changed = True
            keep[name] = known
            continue
        text = _page_text(token, page, title)
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        keep[name] = {"id": page["id"], "title": title, "url": page.get("url"),
                      "modified": page.get("last_edited_time"), "provider": LABEL}
        changed = True

    # Remove anything not in the current list: unshared / deleted items, plus
    # stray files left by an interrupted sync (they'd be indexed untitled).
    stale = (set(manifest) | set(os.listdir(folder))) - set(keep) - {"manifest.json"}
    for name in stale:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
        changed = True

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(keep, fh, indent=1)
    return len(keep), changed


def disconnect(workspace):
    """Delete the synced copies and the connection. (To fully remove our access
    on Notion's side, the admin can also remove the integration in Notion:
    Settings -> Connections.)"""
    folder = workspaces.connector_dir(workspace, PROVIDER)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
    store.remove(workspace, PROVIDER)
