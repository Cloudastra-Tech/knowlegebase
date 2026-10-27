"""
connectors/confluence.py  —  sync a workspace's Confluence spaces into its index.

Two connectors, one module (a workspace has one connection per provider):
  confluence      Confluence Cloud, connected with Atlassian OAuth 2.0 (3LO):
                  the admin signs in on Atlassian's consent screen.
  confluence_dc   Confluence Data Center / Server (7.9+), connected with the
                  site's address and a personal access token pasted into a form.

Read-only. Nothing is synced until an admin picks SPACES on the card. In them:
pages and blog posts (their storage-format XHTML, turned into text by
connectors/extract.py; macros that pull content from elsewhere become
placeholders), and their attachments of a type extract.py reads.

How a sync finds what changed:
  FULL (first run, the picks changed, every RECONCILE seconds, or asked for)
      the space's pages and blog posts are listed from the content API (the
      authoritative list: anything known but not listed is deleted), plus one
      CQL search over the spaces for what a listing doesn't carry: attachments,
      ancestors and read restrictions.
  INCREMENTAL (every run in between)
      one CQL search: `space in (...) AND type in (page, blogpost, attachment)
      AND lastmodified >= "<watermark>"`, paged with its cursor. CQL dates are in
      the site's / user's own time zone, which we don't know, so the watermark
      is moved back by TZ_SLACK (more than any UTC offset); what comes back
      unchanged costs nothing (the framework skips an unchanged version).
      Search can't see deletes or permission changes: the next full run does.

Permissions are CAPTURED into each item's `acl`, not enforced (who may ask is
still the connection's access level): the space's read permissions (Cloud),
the page's own read restrictions, and its ancestors (restrictions are inherited
down the page tree). Users are Atlassian account ids (Cloud) or user names (DC);
emails are often hidden by profile visibility, so mapping them to members is
left to the enforcement step.

Rate limits (Cloud): points-based hourly quotas, 429 + Retry-After, and
X-RateLimit-NearLimit when less than 20 % is left
(https://developer.atlassian.com/cloud/confluence/rate-limiting/). connectors/http.py
waits out Retry-After (or X-RateLimit-Reset), pauses the token's bucket on a
429, and gives up with RateLimited when the wait is longer than it should sleep;
a near-limit answer slows this token's calls down.

Cloud needs ATLASSIAN_CLIENT_ID and ATLASSIAN_CLIENT_SECRET (an OAuth 2.0
integration at developer.atlassian.com/console/myapp, callback URL
<PUBLIC_URL>/api/integrations/confluence/callback, the SCOPES below). The app,
its token rotation and the API gateway are shared with Jira: connectors/atlassian.py.

API references (checked 2026-10-27):
  OAuth 2.0 (3LO)     https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
  scopes              https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/
  REST v2 (spec)      https://dac-static.atlassian.com/cloud/confluence/openapi-v2.v3.json
  REST v1 (spec)      https://dac-static.atlassian.com/cloud/confluence/swagger.v3.json
  CQL fields          https://developer.atlassian.com/cloud/confluence/cql-fields/
  Data Center REST    https://docs.atlassian.com/ConfluenceServer/rest/8.5.4/
  personal tokens     https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html
"""

import datetime
import html
import json
import logging
import os
import re
import time
from urllib.parse import parse_qsl, urlsplit

import integrations_store as store
from connectors import atlassian, extract, http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Fetched, FormError,
                             Item, LimitReached, Skipped, Upsert)

log = logging.getLogger("uvicorn.error")

CLOUD = "confluence"
DATA_CENTER = "confluence_dc"

API = f"{atlassian.GATEWAY}/confluence"                 # + /{cloudid}/wiki/...
# Granular read scopes, one per API the sync calls (the v2 spec's
# x-atlassian-oauth2-scopes; the v1 search and download list these granular
# scopes as "Beta" next to their classic ones). offline_access: a refresh token.
SCOPES = " ".join((
    "read:space:confluence",               # v2 /spaces, /spaces/{id}/permissions
    "read:page:confluence",                # v2 pages and blog posts (bodies too)
    "read:blogpost:confluence",            # blog posts (the scopes page lists it)
    "read:attachment:confluence",          # attachment download (v1)
    "read:content-details:confluence",     # v1 CQL /content/search
    "read:content.restriction:confluence", # restrictions in the search's expand
    "offline_access",
))

RATE = atlassian.RATE                 # calls a second per token
NEAR_LIMIT_PAUSE = atlassian.NEAR_LIMIT_PAUSE
V2_PAGE = 250                         # v2 listings: limit max 250
V1_PAGE = 100                         # v1 listings / CQL search
RECONCILE = 24 * 60 * 60              # seconds between full listings (deletes, permissions)
TZ_SLACK = 14 * 60 * 60 + 10 * 60     # CQL dates are local to the site: look back this far
MAX_BYTES = 20 * 1024 * 1024          # attachments bigger than this are skipped
TYPES = ("page", "blogpost")
EXPAND = ("version,space,ancestors,container,"
          "restrictions.read.restrictions.user,restrictions.read.restrictions.group")
# Macros whose content lives elsewhere: a placeholder instead of nothing.
PLACEHOLDER_MACROS = ("jira", "include", "excerpt-include", "children", "pagetree",
                      "contentbylabel", "recently-updated", "toc", "blog-posts", "attachments")


# ---- storage format -> HTML extract.py reads ------------------------------------------
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_CODE_BODY = re.compile(r"<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>", re.S)
_MACRO = re.compile(r'<ac:structured-macro\b[^>]*\bac:name="([^"]+)"[^>]*?(?:/>|>(.*?)'
                    r"</ac:structured-macro>)", re.S)
_PARAM = re.compile(r"<ac:(?:parameter|task-id)\b[^>]*>.*?</ac:(?:parameter|task-id)>", re.S)
_TASK_STATUS = re.compile(r"<ac:task-status>\s*(complete|incomplete)\s*</ac:task-status>")


def storage_to_html(xhtml):
    """Confluence's storage format (XHTML with ac:/ri: tags) as plain HTML for
    extract.extract, shaped for chunking.py: headings become "# ..." lines and
    table rows "a | b" (extract's HTML reader), code blocks fenced (```), macro
    parameters dropped,
    macros that show other content (Jira issues, includes, page trees) replaced
    by a placeholder, task lists as [ ] / [x]."""
    text = xhtml or ""

    def macro(m):
        name, body = m.group(1), m.group(2) or ""
        if name in PLACEHOLDER_MACROS:
            key = re.search(r'<ac:parameter ac:name="key">([^<]+)</ac:parameter>', body)
            label = "Jira issue " + key.group(1) if name == "jira" and key else name
            return f"<p>[{html.escape(label)}]</p>"
        return m.group(0)

    text = _MACRO.sub(macro, text)
    # A code / noformat body as a fenced block (chunking.py keeps those whole);
    # any other CDATA (a link's text) as plain text.
    text = _CODE_BODY.sub(lambda m: "<pre>\n```\n" + html.escape(m.group(1).strip("\n"))
                          + "\n```\n</pre>", text)
    text = _CDATA.sub(lambda m: html.escape(m.group(1)), text)
    text = _PARAM.sub("", text)
    text = _TASK_STATUS.sub(lambda m: "[x] " if m.group(1) == "complete" else "[ ] ", text)
    text = text.replace("<ac:task>", "<p>").replace("</ac:task>", "</p>")
    return text


# ---- helpers ------------------------------------------------------------------------
def _ts(iso):
    try:
        return datetime.datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _cql_date(ts):
    """A CQL date ("yyyy-MM-dd HH:mm", https://developer.atlassian.com/cloud/confluence/cql-fields/)."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _cql_spaces(keys):
    return ", ".join('"' + k.replace('"', "") + '"' for k in keys)


def _next(link):
    """The query of a `_links.next` link (it carries the cursor, or start): the
    next call is the same endpoint with these parameters."""
    return dict(parse_qsl(urlsplit(link).query)) if link else None


def _sid(kind, content_id):
    return f"{kind}-{content_id}"


def _principals(restrictions):
    """(users, groups) from a v1 content's restrictions.read expansion."""
    r = ((restrictions or {}).get("read") or {}).get("restrictions") or {}
    users, groups = set(), set()
    for u in (r.get("user") or {}).get("results") or []:
        who = u.get("accountId") or u.get("username") or u.get("userKey")
        if who:
            users.add(f"atlassian:{who}")
        if u.get("email"):
            users.add(u["email"].lower())
    for g in (r.get("group") or {}).get("results") or []:
        who = g.get("id") or g.get("name")
        if who:
            groups.add(f"atlassian:group:{who}")
    return sorted(users), sorted(groups)


class _Doc:
    """One page, blog post or attachment, from whichever API listed it."""

    def __init__(self, kind, content_id, title, version, modified, space, url,
                 parent=None, ancestors=(), restrictions=None, mime="text/html", size=None,
                 container=None, download=None):
        self.kind, self.id, self.title = kind, str(content_id), title or "Untitled"
        self.sid = _sid(kind, content_id)
        self.version, self.modified, self.space, self.url = version, modified, space, url
        self.parent, self.ancestors, self.restrictions = parent, list(ancestors), restrictions
        self.mime, self.size, self.container, self.download = mime, size, container, download


def _from_v1(c, site):
    """A v1 content object (CQL search, Data Center listing) as a _Doc."""
    kind = c.get("type") or "page"
    version = c.get("version") or {}
    links = c.get("_links") or {}
    space = (c.get("space") or {}).get("key")
    url = site + links["webui"] if links.get("webui") else ""
    if kind == "attachment":
        container = c.get("container") or {}
        ctype = container.get("type") if container.get("type") in TYPES else "page"
        parent = _sid(ctype, container["id"]) if container.get("id") else None
        ext = c.get("extensions") or {}
        size = ext.get("fileSize")
        return _Doc(kind, c["id"], c.get("title"), str(version.get("number") or ""),
                    version.get("when"), space, url, parent=parent,
                    ancestors=[parent] if parent else [], mime=ext.get("mediaType") or "",
                    size=int(size) if size else None, container=container.get("id"),
                    download=links.get("download"))
    ancestors = [_sid("page", a["id"]) for a in c.get("ancestors") or [] if a.get("id")]
    return _Doc(kind, c["id"], c.get("title"), str(version.get("number") or ""),
                version.get("when"), space, url, parent=ancestors[-1] if ancestors else None,
                ancestors=ancestors, restrictions=c.get("restrictions"))


class _Confluence(Connector):
    """What Cloud and Data Center share: the picks, the sync, the item shape.
    Each edition supplies _session (where to call, how to authenticate) and
    the few calls that differ (_spaces, _listing, _space_read, _body, _download)."""
    PICKER = "list"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "spaces"
    UNIT = "pages"
    ACL = True

    # ---- calls --------------------------------------------------------------------
    def _get(self, ws, url, params=None, **kwargs):
        raise NotImplementedError

    def _json(self, ws, url, params=None):
        try:
            return self._get(ws, url, params).json()
        except ValueError:
            raise ConfigError("the answer isn't JSON") from None

    def _pages(self, ws, url, params, key="results"):
        """Every result of a paged listing (v2 cursor or v1 start: both are in
        `_links.next`)."""
        while params is not None:
            data = self._json(ws, url, params)
            yield from data.get(key) or []
            params = _next((data.get("_links") or {}).get("next"))

    def _search(self, ws, s, cql):
        return (_from_v1(c, s["site"]) for c in self._pages(
            ws, f"{s['rest']}/content/search", {"cql": cql, "limit": V1_PAGE, "expand": EXPAND}))

    # ---- picking ------------------------------------------------------------------
    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            p["kind"] = "space"
        return out

    # ---- syncing ------------------------------------------------------------------
    def _item(self, d, space_read):
        acl = {"space": d.space, "space_read": space_read.get(d.space)}
        if d.kind == "attachment":
            acl["inherits_from"] = d.ancestors
        else:
            users, groups = _principals(d.restrictions)
            acl.update(restricted=bool(users or groups), users=users, groups=groups,
                       inherits_from=d.ancestors)
            if d.restrictions is None:
                acl["partial"] = True          # not in the search index yet: next full run
        return Item(source_id=d.sid, title=d.title, url=d.url, version=d.version,
                    kind="file" if d.kind == "attachment" else "page",
                    mime=d.mime, modified=d.modified, size=d.size, parent_id=d.parent,
                    acl=acl, attrs={"type": d.kind, "id": d.id, "space": d.space,
                                    "container": d.container, "download": d.download})

    def _wanted(self, d):
        """An attachment worth syncing: a type extract.py reads (others aren't
        listed at all: images and the like would only fill the skipped list)."""
        return d.kind != "attachment" or extract.kind_of(d.mime, d.title) is not None

    def changes(self, ctx, cursor):
        picked = (ctx.settings or {}).get("spaces") or []
        state = json.loads(cursor) if cursor else {}
        if not picked:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        s = self._session(ws)
        keys = sorted(str(p["id"]) for p in picked)
        full = (ctx.full or not state or state.get("spaces") != keys
                or time.time() - state.get("full_at", 0) > RECONCILE)
        types = "type in (page, blogpost, attachment)"
        where = f"space in ({_cql_spaces(keys)}) AND {types}"
        if full:
            space_read = self._space_read(ws, s, keys)
            found = {d.sid: d for d in self._search(ws, s, where)}
            docs = []
            for key in keys:
                for d in self._listing(ws, s, key, space_read):
                    hit = found.get(d.sid)
                    if hit:                           # the listing is the truth for what
                        d.ancestors, d.parent = hit.ancestors, hit.parent   # exists; search
                        d.restrictions = hit.restrictions                   # adds the rest
                    docs.append(d)
            alive = {d.sid for d in docs}
            docs += [d for d in found.values() if d.kind == "attachment" and d.parent in alive]
        else:
            space_read = state.get("space_read") or {}
            since = (_ts(state.get("watermark")) or state.get("full_at", 0)) - TZ_SLACK
            docs = list(self._search(ws, s, f'{where} AND lastmodified >= "{_cql_date(since)}"'))
        newest = state.get("watermark") or ""
        seen, n = set(), 0
        for d in docs:
            if _ts(d.modified) > _ts(newest):
                newest = d.modified
            if d.sid in seen or d.space not in keys or not self._wanted(d):
                continue
            if ctx.max_items and n >= ctx.max_items:
                yield LimitReached(ctx.max_items)
                yield Cursor(cursor)               # deletes nothing; the next run tries again
                return
            seen.add(d.sid)
            n += 1
            if d.size and d.size > MAX_BYTES:
                yield Skipped(d.sid, "too_large", d.title, d.url)
            else:
                yield Upsert(self._item(d, space_read))
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"spaces": keys, "watermark": newest, "space_read": space_read,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def fetch(self, ctx, item):
        a = item.attrs or {}
        s = self._session(ctx.workspace)
        if a.get("type") == "attachment":
            if item.size and item.size > MAX_BYTES:
                return None
            return Fetched(self._download(ctx.workspace, s, a), item.mime)
        return Fetched(storage_to_html(self._body(ctx.workspace, s, a["type"], a["id"])),
                       "text/html")


# ---- Confluence Cloud ---------------------------------------------------------------
class ConfluenceCloud(_Confluence):
    PROVIDER = CLOUD
    LABEL = "Confluence"
    DESCRIPTION = "Pages, blog posts and attachments from the spaces you pick"
    AUTH = "oauth"
    SETUP_ENV = atlassian.SETUP_ENV
    CALLBACK = atlassian.CALLBACK
    tokens = atlassian.Session(CLOUD, "Confluence")

    def configured(self):
        return atlassian.configured()

    def auth_url(self, redirect_uri, state):
        return atlassian.authorize_url(os.getenv("ATLASSIAN_SCOPES") or SCOPES, redirect_uri,
                                       state)

    def finish_connect(self, workspace, code, redirect_uri, user):
        """Swap the code for tokens, find the Confluence site they reach, save."""
        t = atlassian.exchange(code, redirect_uri)
        site = atlassian.first_site(t["access_token"], "confluence")
        row = store.get(workspace, CLOUD)
        store.connect(workspace, CLOUD, atlassian.account_label(site), {
            **t, "cloud_id": site["id"], "site_url": (site.get("url") or "").rstrip("/")}, user)
        if row and (row.get("settings") or {}).get("cloud_id") != site["id"]:
            store.update(workspace, CLOUD, settings=dict(row.get("settings") or {}, spaces=[],
                                                         cloud_id=site["id"]))
        elif not row:
            store.update(workspace, CLOUD, settings={"cloud_id": site["id"]})

    def _secret(self, workspace):
        return self.tokens.secret(workspace)

    def _access_token(self, workspace, force=False):
        return self.tokens.access_token(workspace, force)

    def _session(self, ws):
        sec = self._secret(ws)
        wiki = f"{API}/{sec['cloud_id']}/wiki"
        return {"v2": f"{wiki}/api/v2", "rest": f"{wiki}/rest/api",
                "site": (sec.get("site_url") or "") + "/wiki"}

    def _get(self, ws, url, params=None, **kwargs):
        return self.tokens.get(ws, url, params, **kwargs)

    def list_containers(self, ctx, parent=None):
        s = self._session(ctx.workspace)
        spaces = self._pages(ctx.workspace, f"{s['v2']}/spaces",
                             {"limit": V2_PAGE, "status": "current"})
        return sorted(({"id": sp["key"], "name": sp.get("name") or sp["key"], "kind": "space",
                        "space_type": sp.get("type")} for sp in spaces if sp.get("key")),
                      key=lambda c: c["name"].lower())

    def _space_read(self, ws, s, keys):
        """{space key: who may read it} from the space permission assignments
        (v2 GET /spaces/{id}/permissions; operation read on the space)."""
        out = {}
        found = self._pages(ws, f"{s['v2']}/spaces", {"keys": ",".join(keys), "limit": V2_PAGE})
        for sp in found:
            users, groups, roles = set(), set(), set()
            for p in self._pages(ws, f"{s['v2']}/spaces/{sp['id']}/permissions",
                                 {"limit": V2_PAGE}):
                op, who = p.get("operation") or {}, p.get("principal") or {}
                if op.get("key") != "read" or op.get("targetType") != "space" or not who.get("id"):
                    continue
                {"user": users, "group": groups}.get(who.get("type"), roles).add(
                    f"atlassian:{'' if who.get('type') == 'user' else who.get('type') + ':'}"
                    f"{who['id']}")
            out[sp["key"]] = {"users": sorted(users), "groups": sorted(groups),
                              "roles": sorted(roles), "space_id": sp["id"]}
        missing = set(keys) - set(out)
        if missing:
            raise ConfigError(f"picked spaces are gone: {sorted(missing)}")
        return out

    def _listing(self, ws, s, key, space_read):
        """The space's current pages and blog posts (v2: the authoritative list)."""
        space_id = space_read[key]["space_id"]
        for kind, path in (("page", "pages"), ("blogpost", "blogposts")):
            for p in self._pages(ws, f"{s['v2']}/spaces/{space_id}/{path}",
                                 {"limit": V2_PAGE, "status": "current"}):
                version = p.get("version") or {}
                webui = (p.get("_links") or {}).get("webui")
                parent = p.get("parentId") if p.get("parentType") in (None, "page") else None
                yield _Doc(kind, p["id"], p.get("title"), str(version.get("number") or ""),
                           version.get("createdAt"), key, s["site"] + webui if webui else "",
                           parent=_sid("page", parent) if parent else None,
                           ancestors=[_sid("page", parent)] if parent else [])

    def _body(self, ws, s, kind, content_id):
        path = "pages" if kind == "page" else "blogposts"
        data = self._json(ws, f"{s['v2']}/{path}/{content_id}", {"body-format": "storage"})
        return ((data.get("body") or {}).get("storage") or {}).get("value") or ""

    def _download(self, ws, s, attrs):
        """v1 download: a 302 to the file, which requests follows."""
        r = self._get(ws, f"{s['rest']}/content/{attrs['container']}/child/attachment/"
                          f"{attrs['id']}/download", timeout=60)
        return r.content

    def disconnect(self, workspace):
        """Atlassian has no revoke endpoint for 3LO apps: the admin removes the app
        under their Atlassian account's Connected apps if it matters."""


# ---- Confluence Data Center / Server ------------------------------------------------
def _base(url):
    """The site's address as typed ("https://wiki.acme.com/confluence/", ".../rest/api")."""
    url = (url or "").strip().rstrip("/")
    for tail in ("/rest/api", "/rest"):
        if url.endswith(tail):
            url = url[:-len(tail)]
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname or parts.query or parts.fragment:
        raise FormError("Enter the Confluence address, like https://confluence.example.com.")
    return url


class ConfluenceDataCenter(_Confluence):
    PROVIDER = DATA_CENTER
    LABEL = "Confluence Data Center"
    DESCRIPTION = "Self-hosted Confluence (7.9+): pages, blog posts and attachments"
    AUTH = "token"

    def _conn(self, ws):
        row = store.get(ws, DATA_CENTER)
        sec = store.secret(ws, DATA_CENTER)
        if not row or not sec or not sec.get("token"):
            raise AuthExpired("Confluence Data Center is not connected")
        base = (row.get("settings") or {}).get("base_url")
        if not base:
            raise ConfigError("no Confluence address")
        return base, sec["token"]

    def _call(self, base, token, url, params=None, **kwargs):
        r = http.request("GET", url, params=params, guard=http.guard_public, rate=RATE,
                         burst=RATE, bucket=http.bucket_key(DATA_CENTER, token),
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/json"}, **kwargs)
        if r.status_code >= 300:          # a redirect: a sign-in page in front of the API
            raise ConfigError(f"GET {http.safe_url(url)}: redirected")
        return r

    def _get(self, ws, url, params=None, **kwargs):
        base, token = self._conn(ws)
        return self._call(base, token, url, params, **kwargs)

    def _session(self, ws):
        base, _ = self._conn(ws)
        return {"rest": f"{base}/rest/api", "site": base, "base": base}

    def connect_form(self, workspace, form, user):
        """Check the address and token by asking who the token belongs to
        (GET /rest/api/user/current), then save the connection (token encrypted)."""
        base = _base(form.get("url"))
        token = (form.get("token") or "").strip()
        if not token:
            raise FormError("Paste a personal access token (your avatar → Settings → "
                            "Personal access tokens).")
        try:
            me = self._call(base, token, f"{base}/rest/api/user/current").json()
        except AuthExpired:
            raise FormError("Confluence didn't accept that token.") from None
        except ConfigError as e:
            raise FormError(f"Couldn't use that address: {e}") from None
        except Exception:
            raise FormError("Couldn't reach Confluence at that address.") from None
        if not isinstance(me, dict) or me.get("type") == "anonymous":
            raise FormError("Confluence didn't accept that token.")
        host = urlsplit(base).hostname
        who = me.get("displayName") or me.get("username")
        account = f"{host} ({who})" if who else host
        row = store.get(workspace, DATA_CENTER)
        store.connect(workspace, DATA_CENTER, account, {"token": token}, user)
        settings = dict((row or {}).get("settings") or {}, base_url=base)
        if row and (row.get("settings") or {}).get("base_url") != base:
            settings["spaces"] = []           # another site: its spaces differ
        store.update(workspace, DATA_CENTER, settings=settings)
        return account

    def list_containers(self, ctx, parent=None):
        s = self._session(ctx.workspace)
        spaces = self._pages(ctx.workspace, f"{s['rest']}/space",
                             {"limit": V1_PAGE, "status": "current"})
        return sorted(({"id": sp["key"], "name": sp.get("name") or sp["key"], "kind": "space",
                        "space_type": sp.get("type")} for sp in spaces if sp.get("key")),
                      key=lambda c: c["name"].lower())

    def _space_read(self, ws, s, keys):
        """Data Center's REST API has no call to read space permissions: unknown
        (None), so enforcement later can tell it from "nobody"."""
        return {k: None for k in keys}

    def _listing(self, ws, s, key, space_read):
        for kind in TYPES:
            for c in self._pages(ws, f"{s['rest']}/content", {
                    "spaceKey": key, "type": kind, "status": "current", "limit": V1_PAGE,
                    "expand": "version,space,ancestors"}):
                d = _from_v1(c, s["site"])
                d.restrictions = None
                yield d

    def _body(self, ws, s, kind, content_id):
        data = self._json(ws, f"{s['rest']}/content/{content_id}", {"expand": "body.storage"})
        return ((data.get("body") or {}).get("storage") or {}).get("value") or ""

    def _download(self, ws, s, attrs):
        link = attrs.get("download") or ""
        if not link.startswith("/"):
            raise ConfigError("attachment without a download link")
        return self._get(ws, s["base"] + link, timeout=60).content


CLOUD_CONNECTOR = ConfluenceCloud()
DC_CONNECTOR = ConfluenceDataCenter()
