"""
connectors/clickup.py  —  sync a workspace's ClickUp tasks and Docs into its index.

Read-only, as the user who connects (OAuth: ClickUp asks them which of their
ClickUp Workspaces to authorize; there are no scopes). ClickUp's OAuth access
tokens don't expire at the moment ("subject to change"); a 401 later means the
user revoked the app (OAUTH_019 / 021 / 025 / 077) or a Workspace was taken out
of the authorization (OAUTH_023 ...): reconnect.

What is synced (nothing until an admin picks on the card):
  containers  a ClickUp Workspace (everything in it), Spaces, Folders or Lists;
  tasks       of the Lists in scope, open and closed, subtasks included: name, key
              facts (List, status, assignees, due date), the Markdown description
              and the comments, as Markdown;
  Docs        whose parent (a Space, Folder or List; "Everything" only with the
              whole Workspace picked) is in scope: every page, exported as
              Markdown (content_format=text/md), as one document per Doc.

How a sync finds what changed:
  tasks  GET /team/{id}/task (Get Filtered Team Tasks) with list_ids[], ordered by
         date_updated; INCREMENTAL: date_updated_gt the last run's start (ms) less
         OVERLAP. FULL (first run, picks or Lists changed, every RECONCILE seconds,
         asked for): every task; anything known but not listed was deleted or
         moved out and goes behind the mass-delete guard.
  Docs   ClickUp has no Doc webhooks or change feed: every run lists the
         Workspace's Docs (cheap) and a Doc whose date_updated moved is exported
         again (skip-if-unchanged does the rest); a deleted or archived Doc is
         deleted.

Permissions: a task is shared with its List's members, as ClickUp lists them
(GET /list/{id}/member: explicit members; access inherited from a Space, Folder
or Team isn't in it, so never more people than ClickUp names). A Doc on a List
takes that List's members; on a PRIVATE Space, the Space's members; anywhere
else (a Folder, a public Space, Everything) its readers can't be read: partial,
for no one under the source policy (kb/services/permissions.py).

Rate limits: per token, by the plan of the Workspace: 100 requests a minute
(Free, Unlimited, Business), 1,000 (Business Plus), 10,000 (Enterprise); a 429
carries X-RateLimit-Reset (Unix time), which connectors/http.py waits for.
Calls are spaced at RATE a second per token.

Needs CLICKUP_CLIENT_ID and CLICKUP_CLIENT_SECRET: an OAuth app (ClickUp
Settings -> Apps) with the redirect URL <PUBLIC_URL>/api/integrations/clickup/callback.

API references (checked 2026-10-27):
  OAuth         https://developer.clickup.com/docs/authentication (authorize at
                https://app.clickup.com/api?client_id&redirect_uri&state; no scopes; tokens
                don't expire currently; Authorization: Bearer <token>)
                https://developer.clickup.com/reference/getaccesstoken (POST
                https://api.clickup.com/api/v2/oauth/token: client_id, client_secret, code in
                the body -> {access_token})
  workspaces    https://developer.clickup.com/reference/getauthorizedteams
  hierarchy     https://developer.clickup.com/reference/getspaces (private, members),
                https://developer.clickup.com/reference/getfolders,
                https://developer.clickup.com/reference/getlists,
                https://developer.clickup.com/reference/getfolderlesslists
  members       https://developer.clickup.com/reference/getlistmembers
  tasks         https://developer.clickup.com/reference/getfilteredteamtasks (page from 0,
                100 a page, list_ids[], date_updated_gt in ms, include_closed, subtasks,
                include_markdown_description, order_by updated)
  comments      https://developer.clickup.com/reference/gettaskcomments (25 newest first;
                start + start_id for older ones)
  Docs          https://developer.clickup.com/reference/searchdocspublic (limit 100, cursor,
                next_cursor; parent.type 4 Space, 5 Folder, 6 List, 7 Everything; deleted,
                archived, date_updated),
                https://developer.clickup.com/reference/getdocpagespublic
                (content_format=text/md, max_page_depth=-1; nested pages)
  limits        https://developer.clickup.com/docs/rate-limits
  errors        https://developer.clickup.com/docs/common_errors
  webhooks      https://developer.clickup.com/docs/webhooks (no Doc events)
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
                             Forbidden, FormError, Item, LimitReached, NotFound, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "clickup"
AUTH_URL = "https://app.clickup.com/api"
API = "https://api.clickup.com/api"
TOKEN_URL = f"{API}/v2/oauth/token"
RATE = 1.5                             # calls a second per token (100 a minute)
TASK_PAGE = 100
LISTS_A_CALL = 20                      # list_ids[] a task query carries
RECONCILE = 24 * 60 * 60
OVERLAP = 120 * 1000                   # ms
PICK_KINDS = {"workspace": "category", "space": "space", "folder": "folder", "list": "project"}
PARENT_TYPES = {4: "space", 5: "folder", 6: "list", 7: "workspace"}


class ClickUp(Connector):
    PROVIDER = PROVIDER
    LABEL = "ClickUp"
    DESCRIPTION = "Tasks with comments, and Docs, of the Spaces, Folders and Lists you pick"
    AUTH = "oauth"
    PICKER = "tree"
    PICKER_REQUIRED = True
    UNIT = "items"
    SETUP_ENV = "CLICKUP_CLIENT_ID / CLICKUP_CLIENT_SECRET"
    ACL = True

    def configured(self):
        return bool(os.getenv("CLICKUP_CLIENT_ID") and os.getenv("CLICKUP_CLIENT_SECRET"))

    # ---- connecting ---------------------------------------------------------------
    def auth_url(self, redirect_uri, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": os.environ["CLICKUP_CLIENT_ID"], "redirect_uri": redirect_uri,
            "state": state})

    def finish_connect(self, workspace, code, redirect_uri, user):
        r = http.request("POST", TOKEN_URL, retries=1, ok=(400, 401), json={
            "client_id": os.environ.get("CLICKUP_CLIENT_ID", ""),
            "client_secret": os.environ.get("CLICKUP_CLIENT_SECRET", ""), "code": code})
        if r.status_code >= 400 or not (r.json() or {}).get("access_token"):
            raise ConfigError(f"ClickUp refused the code (HTTP {r.status_code})")
        token = r.json()["access_token"]
        me = (self._call_token(token, "GET", "/v2/user").json() or {}).get("user") or {}
        teams = (self._call_token(token, "GET", "/v2/team").json() or {}).get("teams") or []
        account = me.get("email") or me.get("username") or "ClickUp"
        if teams:
            account = f"{', '.join(t.get('name') or t['id'] for t in teams[:3])} ({account})"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, {"access_token": token}, user)
        ids = sorted(str(t["id"]) for t in teams)
        if row and (row.get("settings") or {}).get("teams") != ids:
            store.update(workspace, PROVIDER, settings=dict(row.get("settings") or {},
                                                            containers=[], teams=ids))
        elif not row:
            store.update(workspace, PROVIDER, settings={"teams": ids})

    # ---- calls --------------------------------------------------------------------
    def _call_token(self, token, method, path, params=None):
        return http.request(method, f"{API}{path}", params=params,
                            bucket=http.bucket_key(PROVIDER, token), rate=RATE, burst=3,
                            headers={"Authorization": f"Bearer {token}",
                                     "Accept": "application/json"})

    def _get(self, ws, path, params=None):
        sec = store.secret(ws, PROVIDER)
        if not sec or not sec.get("access_token"):
            raise AuthExpired("ClickUp is not connected")
        r = self._call_token(sec["access_token"], "GET", path, params)
        try:
            return r.json() or {}
        except ValueError:
            raise ConfigError("the answer isn't JSON") from None

    # ---- the hierarchy ------------------------------------------------------------
    def _teams(self, ws):
        return self._get(ws, "/v2/team").get("teams") or []

    def _spaces(self, ws, team):
        return self._get(ws, f"/v2/team/{team}/space", {"archived": "false"}).get("spaces") or []

    def _folders(self, ws, space):
        return self._get(ws, f"/v2/space/{space}/folder",
                         {"archived": "false"}).get("folders") or []

    def _lists(self, ws, parent_kind, pid):
        path = f"/v2/folder/{pid}/list" if parent_kind == "folder" else f"/v2/space/{pid}/list"
        return self._get(ws, path, {"archived": "false"}).get("lists") or []

    def list_containers(self, ctx, parent=None):
        ws = ctx.workspace
        if not parent:
            out = [{"id": f"workspace-{t['id']}", "name": t.get("name") or str(t["id"]),
                    "kind": "category", "has_children": True} for t in self._teams(ws)]
        else:
            kind, _, pid = parent.partition("-")
            if kind == "workspace":
                out = [{"id": f"space-{s['id']}", "name": s.get("name") or str(s["id"]),
                        "kind": "space", "has_children": True} for s in self._spaces(ws, pid)]
            elif kind == "space":
                out = ([{"id": f"folder-{f['id']}", "name": f.get("name") or str(f["id"]),
                         "kind": "folder", "has_children": True}
                        for f in self._folders(ws, pid)]
                       + [{"id": f"list-{x['id']}", "name": x.get("name") or str(x["id"]),
                           "kind": "project"} for x in self._lists(ws, "space", pid)])
            elif kind == "folder":
                out = [{"id": f"list-{x['id']}", "name": x.get("name") or str(x["id"]),
                        "kind": "project"} for x in self._lists(ws, "folder", pid)]
            else:
                out = []
        return sorted(out, key=lambda c: (c["kind"] == "project", c["name"].lower()))

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            kind, _, pid = p["id"].partition("-")
            if kind not in PICK_KINDS or not pid:
                raise FormError("Pick ClickUp Workspaces, Spaces, Folders or Lists.")
            p["kind"] = PICK_KINDS[kind]
        return out

    def _scope(self, ws, picks):
        """What the picks cover: {"team": {team ids}, "space": {...}, "folder": {...},
        "list": {list id: (team, space id)}, "private": {space id: [emails]}}."""
        scope = {"team": set(), "space": set(), "folder": set(), "list": {}, "private": {}}
        picked = {k: {p["id"].partition("-")[2] for p in picks if p["id"].startswith(k + "-")}
                  for k in PICK_KINDS}
        for team in self._teams(ws):
            tid = str(team["id"])
            whole = tid in picked["workspace"]
            if whole:
                scope["team"].add(tid)
            for space in self._spaces(ws, tid):
                sid = str(space["id"])
                if space.get("private"):
                    scope["private"][sid] = sorted({
                        ((m.get("user") or {}).get("email") or "").lower()
                        for m in space.get("members") or []} - {""})
                space_in = whole or sid in picked["space"]
                if space_in:
                    scope["space"].add(sid)
                for folder in self._folders(ws, sid):
                    fid = str(folder["id"])
                    folder_in = space_in or fid in picked["folder"]
                    if folder_in:
                        scope["folder"].add(fid)
                    for lst in folder.get("lists") or self._lists(ws, "folder", fid):
                        if folder_in or str(lst["id"]) in picked["list"]:
                            scope["list"][str(lst["id"])] = (tid, sid)
                for lst in self._lists(ws, "space", sid):
                    if space_in or str(lst["id"]) in picked["list"]:
                        scope["list"][str(lst["id"])] = (tid, sid)
        return scope

    def _members(self, ws, list_id, cache):
        if list_id not in cache:
            try:
                members = self._get(ws, f"/v2/list/{list_id}/member").get("members") or []
                cache[list_id] = sorted({(m.get("email") or "").lower()
                                         for m in members} - {""})
            except (Forbidden, NotFound):
                cache[list_id] = None
        return cache[list_id]

    def _list_acl(self, ws, list_id, cache):
        users = self._members(ws, list_id, cache)
        if users is None:
            return {"via": PROVIDER, "list": list_id, "partial": True}
        return {"via": PROVIDER, "list": list_id, "users": users}

    # ---- syncing ------------------------------------------------------------------
    def changes(self, ctx, cursor):
        picks = (ctx.settings or {}).get("containers") or []
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        scope = self._scope(ws, picks)
        sig = hashlib.sha256(json.dumps(sorted(scope["list"])).encode()).hexdigest()[:16]
        started = int(time.time() * 1000)
        full = (ctx.full or not state or state.get("lists") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        since = None if full else state.get("since")
        full = full or not since
        cache, seen, n = {}, set(), 0

        def over():
            return ctx.max_items and n >= ctx.max_items

        by_team = {}
        for lid, (tid, _sid) in scope["list"].items():
            by_team.setdefault(tid, []).append(lid)
        for tid, lists in sorted(by_team.items()):
            lists.sort()
            for i in range(0, len(lists), LISTS_A_CALL):
                for t in self._tasks(ws, tid, lists[i:i + LISTS_A_CALL],
                                     None if full else since - OVERLAP):
                    lid = str((t.get("list") or {}).get("id") or "")
                    sid = f"task-{t['id']}"
                    if lid not in scope["list"]:
                        if sid in ctx.known and not full:
                            yield Delete(sid)     # moved out of the picks
                        continue
                    if over():
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)
                        return
                    seen.add(sid)
                    n += 1
                    yield Upsert(self._task_item(t, self._list_acl(ws, lid, cache)))
        teams = sorted({tid for tid, _ in scope["list"].values()} | scope["team"])
        for tid in teams:
            for d in self._docs(ws, tid):
                sid = f"doc-{d['id']}"
                if d.get("deleted") or d.get("archived"):
                    if sid in ctx.known:
                        yield Delete(sid)
                    continue
                acl = self._doc_acl(ws, d, tid, scope, cache)
                if acl is None:
                    continue                      # not under the picks
                if over():
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)
                    return
                seen.add(sid)
                n += 1
                yield Upsert(Item(
                    source_id=sid, title=d.get("name") or "Untitled Doc",
                    url=f"https://app.clickup.com/{tid}/v/dc/{d['id']}",
                    version=str(d.get("date_updated") or ""), kind="page",
                    mime="text/markdown", modified=_iso(d.get("date_updated")),
                    parent_id=f"{(d.get('parent') or {}).get('id') or tid}", acl=acl,
                    attrs={"id": str(d["id"]), "team": tid}))
        if full:
            yield from ctx.missing(seen)
        else:
            # Docs are listed whole every run: a known Doc missing from the listing is gone.
            listed_docs = {s for s in seen if s.startswith("doc-")}
            for sid in sorted(ctx.known):
                if sid.startswith("doc-") and sid not in listed_docs:
                    yield Delete(sid)
        yield Cursor(json.dumps({"lists": sig, "since": started,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def _tasks(self, ws, team, lists, since_ms):
        page = 0
        while True:
            params = [("page", page), ("order_by", "updated"), ("include_closed", "true"),
                      ("subtasks", "true"), ("include_markdown_description", "true")]
            params += [("list_ids[]", lid) for lid in lists]
            if since_ms:
                params.append(("date_updated_gt", int(since_ms)))
            tasks = self._get(ws, f"/v2/team/{team}/task", params).get("tasks") or []
            yield from tasks
            if len(tasks) < TASK_PAGE:            # (no last_page on this endpoint)
                return
            page += 1

    def _docs(self, ws, team):
        params = {"limit": 100}
        while True:
            data = self._get(ws, f"/v3/workspaces/{team}/docs", params)
            yield from data.get("docs") or []
            cursor = data.get("next_cursor")
            if not cursor:
                return
            params = {"limit": 100, "cursor": cursor}

    def _doc_acl(self, ws, d, team, scope, cache):
        """The Doc's acl, or None when it isn't under the picks."""
        parent = d.get("parent") or {}
        kind, pid = PARENT_TYPES.get(parent.get("type")), str(parent.get("id") or "")
        if kind == "list" and pid in scope["list"]:
            return self._list_acl(ws, pid, cache)
        if kind == "space" and pid in scope["space"]:
            if pid in scope["private"]:
                return {"via": PROVIDER, "list": f"space:{pid}", "users": scope["private"][pid]}
            return {"via": PROVIDER, "list": None, "partial": True}
        if kind == "folder" and pid in scope["folder"]:
            return {"via": PROVIDER, "list": None, "partial": True}
        if kind == "workspace" and team in scope["team"]:
            return {"via": PROVIDER, "list": None, "partial": True}
        return None

    @staticmethod
    def _task_item(t, acl):
        attrs = {"id": str(t["id"]), "list": (t.get("list") or {}).get("name"),
                 "folder": (t.get("folder") or {}).get("name"),
                 "status": (t.get("status") or {}).get("status"),
                 "assignees": [a.get("username") for a in t.get("assignees") or []
                               if a.get("username")],
                 "due": _iso(t.get("due_date")), "created": _iso(t.get("date_created")),
                 "parent": t.get("parent")}
        return Item(source_id=f"task-{t['id']}", title=t.get("name") or "Untitled task",
                    url=t.get("url") or "", version=str(t.get("date_updated") or ""),
                    kind="issue", mime="text/markdown", modified=_iso(t.get("date_updated")),
                    parent_id=f"list-{(t.get('list') or {}).get('id')}", acl=acl,
                    attrs={k: v for k, v in attrs.items() if v},
                    content={"description": t.get("markdown_description")
                             or t.get("text_content") or t.get("description") or ""})

    def fetch(self, ctx, item):
        ws = ctx.workspace
        a = item.attrs or {}
        if item.kind == "page":
            pages = self._get(ws, f"/v3/workspaces/{a['team']}/docs/{a['id']}/pages",
                              {"content_format": "text/md", "max_page_depth": -1})
            parts = []

            def walk(nodes, depth):
                for p in nodes or []:
                    if p.get("deleted") or p.get("archived"):
                        continue
                    if (p.get("content") or "").strip() or p.get("name"):
                        parts.append(f"{'#' * min(depth + 1, 4)} {p.get('name') or 'Untitled'}"
                                     f"\n\n{(p.get('content') or '').strip()}")
                    walk(p.get("pages"), depth + 1)

            walk(pages if isinstance(pages, list) else pages.get("pages"), 1)
            return Fetched("\n\n".join(parts), "text/markdown")
        facts = [("List", a.get("list")), ("Folder", a.get("folder")),
                 ("Status", a.get("status")),
                 ("Assignees", ", ".join(a.get("assignees") or []) or None),
                 ("Due", (a.get("due") or "")[:10] or None),
                 ("Created", (a.get("created") or "")[:10] or None)]
        parts = ["\n".join(f"{k}: {v}" for k, v in facts if v)]
        description = ((item.content or {}).get("description") or "").strip()
        if description:
            parts.append("## Description\n\n" + description)
        comments = self._comments(ws, a["id"])
        lines = [f"### {(c.get('user') or {}).get('username') or 'Someone'}, "
                 f"{(_iso(c.get('date')) or '')[:10]}\n\n{c['comment_text'].strip()}"
                 for c in sorted(comments, key=lambda c: int(c.get("date") or 0))
                 if (c.get("comment_text") or "").strip()]
        if lines:
            parts.append("## Comments\n\n" + "\n\n".join(lines))
        return Fetched("\n\n".join(p for p in parts if p), "text/markdown")

    def _comments(self, ws, task):
        """Every comment: 25 a page, newest first; older ones by start + start_id."""
        out, params, ids = [], {}, set()
        while True:
            page = self._get(ws, f"/v2/task/{task}/comment", params).get("comments") or []
            page = [c for c in page if c.get("id") not in ids]
            ids |= {c.get("id") for c in page}
            out += page
            if len(page) < 25:
                return out
            last = page[-1]
            params = {"start": last.get("date"), "start_id": last.get("id")}

    def disconnect(self, workspace):
        """ClickUp documents no revoke call: the stored token is deleted here; the user
        removes the app in ClickUp (Settings -> Apps) to cut access at the source."""
        log.info("ClickUp: disconnected workspace %s; remove the app in ClickUp to revoke its "
                 "access there", workspace)


def _iso(ms):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))
    except (TypeError, ValueError):
        return None


CONNECTOR = ClickUp()
