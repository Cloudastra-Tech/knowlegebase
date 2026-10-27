"""
connectors/linear.py  —  sync a workspace's Linear teams (issues, projects and
documents) into its index.

Read-only: OAuth 2 with the `read` scope, as the user who connects (actor=user),
so only what that user can see in Linear is synced: a private team they aren't
in never appears. Linear access tokens last 24 hours and come with a refresh
token that ROTATES on every refresh (since 2026-04-01 for every OAuth app): the
new pair is saved under a per-connection lock before the new access token is
used, and refreshing starts at 80 % of the token's life.

What is synced (nothing until an admin picks TEAMS on the card):
  issues      title, key facts (team, status, assignee, project, labels),
              description and comments, as Markdown;
  projects    of those teams: their description and content (Markdown);
  documents   attached to those teams, their projects, or their issues.
Archived and trashed items are left out (and deleted when they become so).

How a sync finds what changed: GraphQL, Relay pages (`first` / `after`,
pageInfo.endCursor).
  FULL (first run, the picks changed, every RECONCILE seconds, asked for)
      every issue of the picked teams, every project and document in scope;
      anything known but not listed was archived, deleted or moved out and goes
      behind the mass-delete guard.
  INCREMENTAL
      issues, projects and documents with `updatedAt` after the last run's
      newest (less OVERLAP seconds), includeArchived so an archive or trash is
      seen and deleted.

Permissions: Linear permissions are team-based. Each item's acl is the
team's MEMBERS' emails (active users only), read once per run per team:
{"via": "linear", "team": id, "users": [...]}; a project or document of
several teams takes the union of the picked teams' members. Members of the
Linear organisation outside the team (who can read a public team's issues) are
not listed: the source policy never lets in more people than the team.

Rate limits: 5,000 requests and 2,000,000 complexity points an hour per user,
10,000 points per query; Linear answers 429 with Retry-After, which
connectors/http.py waits out (calls are also spaced per token). Pages are
50 issues with up to 50 comments each to stay well under the per-query cap.

Needs LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET: an OAuth application at
linear.app/settings/api/applications with the callback URL
<PUBLIC_URL>/api/integrations/linear/callback.

API references (checked 2026-10-27):
  OAuth           https://linear.app/developers/oauth-2-0-authentication
                  (GET https://linear.app/oauth/authorize: client_id, redirect_uri,
                  response_type=code, scope (comma separated), state, prompt=consent,
                  actor; POST https://api.linear.app/oauth/token, form-encoded;
                  access tokens valid 24 h; refresh_token grant returns a new access
                  AND refresh token; POST https://api.linear.app/oauth/revoke `token`)
  GraphQL         https://linear.app/developers/graphql (POST https://api.linear.app/graphql,
                  Authorization: Bearer for OAuth tokens)
  pagination      https://linear.app/developers/pagination (first/after, nodes, pageInfo;
                  orderBy createdAt | updatedAt)
  filtering       https://linear.app/developers/filtering (DateComparator gt, gte, ...)
  schema          https://github.com/linear/linear/blob/master/packages/sdk/src/schema.graphql
                  (IssueFilter.updatedAt, includeArchived, Issue.trashed, Team.members,
                  Project.content, Document.{content, project, team, issue})
  rate limits     https://linear.app/developers/rate-limiting (429 + Retry-After)
  private teams   https://linear.app/docs/private-teams
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
                             Forbidden, FormError, Item, LimitReached, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "linear"
AUTH_URL = "https://linear.app/oauth/authorize"
TOKEN_URL = "https://api.linear.app/oauth/token"
REVOKE_URL = "https://api.linear.app/oauth/revoke"
API = "https://api.linear.app/graphql"
SCOPE = "read"
RATE = 1                               # calls a second per token (5,000 an hour)
PAGE = 50
RECONCILE = 24 * 60 * 60               # seconds between full listings
OVERLAP = 120                          # seconds re-checked before the watermark
RENEW_AT = 0.8                         # refresh at 80 % of the token's life

_PAGE_INFO = "pageInfo { hasNextPage endCursor }"
TEAMS = f"""query Teams($after: String) {{
  teams(first: {PAGE}, after: $after) {{ nodes {{ id key name }} {_PAGE_INFO} }} }}"""
MEMBERS = f"""query Members($id: String!, $after: String) {{
  team(id: $id) {{ members(first: {PAGE}, after: $after) {{
    nodes {{ email active }} {_PAGE_INFO} }} }} }}"""
ISSUES = f"""query Issues($filter: IssueFilter, $after: String, $archived: Boolean) {{
  issues(first: {PAGE}, after: $after, filter: $filter, orderBy: updatedAt,
         includeArchived: $archived) {{
    nodes {{ id identifier title description url createdAt updatedAt archivedAt trashed
      state {{ name }} team {{ id key name }} assignee {{ name }} creator {{ name }}
      project {{ name }} labels {{ nodes {{ name }} }}
      comments(first: 50) {{ nodes {{ body createdAt user {{ name }} }} {_PAGE_INFO} }} }}
    {_PAGE_INFO} }} }}"""
COMMENTS = f"""query Comments($id: String!, $after: String) {{
  issue(id: $id) {{ comments(first: {PAGE}, after: $after) {{
    nodes {{ body createdAt user {{ name }} }} {_PAGE_INFO} }} }} }}"""
PROJECTS = f"""query Projects($filter: ProjectFilter, $after: String, $archived: Boolean) {{
  projects(first: {PAGE}, after: $after, filter: $filter, orderBy: updatedAt,
           includeArchived: $archived) {{
    nodes {{ id name description content url updatedAt archivedAt
      teams {{ nodes {{ id }} }} }} {_PAGE_INFO} }} }}"""
DOCUMENTS = f"""query Documents($filter: DocumentFilter, $after: String, $archived: Boolean) {{
  documents(first: {PAGE}, after: $after, filter: $filter, orderBy: updatedAt,
            includeArchived: $archived) {{
    nodes {{ id title content url updatedAt archivedAt trashed
      team {{ id }} project {{ id }} issue {{ team {{ id }} }} }} {_PAGE_INFO} }} }}"""
VIEWER = "query { viewer { name email organization { name urlKey } } }"


def _since(watermark):
    """The watermark less OVERLAP seconds, as Linear's ISO 8601."""
    try:
        t = datetime.datetime.fromisoformat(watermark.replace("Z", "+00:00"))
        return (t - datetime.timedelta(seconds=OVERLAP)).isoformat().replace("+00:00", "Z")
    except (AttributeError, ValueError):
        return None


class Linear(Connector):
    PROVIDER = PROVIDER
    LABEL = "Linear"
    DESCRIPTION = "Issues with comments, projects and documents of the teams you pick"
    AUTH = "oauth"
    PICKER = "list"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "teams"
    UNIT = "items"
    SETUP_ENV = "LINEAR_CLIENT_ID / LINEAR_CLIENT_SECRET"
    ACL = True

    def configured(self):
        return bool(os.getenv("LINEAR_CLIENT_ID") and os.getenv("LINEAR_CLIENT_SECRET"))

    # ---- connecting ---------------------------------------------------------------
    def auth_url(self, redirect_uri, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": os.environ["LINEAR_CLIENT_ID"], "redirect_uri": redirect_uri,
            "response_type": "code", "scope": SCOPE, "state": state,
            "prompt": "consent",              # pick the Linear workspace every time
            "actor": "user"})

    def _token_call(self, data):
        r = http.request("POST", TOKEN_URL, retries=1, ok=(400, 401), data={
            **data, "client_id": os.environ.get("LINEAR_CLIENT_ID", ""),
            "client_secret": os.environ.get("LINEAR_CLIENT_SECRET", "")},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code in (400, 401):
            raise AuthExpired(f"Linear refused the {data['grant_type']} grant")
        return r.json()

    @staticmethod
    def _tokens(t, old=None):
        life = int(t.get("expires_in") or 86399)
        now = time.time()
        return {"access_token": t["access_token"],
                "refresh_token": t.get("refresh_token") or (old or {}).get("refresh_token"),
                "expires_at": now + life - 60, "renew_at": now + life * RENEW_AT}

    def finish_connect(self, workspace, code, redirect_uri, user):
        t = self._token_call({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": redirect_uri})
        if not t.get("refresh_token"):
            raise ConfigError("Linear did not return a refresh token")
        sec = self._tokens(t)
        me = self._post(sec["access_token"], VIEWER).get("viewer") or {}
        org = (me.get("organization") or {}).get("name") or "Linear"
        account = f"{org} ({me['email']})" if me.get("email") else org
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, sec, user)
        key = (me.get("organization") or {}).get("urlKey")
        if row and (row.get("settings") or {}).get("organization") != key:
            store.update(workspace, PROVIDER, settings=dict(row.get("settings") or {},
                                                            teams=[], organization=key))
        elif not row:
            store.update(workspace, PROVIDER, settings={"organization": key})

    # ---- tokens and calls ---------------------------------------------------------
    def _access_token(self, workspace, used=None):
        """A valid access token: refreshed (under a lock, the rotated refresh token
        saved first) once it's past RENEW_AT, or when `used` was refused."""
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = store.secret(workspace, PROVIDER)
            if not sec or not sec.get("access_token"):
                raise AuthExpired("Linear is not connected")
            stale = sec.get("renew_at", 0) <= time.time()
            if not stale and (used is None or sec["access_token"] != used):
                return sec["access_token"]
            if not sec.get("refresh_token"):
                raise AuthExpired("Linear refused the token and there is no refresh token")
            sec = self._tokens(self._token_call({"grant_type": "refresh_token",
                                                 "refresh_token": sec["refresh_token"]}), sec)
            store.update(workspace, PROVIDER, secret=sec)
            return sec["access_token"]

    def _post(self, token, query, variables=None):
        r = http.request("POST", API, bucket=http.bucket_key(PROVIDER, token), rate=RATE,
                         burst=5, ok=(400,), json={"query": query, "variables": variables or {}},
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"})
        body = r.json() if r.content else {}
        errors = body.get("errors") or []
        codes = {((e.get("extensions") or {}).get("code") or "") for e in errors}
        if "AUTHENTICATION_ERROR" in codes:
            raise AuthExpired("Linear: authentication error")
        if "FORBIDDEN" in codes:
            raise Forbidden("Linear: forbidden")
        if r.status_code == 400 or (errors and not body.get("data")):
            raise ConfigError(f"Linear refused the query ({', '.join(sorted(codes)) or 'error'})")
        return body.get("data") or {}

    def _gql(self, workspace, query, variables=None):
        token = self._access_token(workspace)
        try:
            return self._post(token, query, variables)
        except AuthExpired:
            return self._post(self._access_token(workspace, used=token), query, variables)

    def _pages(self, workspace, query, field, variables=None, root=None):
        """Every node of a connection (`root`: an object it hangs off, like team)."""
        after = None
        while True:
            data = self._gql(workspace, query, {**(variables or {}), "after": after})
            conn = (data.get(root) or {}).get(field) if root else data.get(field)
            conn = conn or {}
            yield from conn.get("nodes") or []
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage") or not info.get("endCursor"):
                return
            after = info["endCursor"]

    # ---- picking ------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        teams = self._pages(ctx.workspace, TEAMS, "teams")
        return sorted(({"id": t["id"], "name": f"{t.get('name') or t['id']} ({t.get('key')})"
                        if t.get("key") else t.get("name") or t["id"], "kind": "team"}
                       for t in teams), key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            if p["kind"] != "team":
                raise FormError("Pick Linear teams.")
        return out

    # ---- syncing ------------------------------------------------------------------
    def _members(self, workspace, team):
        return sorted({m["email"].lower() for m in self._pages(
            workspace, MEMBERS, "members", {"id": team}, root="team")
            if m.get("email") and m.get("active", True)})

    def changes(self, ctx, cursor):
        teams = sorted(str(p["id"]) for p in (ctx.settings or {}).get("teams") or [])
        if not teams:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        sig = hashlib.sha256(json.dumps(teams).encode()).hexdigest()[:16]
        full = (ctx.full or not state or state.get("teams") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        since = None if full else _since(state.get("watermark"))
        full = full or since is None
        members = {t: self._members(ws, t) for t in teams}
        picked = set(teams)
        newest = state.get("watermark") or ""
        seen, n = set(), 0

        def acl(team_ids):
            in_scope = sorted(set(team_ids) & picked)
            users = sorted({u for t in in_scope for u in members[t]})
            return {"via": "linear", "team": in_scope[0] if in_scope else None,
                    "teams": in_scope, "users": users}

        def gone(node):
            return bool(node.get("archivedAt") or node.get("trashed"))

        updated = {"updatedAt": {"gt": since}} if since else {}
        sources = [
            ("issue", ISSUES, "issues", {"team": {"id": {"in": teams}}, **updated}),
            ("project", PROJECTS, "projects", updated or None),
            ("document", DOCUMENTS, "documents", updated or None),
        ]
        projects = {}                        # project id -> its picked teams
        for kind, query, field, flt in sources:
            for node in self._pages(ws, query, field, {"filter": flt, "archived": not full}):
                newest = max(newest, node.get("updatedAt") or "")
                if kind == "issue":
                    team_ids = [(node.get("team") or {}).get("id")]
                elif kind == "project":
                    team_ids = [t["id"] for t in (node.get("teams") or {}).get("nodes") or []]
                    if set(team_ids) & picked:
                        projects[node["id"]] = team_ids
                else:
                    team_ids = [(node.get("team") or {}).get("id"),
                                ((node.get("issue") or {}).get("team") or {}).get("id")]
                    project = (node.get("project") or {}).get("id")
                    if project:
                        team_ids += projects.get(project) or self._known_teams(ctx, project)
                sid = f"{kind}:{node['id']}"
                if gone(node) or not set(team_ids) & picked:
                    if sid in ctx.known:
                        yield Delete(sid)
                    continue
                if ctx.max_items and n >= ctx.max_items:
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)             # deletes nothing; tries again
                    return
                seen.add(sid)
                n += 1
                yield Upsert(self._item(kind, node, acl(team_ids)))
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"teams": sig, "watermark": newest,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    @staticmethod
    def _known_teams(ctx, project):
        """The picked teams of a project synced before (not listed in this run)."""
        acl = (ctx.items.get(f"project:{project}") or {}).get("acl") or {}
        return list(acl.get("teams") or [])

    @staticmethod
    def _item(kind, node, acl):
        if kind == "issue":
            title = f"{node.get('identifier')}: {node.get('title') or 'Untitled'}"
            team = node.get("team") or {}
            attrs = {"team": team.get("name"), "status": (node.get("state") or {}).get("name"),
                     "assignee": (node.get("assignee") or {}).get("name"),
                     "creator": (node.get("creator") or {}).get("name"),
                     "project": (node.get("project") or {}).get("name"),
                     "labels": [label["name"] for label in
                                (node.get("labels") or {}).get("nodes") or []]}
            parent = team.get("id")
        elif kind == "project":
            title, attrs = node.get("name") or "Untitled project", {}
            parent = acl["team"]
        else:
            title, attrs = node.get("title") or "Untitled document", {}
            parent = acl["team"]
        return Item(source_id=f"{kind}:{node['id']}", title=title, url=node.get("url") or "",
                    version=node.get("updatedAt") or "", kind=kind, mime="text/markdown",
                    modified=node.get("updatedAt"), parent_id=parent, acl=acl,
                    attrs={"id": node["id"], **{k: v for k, v in attrs.items() if v}},
                    content=node)

    def fetch(self, ctx, item):
        node = item.content
        if node is None:                           # (not listed in this run)
            return None
        if item.kind == "project":
            parts = [node.get("description") or "", node.get("content") or ""]
            return Fetched("\n\n".join(p for p in parts if p.strip()), "text/markdown")
        if item.kind == "document":
            return Fetched(node.get("content") or "", "text/markdown")
        a = item.attrs or {}
        facts = [("Team", a.get("team")), ("Status", a.get("status")),
                 ("Assignee", a.get("assignee")), ("Created by", a.get("creator")),
                 ("Project", a.get("project")),
                 ("Labels", ", ".join(a.get("labels") or []) or None),
                 ("Created", (node.get("createdAt") or "")[:10] or None),
                 ("Updated", (node.get("updatedAt") or "")[:10] or None)]
        parts = ["\n".join(f"{k}: {v}" for k, v in facts if v)]
        if (node.get("description") or "").strip():
            parts.append("## Description\n\n" + node["description"].strip())
        comments = list(((node.get("comments") or {}).get("nodes")) or [])
        info = (node.get("comments") or {}).get("pageInfo") or {}
        if info.get("hasNextPage") and info.get("endCursor"):
            after = info["endCursor"]
            while after:
                data = self._gql(ctx.workspace, COMMENTS, {"id": node["id"], "after": after})
                conn = (data.get("issue") or {}).get("comments") or {}
                comments += conn.get("nodes") or []
                page = conn.get("pageInfo") or {}
                after = page.get("endCursor") if page.get("hasNextPage") else None
        lines = [f"### {(c.get('user') or {}).get('name') or 'Someone'}, "
                 f"{(c.get('createdAt') or '')[:10]}\n\n{c['body'].strip()}"
                 for c in sorted(comments, key=lambda c: c.get("createdAt") or "")
                 if (c.get("body") or "").strip()]
        if lines:
            parts.append("## Comments\n\n" + "\n\n".join(lines))
        return Fetched("\n\n".join(p for p in parts if p), "text/markdown")

    def disconnect(self, workspace):
        """Revoke the refresh token (and with it the access tokens) at Linear."""
        sec = store.secret(workspace, PROVIDER)
        token = (sec or {}).get("refresh_token") or (sec or {}).get("access_token")
        if not token:
            return
        hint = "refresh_token" if sec.get("refresh_token") else "access_token"
        try:
            http.request("POST", REVOKE_URL, retries=1, ok=(400,),
                         data={"token": token, "token_type_hint": hint},
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as e:
            log.warning("Linear: couldn't revoke the token of workspace %s (%s); remove the "
                        "app in Linear (Settings -> Applications) if it matters",
                        workspace, type(e).__name__)


CONNECTOR = Linear()
