"""
connectors/asana.py  —  sync a workspace's Asana projects (tasks with their
comments, and project briefs) into its index.

Read-only in what it does (every call is a GET), as the user who connects: only
what that user can see in Asana is synced. OAuth 2: access tokens last an hour
and come with a refresh token; refreshing starts at 80 % of the token's life,
under a per-connection lock, and a refresh token Asana hands back is saved
before the new access token is used.

The app needs Asana's FULL PERMISSIONS ("default" scope): Asana's granular
scopes don't cover project briefs, memberships or the events API yet ("scopes
are not yet available for every Asana API endpoint"), and an app registered
with full permissions can't ask for granular ones. Nothing is ever written.

What is synced (nothing until an admin picks on the card):
  containers  Asana WORKSPACES (every unarchived project in them, new ones
              included) or single PROJECTS;
  tasks       name, key facts (project, assignee, status, due date), notes, and
              the comments (stories of subtype comment_added), as Markdown;
  briefs      each project's overview (notes) and project brief, as Markdown.

How a sync finds what changed:
  FULL (first run, the picks or projects changed, every RECONCILE seconds, an
  expired events token, asked for)
      every task of every project in scope; anything known but not listed was
      deleted or moved out and goes behind the mass-delete guard.
  INCREMENTAL
      GET /tasks?project=..&modified_since=<last run's start, less OVERLAP> per
      project; the EVENTS API (one sync token per project) reports tasks
      deleted or removed from the project, which are deleted. Events tokens
      expire after 24 hours (sooner at times) and delivery is at most once: a
      412 means "too old", and that project's next run lists everything.

Permissions: an Asana project is shared with its MEMBERS: each item's acl is
the project's members' emails (GET /memberships?parent=<project>, then each
user's email), {"via": "asana", "project": gid, "users": [...]}. Team members
of the project (a whole team added) aren't expanded: left out, and never more
people than Asana lists by name. Memberships that can't be read: partial,
for no one (kb/services/permissions.py).

Rate limits: 150 requests a minute per token on free Asana domains, 1,500 on
paid; 429 with Retry-After (seconds), which connectors/http.py waits out. Calls
are spaced at RATE a second per token.

Needs ASANA_CLIENT_ID and ASANA_CLIENT_SECRET: an app in the Asana developer
console with Full permissions and the redirect URL
<PUBLIC_URL>/api/integrations/asana/callback.

API references (checked 2026-10-27):
  OAuth          https://developers.asana.com/docs/oauth
                 (GET https://app.asana.com/-/oauth_authorize: client_id, redirect_uri,
                 response_type=code, state, scope; POST https://app.asana.com/-/oauth_token,
                 form-encoded, grant_type authorization_code | refresh_token; expires_in
                 3600; data {gid, name, email}; POST https://app.asana.com/-/oauth_revoke
                 with client_id, client_secret and the REFRESH token; "default" scope =
                 full permissions, not combinable with granular scopes)
  scopes         https://developers.asana.com/docs/oauth-scopes
  workspaces     https://developers.asana.com/reference/getworkspaces
  projects       https://developers.asana.com/reference/getprojects (workspace, archived,
                 limit 1-100, offset)
                 https://developers.asana.com/reference/getproject (privacy_setting,
                 project_brief, notes, permalink_url, archived)
  brief          https://developers.asana.com/reference/getprojectbrief (title, text,
                 permalink_url; opt_fields; preview API)
  memberships    https://developers.asana.com/reference/getmemberships (parent=<project>;
                 member {gid, resource_type user | team}; access_level)
  users          https://developers.asana.com/reference/getuser (email)
  tasks          https://developers.asana.com/reference/gettasks (project, modified_since,
                 opt_fields, limit, offset)
  stories        https://developers.asana.com/reference/getstoriesfortask
                 (resource_subtype comment_added, text, created_at, created_by)
  pagination     https://developers.asana.com/docs/pagination (next_page.offset; null last)
  events         https://developers.asana.com/reference/getevents (resource, sync; 412 with
                 a new `sync` when missing or too old; action changed | added | removed |
                 deleted | undeleted; parent on added / removed; has_more; 24 h tokens)
  rate limits    https://developers.asana.com/docs/rate-limits (429 + Retry-After)
  errors         https://developers.asana.com/docs/errors
"""

import datetime
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

PROVIDER = "asana"
AUTH_URL = "https://app.asana.com/-/oauth_authorize"
TOKEN_URL = "https://app.asana.com/-/oauth_token"
REVOKE_URL = "https://app.asana.com/-/oauth_revoke"
API = "https://app.asana.com/api/1.0"
SCOPE = "default"
RATE = 2                               # calls a second per token (150 a minute on free)
PAGE = 100
RECONCILE = 24 * 60 * 60
OVERLAP = 120
RENEW_AT = 0.8
TASK_FIELDS = ("name,notes,completed,completed_at,due_on,modified_at,created_at,permalink_url,"
               "assignee.name,memberships.section.name")
PROJECT_FIELDS = "name,notes,permalink_url,modified_at,archived,project_brief,workspace.name"


def _iso_minus(watermark, seconds):
    try:
        t = datetime.datetime.fromisoformat(watermark.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return (t - datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Asana(Connector):
    PROVIDER = PROVIDER
    LABEL = "Asana"
    DESCRIPTION = "Tasks with comments, and project briefs, of the projects you pick"
    AUTH = "oauth"
    PICKER = "tree"
    PICKER_REQUIRED = True
    UNIT = "items"
    SETUP_ENV = "ASANA_CLIENT_ID / ASANA_CLIENT_SECRET"
    ACL = True

    def configured(self):
        return bool(os.getenv("ASANA_CLIENT_ID") and os.getenv("ASANA_CLIENT_SECRET"))

    # ---- connecting ---------------------------------------------------------------
    def auth_url(self, redirect_uri, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": os.environ["ASANA_CLIENT_ID"], "redirect_uri": redirect_uri,
            "response_type": "code", "state": state, "scope": SCOPE})

    def _token_call(self, data):
        r = http.request("POST", TOKEN_URL, retries=1, ok=(400, 401), data={
            **data, "client_id": os.environ.get("ASANA_CLIENT_ID", ""),
            "client_secret": os.environ.get("ASANA_CLIENT_SECRET", "")})
        if r.status_code in (400, 401):
            raise AuthExpired(f"Asana refused the {data['grant_type']} grant")
        return r.json()

    @staticmethod
    def _tokens(t, old=None):
        life = int(t.get("expires_in") or 3600)
        now = time.time()
        return {"access_token": t["access_token"],
                "refresh_token": t.get("refresh_token") or (old or {}).get("refresh_token"),
                "expires_at": now + life - 60, "renew_at": now + life * RENEW_AT}

    def finish_connect(self, workspace, code, redirect_uri, user):
        t = self._token_call({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": redirect_uri})
        if not t.get("refresh_token"):
            raise ConfigError("Asana did not return a refresh token")
        who = t.get("data") or {}
        account = who.get("email") or who.get("name") or "Asana"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, self._tokens(t), user)
        if row and (row.get("settings") or {}).get("user") != who.get("gid"):
            store.update(workspace, PROVIDER, settings=dict(row.get("settings") or {},
                                                            containers=[], user=who.get("gid")))
        elif not row:
            store.update(workspace, PROVIDER, settings={"user": who.get("gid")})

    # ---- tokens and calls ---------------------------------------------------------
    def _access_token(self, workspace, used=None):
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = store.secret(workspace, PROVIDER)
            if not sec or not sec.get("access_token"):
                raise AuthExpired("Asana is not connected")
            stale = sec.get("renew_at", 0) <= time.time()
            if not stale and (used is None or sec["access_token"] != used):
                return sec["access_token"]
            if not sec.get("refresh_token"):
                raise AuthExpired("Asana refused the token and there is no refresh token")
            sec = self._tokens(self._token_call({"grant_type": "refresh_token",
                                                 "refresh_token": sec["refresh_token"]}), sec)
            store.update(workspace, PROVIDER, secret=sec)
            return sec["access_token"]

    def _get(self, ws, path, params=None, ok=()):
        """One GET; a 401 refreshes the token once."""
        token = self._access_token(ws)
        for attempt in (0, 1):
            try:
                return http.request("GET", f"{API}{path}", params=params, ok=ok,
                                    bucket=http.bucket_key(PROVIDER, ws), rate=RATE, burst=RATE,
                                    headers={"Authorization": f"Bearer {token}",
                                             "Accept": "application/json"})
            except AuthExpired:
                if attempt == 1:
                    raise
                token = self._access_token(ws, used=token)

    def _data(self, ws, path, params=None):
        return (self._get(ws, path, params).json() or {}).get("data")

    def _pages(self, ws, path, params=None):
        params = {**(params or {}), "limit": PAGE}
        while True:
            body = self._get(ws, path, params).json() or {}
            yield from body.get("data") or []
            offset = (body.get("next_page") or {}).get("offset")
            if not offset:
                return
            params = {**params, "offset": offset}

    # ---- picking ------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        ws = ctx.workspace
        if not parent:
            return sorted(({"id": f"workspace-{w['gid']}", "name": w.get("name") or w["gid"],
                            "kind": "category", "has_children": True}
                           for w in self._pages(ws, "/workspaces")),
                          key=lambda c: c["name"].lower())
        gid = parent.removeprefix("workspace-")
        return sorted(({"id": f"project-{p['gid']}", "name": p.get("name") or p["gid"],
                        "kind": "project"}
                       for p in self._pages(ws, "/projects", {"workspace": gid,
                                                              "archived": "false"})),
                      key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            kind, _, gid = p["id"].partition("-")
            if kind not in ("workspace", "project") or not gid.isdigit():
                raise FormError("Pick Asana workspaces or projects.")
            p["kind"] = "category" if kind == "workspace" else "project"
        return out

    # ---- syncing ------------------------------------------------------------------
    def _projects(self, ws, picks):
        """[project gid] in scope: picked projects, and every unarchived project of
        a picked workspace. A picked project that's gone or archived is left out."""
        out = set()
        for p in picks:
            kind, _, gid = p["id"].partition("-")
            if kind == "workspace":
                out |= {x["gid"] for x in self._pages(ws, "/projects", {
                    "workspace": gid, "archived": "false"})}
            else:
                out.add(gid)
        return sorted(out)

    def _members(self, ws, project, emails):
        """The project's members' emails (users only), or None when they can't be read."""
        try:
            members = list(self._pages(ws, "/memberships", {"parent": project}))
        except (Forbidden, NotFound):
            return None
        users = set()
        for m in members:
            who = m.get("member") or {}
            if who.get("resource_type") != "user" or not who.get("gid"):
                continue
            if who["gid"] not in emails:
                try:
                    emails[who["gid"]] = ((self._data(ws, f"/users/{who['gid']}",
                                                      {"opt_fields": "email"}) or {})
                                          .get("email") or "").lower()
                except (Forbidden, NotFound):
                    emails[who["gid"]] = ""
            if emails[who["gid"]]:
                users.add(emails[who["gid"]])
        return sorted(users)

    def _events(self, ws, project, token):
        """(deleted task gids, new token, expired?) since `token` for a project."""
        gone, params = set(), {"resource": project}
        if token:
            params["sync"] = token
        while True:
            r = self._get(ws, "/events", params, ok=(412,))
            body = r.json() or {}
            if r.status_code == 412:          # no token yet, or too old: start again
                return gone, body.get("sync"), bool(token)
            for e in body.get("data") or []:
                res = e.get("resource") or {}
                if res.get("resource_type") != "task":
                    continue
                parent = e.get("parent") or {}
                if e.get("action") == "deleted" or (
                        e.get("action") == "removed" and parent.get("gid") == project):
                    gone.add(res.get("gid"))
                elif e.get("action") in ("undeleted", "added"):
                    gone.discard(res.get("gid"))
            params["sync"] = body.get("sync") or params.get("sync")
            if not body.get("has_more"):
                return gone, params["sync"], False

    def changes(self, ctx, cursor):
        picks = (ctx.settings or {}).get("containers") or []
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        projects = self._projects(ws, picks)
        sig = hashlib.sha256(json.dumps(projects).encode()).hexdigest()[:16]
        started = _now_iso()
        full = (ctx.full or not state or state.get("projects") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        since = None if full else _iso_minus(state.get("since"), OVERLAP)
        tokens = dict(state.get("events") or {})
        deleted = set()
        if not full and since:
            for project in projects:
                gone, tokens[project], expired = self._events(ws, project, tokens.get(project))
                deleted |= gone
                full = full or expired
        full = full or since is None
        if full:                              # fresh event tokens for the next run
            tokens = {p: self._events(ws, p, None)[1] for p in projects}
        emails, seen, n = {}, set(), 0
        for project in projects:
            try:
                meta = self._data(ws, f"/projects/{project}", {"opt_fields": PROJECT_FIELDS}) or {}
            except NotFound:
                continue                       # deleted since it was picked
            if meta.get("archived"):
                continue
            users = self._members(ws, project, emails)
            acl = ({"via": PROVIDER, "project": project, "users": users} if users is not None
                   else {"via": PROVIDER, "project": project, "partial": True})
            params = {"project": project, "opt_fields": TASK_FIELDS}
            if not full:
                params["modified_since"] = since
            for t in self._pages(ws, "/tasks", params):
                sid = f"task-{t['gid']}"
                if ctx.max_items and n >= ctx.max_items:
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)
                    return
                seen.add(sid)
                n += 1
                yield Upsert(self._task_item(t, meta, acl))
            brief = self._brief(ws, meta)
            if brief:
                seen.add(brief.source_id)
                brief.acl = acl
                yield Upsert(brief)
        for gid in sorted(deleted):
            if f"task-{gid}" in ctx.known:
                yield Delete(f"task-{gid}")
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"projects": sig, "since": started, "events": tokens,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def _brief(self, ws, meta):
        """The project's overview and brief as one document (None: neither has text)."""
        parts, url = [], meta.get("permalink_url") or ""
        if (meta.get("notes") or "").strip():
            parts.append("## Overview\n\n" + meta["notes"].strip())
        ref = meta.get("project_brief") or {}
        if ref.get("gid"):
            try:
                brief = self._data(ws, f"/project_briefs/{ref['gid']}",
                                   {"opt_fields": "title,text,permalink_url"}) or {}
            except (Forbidden, NotFound):
                brief = {}
            if (brief.get("text") or "").strip():
                parts.append(f"## {brief.get('title') or 'Project brief'}\n\n"
                             + brief["text"].strip())
                url = brief.get("permalink_url") or url
        if not parts:
            return None
        text = "\n\n".join(parts)
        return Item(source_id=f"brief-{meta['gid']}" if meta.get("gid") else "brief-",
                    title=f"{meta.get('name') or 'Project'}: brief", url=url,
                    version=hashlib.sha256(text.encode()).hexdigest()[:16], kind="page",
                    mime="text/markdown", modified=meta.get("modified_at"),
                    parent_id=f"project-{meta.get('gid')}",
                    attrs={"project": meta.get("name")}, content=text)

    @staticmethod
    def _task_item(t, meta, acl):
        section = next((((m.get("section") or {}).get("name")) for m in t.get("memberships")
                        or [] if (m.get("section") or {}).get("name")), None)
        attrs = {"id": t["gid"], "project": meta.get("name"),
                 "assignee": (t.get("assignee") or {}).get("name"), "section": section,
                 "status": "Completed" if t.get("completed") else "Open",
                 "due": t.get("due_on"), "created": (t.get("created_at") or "")[:10] or None}
        return Item(source_id=f"task-{t['gid']}", title=t.get("name") or "Untitled task",
                    url=t.get("permalink_url") or "", version=t.get("modified_at") or "",
                    kind="issue", mime="text/markdown", modified=t.get("modified_at"),
                    parent_id=f"project-{meta.get('gid')}", acl=acl,
                    attrs={k: v for k, v in attrs.items() if v},
                    content={"notes": t.get("notes") or ""})

    def fetch(self, ctx, item):
        if item.kind != "issue":
            return super().fetch(ctx, item)
        a = item.attrs or {}
        facts = [("Project", a.get("project")), ("Section", a.get("section")),
                 ("Status", a.get("status")), ("Assignee", a.get("assignee")),
                 ("Due", a.get("due")), ("Created", a.get("created"))]
        parts = ["\n".join(f"{k}: {v}" for k, v in facts if v)]
        notes = ((item.content or {}).get("notes") or "").strip()
        if notes:
            parts.append("## Description\n\n" + notes)
        stories = self._pages(ctx.workspace, f"/tasks/{a['id']}/stories", {
            "opt_fields": "resource_subtype,text,created_at,created_by.name"})
        lines = [f"### {(s.get('created_by') or {}).get('name') or 'Someone'}, "
                 f"{(s.get('created_at') or '')[:10]}\n\n{s['text'].strip()}"
                 for s in stories
                 if s.get("resource_subtype") == "comment_added" and (s.get("text") or "").strip()]
        if lines:
            parts.append("## Comments\n\n" + "\n\n".join(lines))
        return Fetched("\n\n".join(p for p in parts if p), "text/markdown")

    def disconnect(self, workspace):
        """Revoke the refresh token (and with it its access tokens) at Asana."""
        sec = store.secret(workspace, PROVIDER) or {}
        if not sec.get("refresh_token"):
            return
        try:
            http.request("POST", REVOKE_URL, retries=1, ok=(400,), data={
                "client_id": os.environ.get("ASANA_CLIENT_ID", ""),
                "client_secret": os.environ.get("ASANA_CLIENT_SECRET", ""),
                "token": sec["refresh_token"]})
        except Exception as e:
            log.warning("Asana: couldn't revoke the token of workspace %s (%s); remove the app "
                        "in Asana (My settings -> Apps) if it matters", workspace,
                        type(e).__name__)


CONNECTOR = Asana()
