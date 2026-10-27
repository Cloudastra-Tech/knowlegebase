"""
connectors/jira.py  —  sync a workspace's Jira projects into its index.

Two connectors, one module (like confluence.py):
  jira       Jira Cloud, through the Atlassian OAuth 2.0 (3LO) app Confluence
             uses (connectors/atlassian.py): the same client id, token rotation
             and callback URL; the consent screen asks only for SCOPES below.
  jira_dc    Jira Data Center / Server (8.14+), connected with the site's
             address and a personal access token pasted into a form.

Read-only. Nothing is synced until an admin picks PROJECTS on the card. Each
issue becomes one document, written as Markdown for chunking.py: the key and
summary (the title), the key fields (type, status, priority, assignee,
reporter, labels, dates), the description (Atlassian Document Format on Cloud,
wiki markup on Data Center, turned into text) and the comments, one heading
each. Comments restricted to a group or role are left out (their audience is
narrower than the issue's). Attachments are not synced.

How a sync finds what changed:
  FULL (first run, the picks changed, every RECONCILE seconds, or asked for)
      every issue of the picked projects is listed with JQL
      `project in (...) ORDER BY key` (the authoritative list: anything known
      but not listed was deleted or moved out, and goes behind the mass-delete
      guard); the projects' browse permissions are read again.
  INCREMENTAL (every run in between)
      `project in (...) AND updated >= "<watermark>" ORDER BY updated ASC`,
      paged (Cloud: nextPageToken; Data Center: startAt). JQL dates are in the
      user's time zone and carry none, so the watermark moves back by TZ_SLACK,
      more than any UTC offset (as Confluence's CQL); an unchanged issue costs
      nothing (the framework skips an unchanged version).
  New issues are synced only if updated within BACKFILL_DAYS (JIRA_BACKFILL_DAYS,
  default 365; 0: all); an issue already synced stays while it exists.

Permissions are CAPTURED into each item's `acl`, not enforced (who may ask is
still the connection's access level): the project's BROWSE_PROJECTS grants
from its permission scheme (users, groups, project roles, application roles,
"anyone"; reporter / assignee style grants are listed as `dynamic`), and the
issue's security level (id and name: its members need the Administer Jira
permission and a manage scope, so they aren't read). Reading the permission
scheme needs Administer Jira or Administer Projects: without it, project_read
is None (unknown) and the item is marked partial.

Cloud needs ATLASSIAN_CLIENT_ID / ATLASSIAN_CLIENT_SECRET: the Confluence app,
with the Jira API and SCOPES added to it (deploy.md, "Jira").

API references (checked 2026-10-27):
  scopes / REST v3   https://dac-static.atlassian.com/cloud/jira/platform/swagger-v3.v3.json
                     (x-atlassian-oauth2-scopes: read:jira-work for search/jql,
                     project/search, issue comments, permissionscheme, securitylevel;
                     read:jira-user for myself)
  search (JQL)       https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/
                     GET /rest/api/3/search/jql: jql (bounded), nextPageToken,
                     maxResults, fields; answer {issues, nextPageToken (absent on
                     the last page), isLast}
  projects           GET /rest/api/3/project/search: startAt, maxResults; {values, isLast}
  comments           GET /rest/api/3/issue/{id}/comment: startAt, maxResults;
                     {comments, startAt, maxResults, total}
  permission schemes GET /rest/api/3/project/{key}/permissionscheme?expand=permissions,
                     holder types: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-permission-schemes/
  ADF                https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
  rate limits        https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
  Data Center REST   https://docs.atlassian.com/software/jira/docs/api/REST/9.12.0/
                     (GET /rest/api/2/search: jql, startAt, maxResults, fields;
                     {startAt, maxResults, total, issues}; /rest/api/2/project;
                     /rest/api/2/myself; .../permissionscheme)
  personal tokens    https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html
"""

import datetime
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

import integrations_store as store
from connectors import atlassian, http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Fetched, Forbidden,
                             FormError, Item, LimitReached, Upsert)
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

CLOUD = "jira"
DATA_CENTER = "jira_dc"

API = f"{atlassian.GATEWAY}/jira"                # + /{cloudid}/rest/api/3/...
# Classic read scopes: every call the sync makes lists read:jira-work (or
# read:jira-user for /myself) as its Current scope in the v3 spec; the granular
# equivalents are still marked Beta there. offline_access: a refresh token.
SCOPES = "read:jira-work read:jira-user offline_access"

RATE = atlassian.RATE
PAGE = 100                            # issues per search page
RECONCILE = 24 * 60 * 60              # seconds between full listings (deletes, permissions)
TZ_SLACK = 14 * 60 * 60 + 10 * 60     # JQL dates are local to the user: look back this far
BACKFILL_DAYS = 365
FIELDS = ("summary,status,issuetype,priority,assignee,reporter,labels,created,updated,"
          "project,security,description")
MAX_COMMENTS = 500                    # per issue


# ---- Atlassian Document Format -> Markdown ------------------------------------------
def _inline(nodes):
    out = []
    for n in nodes or []:
        t, a = n.get("type"), n.get("attrs") or {}
        if t == "text":
            text = n.get("text") or ""
            marks = {m.get("type"): m.get("attrs") or {} for m in n.get("marks") or []}
            if "code" in marks:
                text = f"`{text}`"
            href = (marks.get("link") or {}).get("href")
            if href and href != text:
                text = f"{text} ({href})"
            out.append(text)
        elif t == "hardBreak":
            out.append("\n")
        elif t == "mention":
            out.append(a.get("text") or "@someone")
        elif t == "emoji":
            out.append(a.get("text") or a.get("shortName") or "")
        elif t in ("inlineCard", "blockCard", "embedCard"):
            out.append(a.get("url") or "")
        elif t == "date":
            try:
                out.append(datetime.datetime.fromtimestamp(
                    int(a.get("timestamp")) / 1000, datetime.timezone.utc).strftime("%Y-%m-%d"))
            except (TypeError, ValueError):
                pass
        elif t == "status":
            out.append(f"[{a.get('text') or ''}]")
        elif n.get("content"):
            out.append(_inline(n["content"]))
    return "".join(out)


def _blocks(nodes, indent=""):
    """Each block node as Markdown text: headings as "#" lines, lists as "- " /
    "1. ", code fenced (```), table rows as consecutive "a | b" lines (how
    chunking.py reads a table)."""
    out = []
    for n in nodes or []:
        t, a, kids = n.get("type"), n.get("attrs") or {}, n.get("content") or []
        if t == "paragraph":
            text = _inline(kids)
            if text.strip():
                out.append(indent + text)
        elif t == "heading":
            level = min(max(int(a.get("level") or 1), 1), 6)
            out.append("#" * level + " " + _inline(kids))
        elif t in ("bulletList", "orderedList"):
            lines = []
            for i, item in enumerate(kids, start=int(a.get("order") or 1)):
                mark = "- " if t == "bulletList" else f"{i}. "
                parts = _blocks(item.get("content"), indent + "  ")
                first = parts[0].strip() if parts else ""
                lines.append(indent + mark + first)
                lines += parts[1:]
            out.append("\n".join(lines))
        elif t == "codeBlock":
            lang = a.get("language") or ""
            out.append(f"```{lang}\n{_inline(kids)}\n```")
        elif t == "blockquote":
            out.append("\n".join("> " + line for line in "\n\n".join(_blocks(kids)).split("\n")))
        elif t == "rule":
            out.append("---")
        elif t == "table":
            rows = []
            for row in kids:
                cells = [" ".join(_blocks(c.get("content"))).replace("\n", " ").strip()
                         for c in row.get("content") or []]
                rows.append(" | ".join(cells))
            out.append("\n".join(rows))
        elif t in ("taskList", "decisionList"):
            lines = []
            for item in kids:
                done = (item.get("attrs") or {}).get("state") in ("DONE", "DECIDED")
                mark = ("[x] " if done else "[ ] ") if t == "taskList" else "- "
                lines.append(indent + mark + _inline(item.get("content")))
            out.append("\n".join(lines))
        elif t in ("mediaSingle", "mediaGroup", "media", "mediaInline"):
            continue
        elif t in ("expand", "nestedExpand", "panel", "layoutSection", "layoutColumn",
                   "bodiedExtension", "doc"):
            if a.get("title"):
                out.append(a["title"])
            out += _blocks(kids, indent)
        elif kids:
            out += _blocks(kids, indent)
        elif n.get("text"):
            out.append(n["text"])
    return out


def adf_to_markdown(doc):
    """An ADF document (or a plain string, as Data Center sends) as Markdown."""
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc.strip()
    return "\n\n".join(b for b in _blocks([doc]) if b.strip())


# ---- helpers ------------------------------------------------------------------------
def _ts(iso):
    """Jira's "2026-10-20T10:00:00.000+0000" (or Z) as epoch seconds; 0 if not a date."""
    iso = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", (iso or "").replace("Z", "+00:00"))
    try:
        return datetime.datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def _jql_date(ts):
    """A JQL date ("yyyy-MM-dd HH:mm")."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _jql_keys(keys):
    return ", ".join('"' + k.replace('"', "").replace("\\", "") + '"' for k in keys)


def _name(user):
    return (user or {}).get("displayName") or (user or {}).get("name") or ""


def _browse_grants(scheme):
    """Who may browse a project, from its permission scheme's BROWSE_PROJECTS
    grants (holder types: anyone, applicationRole, group, projectRole, user, and
    issue-relative ones like reporter / assignee / projectLead)."""
    users, groups, roles, dynamic, public = set(), set(), set(), set(), False
    for g in (scheme or {}).get("permissions") or []:
        if g.get("permission") != "BROWSE_PROJECTS":
            continue
        h = g.get("holder") or {}
        kind, value = h.get("type"), h.get("value") or h.get("parameter")
        if kind == "anyone":
            public = True
        elif kind == "user" and value:
            users.add(f"atlassian:{value}")
        elif kind == "group" and value:
            groups.add(f"atlassian:group:{value}")
        elif kind == "projectRole" and value:
            roles.add(f"atlassian:role:{value}")
        elif kind == "applicationRole":
            roles.add(f"atlassian:app:{value or 'any'}")
        elif kind:
            dynamic.add(kind)
    return {"public": public, "users": sorted(users), "groups": sorted(groups),
            "roles": sorted(roles), "dynamic": sorted(dynamic)}


class _Jira(Connector):
    """What Cloud and Data Center share: the picks, the sync, the item shape.
    Each edition supplies _session and the calls that differ (_get, _issues,
    _projects)."""
    PICKER = "list"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "projects"
    UNIT = "issues"
    ACL = True

    def _get(self, ws, url, params=None, **kwargs):
        raise NotImplementedError

    def _json(self, ws, url, params=None):
        try:
            return self._get(ws, url, params).json()
        except ValueError:
            raise ConfigError("the answer isn't JSON") from None

    # ---- picking ------------------------------------------------------------------
    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            p["kind"] = "project"
        return out

    def list_containers(self, ctx, parent=None):
        s = self._session(ctx.workspace)
        return sorted(({"id": p["key"], "name": f"{p.get('name') or p['key']} ({p['key']})",
                        "kind": "project"} for p in self._projects(ctx.workspace, s)
                       if p.get("key")), key=lambda c: c["name"].lower())

    # ---- syncing ------------------------------------------------------------------
    def _project_read(self, ws, s, keys):
        """{project key: its browse grants, or None when the token may not read
        the permission scheme (Administer Projects / Jira only)}."""
        out = {}
        for key in keys:
            try:
                out[key] = _browse_grants(self._json(
                    ws, f"{s['rest']}/project/{key}/permissionscheme", {"expand": "permissions"}))
            except Forbidden:
                out[key] = None
        return out

    def _item(self, s, issue, project_read):
        f = issue.get("fields") or {}
        key = issue.get("key") or issue.get("id")
        project = (f.get("project") or {}).get("key") or key.split("-")[0]
        level = f.get("security") or None
        acl = {"project": project, "project_read": project_read.get(project),
               "security_level": ({"id": str(level.get("id")), "name": level.get("name")}
                                  if level else None),
               "restricted": bool(level)}
        if acl["project_read"] is None:
            acl["partial"] = True
        attrs = {"id": str(issue.get("id")), "key": key, "project": project,
                 "type": (f.get("issuetype") or {}).get("name"),
                 "status": (f.get("status") or {}).get("name"),
                 "priority": (f.get("priority") or {}).get("name"),
                 "assignee": _name(f.get("assignee")) or None,
                 "labels": list(f.get("labels") or [])}
        return Item(source_id=f"issue-{issue.get('id')}", title=f"{key}: {f.get('summary') or ''}",
                    url=f"{s['site']}/browse/{key}", version=f.get("updated") or "",
                    kind="issue", mime="text/markdown", modified=f.get("updated"),
                    parent_id=f"project-{project}", acl=acl, attrs=attrs, content=issue)

    def changes(self, ctx, cursor):
        picked = (ctx.settings or {}).get("projects") or []
        state = json.loads(cursor) if cursor else {}
        if not picked:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        s = self._session(ws)
        keys = sorted(str(p["id"]) for p in picked)
        full = (ctx.full or not state or state.get("projects") != keys
                or time.time() - state.get("full_at", 0) > RECONCILE)
        where = f"project in ({_jql_keys(keys)})"
        if full:
            project_read = self._project_read(ws, s, keys)
            jql = f"{where} ORDER BY key ASC"
        else:
            project_read = state.get("project_read") or {}
            since = (_ts(state.get("watermark")) or state.get("full_at", 0)) - TZ_SLACK
            jql = f'{where} AND updated >= "{_jql_date(since)}" ORDER BY updated ASC'
        days = env_int("JIRA_BACKFILL_DAYS", BACKFILL_DAYS, minimum=0)
        oldest = time.time() - days * 86400 if days else 0
        newest = state.get("watermark") or ""
        seen, n = set(), 0
        for issue in self._issues(ws, s, jql):
            item = self._item(s, issue, project_read)
            if _ts(item.modified) > _ts(newest):
                newest = item.modified
            if item.source_id in seen:
                continue
            seen.add(item.source_id)
            if item.source_id not in ctx.known and _ts(item.modified) < oldest:
                continue                          # older than the backfill window
            if ctx.max_items and n >= ctx.max_items:
                yield LimitReached(ctx.max_items)
                yield Cursor(cursor)               # deletes nothing; the next run tries again
                return
            n += 1
            yield Upsert(item)
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"projects": keys, "watermark": newest,
                                 "project_read": project_read,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def fetch(self, ctx, item):
        """The issue as Markdown: its fields, description and comments."""
        issue = item.content
        if issue is None:                          # (not listed in this run)
            return None
        s = self._session(ctx.workspace)
        f = issue.get("fields") or {}
        a = item.attrs or {}
        facts = [("Project", (f.get("project") or {}).get("name") or a.get("project")),
                 ("Type", a.get("type")), ("Status", a.get("status")),
                 ("Priority", a.get("priority")), ("Assignee", a.get("assignee")),
                 ("Reporter", _name(f.get("reporter")) or None),
                 ("Labels", ", ".join(a.get("labels") or []) or None),
                 ("Created", (f.get("created") or "")[:10] or None),
                 ("Updated", (f.get("updated") or "")[:10] or None)]
        parts = ["\n".join(f"{k}: {v}" for k, v in facts if v)]
        description = adf_to_markdown(f.get("description"))
        if description:
            parts.append("## Description\n\n" + description)
        comments, hidden = [], 0
        for c in self._comments(ctx.workspace, s, a.get("id") or a.get("key")):
            if c.get("visibility"):                # a group / role only: narrower than the issue
                hidden += 1
                continue
            body = adf_to_markdown(c.get("body"))
            if body:
                when = (c.get("created") or "")[:10]
                comments.append(f"### {_name(c.get('author')) or 'Someone'}, {when}\n\n{body}")
        if comments:
            parts.append("## Comments\n\n" + "\n\n".join(comments))
        if hidden:
            log.info("Jira: %s has %d restricted comment(s), not synced", a.get("key"), hidden)
        return Fetched("\n\n".join(p for p in parts if p), "text/markdown")

    def _comments(self, ws, s, issue_id):
        start = 0
        while start < MAX_COMMENTS:
            data = self._json(ws, f"{s['rest']}/issue/{issue_id}/comment",
                              {"startAt": start, "maxResults": 100, "orderBy": "created"})
            got = data.get("comments") or []
            yield from got
            start += len(got)
            if not got or start >= int(data.get("total") or 0):
                return


# ---- Jira Cloud ---------------------------------------------------------------------
class JiraCloud(_Jira):
    PROVIDER = CLOUD
    LABEL = "Jira"
    DESCRIPTION = "Issues, their comments and key fields from the projects you pick"
    AUTH = "oauth"
    SETUP_ENV = atlassian.SETUP_ENV
    CALLBACK = atlassian.CALLBACK
    tokens = atlassian.Session(CLOUD, "Jira")

    def configured(self):
        return atlassian.configured()

    def auth_url(self, redirect_uri, state):
        return atlassian.authorize_url(os.getenv("JIRA_SCOPES") or SCOPES, redirect_uri, state)

    def finish_connect(self, workspace, code, redirect_uri, user):
        """Swap the code for tokens, find the Jira site they reach, save."""
        t = atlassian.exchange(code, redirect_uri)
        site = atlassian.first_site(t["access_token"], "jira")
        row = store.get(workspace, CLOUD)
        store.connect(workspace, CLOUD, atlassian.account_label(site), {
            **t, "cloud_id": site["id"], "site_url": (site.get("url") or "").rstrip("/")}, user)
        settings = dict((row or {}).get("settings") or {}, cloud_id=site["id"])
        if row and (row.get("settings") or {}).get("cloud_id") != site["id"]:
            settings["projects"] = []             # another site: its projects differ
        store.update(workspace, CLOUD, settings=settings)

    def _session(self, ws):
        sec = self.tokens.secret(ws)
        return {"rest": f"{API}/{sec['cloud_id']}/rest/api/3",
                "site": (sec.get("site_url") or "").rstrip("/")}

    def _get(self, ws, url, params=None, **kwargs):
        return self.tokens.get(ws, url, params, **kwargs)

    def _projects(self, ws, s):
        start = 0
        while True:
            data = self._json(ws, f"{s['rest']}/project/search",
                              {"startAt": start, "maxResults": 50, "orderBy": "name"})
            got = data.get("values") or []
            yield from got
            start += len(got)
            if data.get("isLast", True) or not got:
                return

    def _issues(self, ws, s, jql):
        """Every issue the JQL finds (GET /search/jql, paged by nextPageToken)."""
        params = {"jql": jql, "maxResults": PAGE, "fields": FIELDS}
        while True:
            data = self._json(ws, f"{s['rest']}/search/jql", params)
            yield from data.get("issues") or []
            token = data.get("nextPageToken")
            if not token or data.get("isLast"):
                return
            params = {**params, "nextPageToken": token}

    def disconnect(self, workspace):
        """Atlassian has no revoke endpoint for 3LO apps: the admin removes the app
        under their Atlassian account's Connected apps if it matters."""


# ---- Jira Data Center / Server ------------------------------------------------------
def _base(url):
    """The site's address as typed ("https://jira.acme.com/", ".../rest/api/2")."""
    url = (url or "").strip().rstrip("/")
    for tail in ("/rest/api/2", "/rest/api", "/rest"):
        if url.endswith(tail):
            url = url[:-len(tail)]
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname or parts.query or parts.fragment:
        raise FormError("Enter the Jira address, like https://jira.example.com.")
    return url


class JiraDataCenter(_Jira):
    PROVIDER = DATA_CENTER
    LABEL = "Jira Data Center"
    DESCRIPTION = "Self-hosted Jira (8.14+): issues, comments and key fields"
    AUTH = "token"

    def _conn(self, ws):
        row = store.get(ws, DATA_CENTER)
        sec = store.secret(ws, DATA_CENTER)
        if not row or not sec or not sec.get("token"):
            raise AuthExpired("Jira Data Center is not connected")
        base = (row.get("settings") or {}).get("base_url")
        if not base:
            raise ConfigError("no Jira address")
        return base, sec["token"]

    def _call(self, token, url, params=None, **kwargs):
        r = http.request("GET", url, params=params, guard=http.guard_public, rate=RATE,
                         burst=RATE, bucket=http.bucket_key(DATA_CENTER, token),
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/json"}, **kwargs)
        if r.status_code >= 300:          # a redirect: a sign-in page in front of the API
            raise ConfigError(f"GET {http.safe_url(url)}: redirected")
        return r

    def _get(self, ws, url, params=None, **kwargs):
        _, token = self._conn(ws)
        return self._call(token, url, params, **kwargs)

    def _session(self, ws):
        base, _ = self._conn(ws)
        return {"rest": f"{base}/rest/api/2", "site": base}

    def connect_form(self, workspace, form, user):
        """Check the address and token by asking who the token belongs to
        (GET /rest/api/2/myself), then save the connection (token encrypted)."""
        base = _base(form.get("url"))
        token = (form.get("token") or "").strip()
        if not token:
            raise FormError("Paste a personal access token (your avatar → Profile → "
                            "Personal Access Tokens).")
        try:
            me = self._call(token, f"{base}/rest/api/2/myself").json()
        except AuthExpired:
            raise FormError("Jira didn't accept that token.") from None
        except ConfigError as e:
            raise FormError(f"Couldn't use that address: {e}") from None
        except Exception:
            raise FormError("Couldn't reach Jira at that address.") from None
        if not isinstance(me, dict) or not (me.get("name") or me.get("key")):
            raise FormError("Jira didn't accept that token.")
        host = urlsplit(base).hostname
        who = me.get("displayName") or me.get("name")
        account = f"{host} ({who})" if who else host
        row = store.get(workspace, DATA_CENTER)
        store.connect(workspace, DATA_CENTER, account, {"token": token}, user)
        settings = dict((row or {}).get("settings") or {}, base_url=base)
        if row and (row.get("settings") or {}).get("base_url") != base:
            settings["projects"] = []         # another site: its projects differ
        store.update(workspace, DATA_CENTER, settings=settings)
        return account

    def _projects(self, ws, s):
        data = self._json(ws, f"{s['rest']}/project")
        return data if isinstance(data, list) else []

    def _issues(self, ws, s, jql):
        """Every issue the JQL finds (GET /rest/api/2/search, paged by startAt)."""
        start = 0
        while True:
            data = self._json(ws, f"{s['rest']}/search", {"jql": jql, "startAt": start,
                                                           "maxResults": PAGE, "fields": FIELDS})
            got = data.get("issues") or []
            yield from got
            start += len(got)
            if not got or start >= int(data.get("total") or 0):
                return


CLOUD_CONNECTOR = JiraCloud()
DC_CONNECTOR = JiraDataCenter()
