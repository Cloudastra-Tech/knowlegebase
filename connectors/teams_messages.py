"""
connectors/teams_messages.py  —  sync Microsoft Teams CHANNEL MESSAGES into an index.

A source, not the Teams bot (kb/routers/teams.py answers questions in Teams;
this reads what people wrote in channels). Read-only, through Microsoft Graph,
as the APPLICATION: a multi-tenant Entra app with the application permissions
Team.ReadBasic.All, Channel.ReadBasic.All, ChannelMessage.Read.All,
TeamMember.Read.All and ChannelMember.Read.All, which a tenant admin approves
for their organisation (admin consent). Connecting is SharePoint's two trips
(connectors/sharepoint.py): the admin signs in (their tenant comes from the ID
token), then approves the app for that tenant if it hasn't been.

PROTECTED APIs: Microsoft treats Teams message APIs as protected: an app may
read channel messages without a user only after Microsoft has approved its
request ("Microsoft Teams APIs in Microsoft Graph that access sensitive data
are considered protected APIs", https://learn.microsoft.com/en-us/microsoftteams/export-teams-content).
Until then Graph answers 403 "Invoked API requires Protected API access in
application-only context ..." and the sync stops with ProtectedApi
(connectors/base.py: "protected_api", not retried, the card says so). It is the
APP that needs approval (the server's operator asks Microsoft), not the tenant.

OPT-IN and LOCKED: a Teams connection syncs nothing until an admin picks teams
or channels, and its messages are for HR and admins only
(settings["access_lock"]) until an admin turns on "let members ask" in the
picker; only then can the policy follow channel membership (source).

What is synced: each THREAD (a root message and its replies) of the picked
channels, as one Markdown document with the team, channel and date: who wrote
what, oldest first. System events and deleted messages are left out. Chats
(1:1 and group) are never read. Files posted in channels live in SharePoint
(the SharePoint connector).

How a sync finds what changed, per channel:
  GET /teams/{t}/channels/{c}/messages/delta: the first round lists the
  channel's root messages (the last eight months: older ones aren't returned by
  delta); its final @odata.deltaLink is kept and the next round returns only
  roots created or changed since. A root with deletedDateTime is deleted. For
  every listed root its replies are read (GET .../messages/{id}/replies) and
  the thread's version is its newest edit, so a changed reply re-indexes it.
  FULL (first run, the picks or the channels under them changed, every
  RECONCILE seconds, or asked for): delta from scratch; threads known but not
  listed go behind the mass-delete guard. A reply to an old thread whose root
  didn't change is caught at the next full run (worst case RECONCILE).

Permissions (the acl of every thread of a channel):
  standard channel   the team's members (emails, GET /teams/{t}/members) and
                     its Microsoft 365 group (entra:<team id>: a team's id is its
                     group's);
  private / shared   the channel's own members (GET .../channels/{c}/allMembers:
                     direct, and for a shared channel the members of the teams
                     it's shared with).
  Members that can't be read: partial, for no one (kb/services/permissions.py).

Throttling: GET channel messages is limited to 20 requests a second per app per
tenant and 1 a second per channel (https://learn.microsoft.com/en-us/graph/throttling-limits,
"Channel messages"): message calls share a 1/s bucket per channel, the rest a
RATE bucket per tenant; 429 / 503 Retry-After is waited out (connectors/http.py).

Needs TEAMS_MESSAGES_CLIENT_ID and TEAMS_MESSAGES_CLIENT_SECRET: an app
registration (multitenant), Web redirect URI
<PUBLIC_URL>/api/integrations/teams_messages/callback, the application
permissions above (and delegated openid / profile / email for the sign-in),
and Microsoft's approval for the protected APIs.

API references (checked 2026-10-27):
  teams          https://learn.microsoft.com/en-us/graph/api/teams-list (Team.ReadBasic.All)
  channels       https://learn.microsoft.com/en-us/graph/api/channel-list (membershipType
                 standard | private | shared)
  delta          https://learn.microsoft.com/en-us/graph/api/chatmessage-delta (channel:
                 GET /teams/{t}/channels/{c}/messages/delta, ChannelMessage.Read.All; roots
                 only, last eight months; @odata.nextLink / @odata.deltaLink; $top)
  replies        https://learn.microsoft.com/en-us/graph/api/chatmessage-list-replies
  messages       https://learn.microsoft.com/en-us/graph/api/resources/chatmessage
                 (messageType message | systemEventMessage; deletedDateTime; body
                 contentType html | text; from.user.displayName)
  members        https://learn.microsoft.com/en-us/graph/api/team-list-members,
                 https://learn.microsoft.com/en-us/graph/api/channel-list-allmembers
                 (aadUserConversationMember: email, userId)
  protected APIs https://learn.microsoft.com/en-us/microsoftteams/export-teams-content
  throttling     https://learn.microsoft.com/en-us/graph/throttling-limits
"""

import hashlib
import html
import json
import logging
import os
import re
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, ConnectRefused, Connector, Cursor,
                             Delete, Forbidden, FormError, Item, LimitReached, MissingScope,
                             NextStep, NotFound, ProtectedApi, Upsert)
from connectors.sharepoint import GRAPH, GRAPH_DEFAULT, LOGIN, NO_CONSENT, _claims

log = logging.getLogger("uvicorn.error")

PROVIDER = "teams_messages"
SIGN_IN_SCOPES = "openid profile email"
RATE = 8                               # calls a second per tenant (not messages)
CHANNEL_RATE = 1                       # message calls a second per channel
PAGE = 50
RECONCILE = 24 * 60 * 60
CONSENT_TRIES = 3


def _protected(r):
    """Is a 403 Graph's "this app isn't approved for Teams' protected APIs"?"""
    try:
        message = ((r.json() or {}).get("error") or {}).get("message") or ""
    except ValueError:
        message = ""
    return "protected api" in message.lower()


def _text(body):
    """A message body (html or text) as plain text."""
    content = (body or {}).get("content") or ""
    if (body or {}).get("contentType") == "html":
        content = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", content, flags=re.I)
        content = html.unescape(re.sub(r"<[^>]+>", "", content))
    return re.sub(r"\n{3,}", "\n\n", content).strip()


def _author(m):
    who = (m.get("from") or {})
    return ((who.get("user") or {}).get("displayName")
            or (who.get("application") or {}).get("displayName") or "Someone")


def _keep(m):
    return m.get("messageType", "message") == "message" and not m.get("deletedDateTime")


class TeamsMessages(Connector):
    PROVIDER = PROVIDER
    LABEL = "Microsoft Teams messages"
    DESCRIPTION = "Conversations in the channels you pick"
    NOTE = ("Needs a Microsoft 365 admin to approve read-only access, and Microsoft's "
            "approval of this app for Teams' protected APIs. Messages are for HR and admins "
            "until you let members ask.")
    AUTH = "oauth"
    PICKER = "tree"
    PICKER_REQUIRED = True
    UNIT = "threads"
    SETUP_ENV = "TEAMS_MESSAGES_CLIENT_ID / TEAMS_MESSAGES_CLIENT_SECRET"
    ACL = True

    def configured(self):
        return bool(os.getenv("TEAMS_MESSAGES_CLIENT_ID")
                    and os.getenv("TEAMS_MESSAGES_CLIENT_SECRET"))

    # ---- connecting (as connectors/sharepoint.py) ------------------------------------
    def auth_url(self, redirect_uri, state):
        return f"{LOGIN}/organizations/oauth2/v2.0/authorize?" + urllib.parse.urlencode({
            "client_id": os.environ["TEAMS_MESSAGES_CLIENT_ID"], "response_type": "code",
            "redirect_uri": redirect_uri, "response_mode": "query", "scope": SIGN_IN_SCOPES,
            "state": state, "prompt": "select_account"})

    def _consent_url(self, tenant, redirect_uri, state):
        return f"{LOGIN}/{tenant}/v2.0/adminconsent?" + urllib.parse.urlencode({
            "client_id": os.environ["TEAMS_MESSAGES_CLIENT_ID"], "scope": GRAPH_DEFAULT,
            "redirect_uri": redirect_uri, "state": state})

    def callback_code(self, params, step=None):
        if step:
            return params.get("tenant", "") if params.get("admin_consent", "").lower() == "true" \
                else ""
        return params.get("code", "")

    def _app_token(self, tenant):
        r = http.request("POST", f"{LOGIN}/{tenant}/oauth2/v2.0/token", retries=1,
                         ok=(400, 401), data={
                             "client_id": os.environ.get("TEAMS_MESSAGES_CLIENT_ID", ""),
                             "client_secret": os.environ.get("TEAMS_MESSAGES_CLIENT_SECRET", ""),
                             "scope": GRAPH_DEFAULT, "grant_type": "client_credentials"})
        if r.status_code >= 400:
            try:
                err = r.json()
            except ValueError:
                err = {}
            if err.get("error") == "invalid_client":
                raise ConfigError("Microsoft refused the app's client secret")
            text = f"{err.get('error', '')} {err.get('error_description', '')}"
            e = AuthExpired("Microsoft refused the app token")
            e.consent = any(code in text for code in NO_CONSENT)
            raise e
        t = r.json()
        return {"access_token": t["access_token"],
                "expires_at": time.time() + int(t.get("expires_in", 3600)) - 60}

    def finish_connect(self, workspace, code, redirect_uri, user, step=None):
        if step is None:
            r = http.request("POST", f"{LOGIN}/organizations/oauth2/v2.0/token", retries=1,
                             ok=(400, 401), data={
                                 "client_id": os.environ.get("TEAMS_MESSAGES_CLIENT_ID", ""),
                                 "client_secret": os.environ.get("TEAMS_MESSAGES_CLIENT_SECRET",
                                                                 ""),
                                 "code": code, "redirect_uri": redirect_uri,
                                 "grant_type": "authorization_code", "scope": SIGN_IN_SCOPES})
            if r.status_code >= 400:
                raise ConfigError(f"Microsoft refused the sign-in code (HTTP {r.status_code})")
            claims = _claims(r.json().get("id_token"))
            tenant = claims.get("tid")
            if not tenant:
                raise ConnectRefused("connect_failed", "no tenant in the ID token")
            who = claims.get("preferred_username") or claims.get("email") or ""
            try:
                token = self._app_token(tenant)
            except AuthExpired as e:
                if not getattr(e, "consent", False):
                    raise
                raise NextStep(lambda state: self._consent_url(tenant, redirect_uri, state),
                               {"tenant": tenant, "who": who}) from None
            return self._save(workspace, tenant, who, token, user)
        if code != step.get("tenant"):
            raise ConnectRefused("wrong_account",
                                 "admin consent came back for another tenant than the sign-in")
        for attempt in range(CONSENT_TRIES):
            try:
                token = self._app_token(code)
                break
            except AuthExpired as e:
                if not getattr(e, "consent", False) or attempt == CONSENT_TRIES - 1:
                    raise ConnectRefused("admin_consent_required", str(e)) from None
                http.sleep(5 * (attempt + 1))
        return self._save(workspace, code, step.get("who") or "", token, user)

    def _save(self, workspace, tenant, who, token, user):
        """Check the app can list teams, then save the connection, LOCKED to HR and
        admins (an admin opens it up in the picker)."""
        try:
            self._graph(None, "GET", f"{GRAPH}/teams", token=token["access_token"],
                        tenant=tenant, params={"$top": 1}, container=True)
        except MissingScope:
            raise ConnectRefused("missing_scope", "Team.ReadBasic.All not granted") from None
        domain = who.split("@")[-1] if "@" in who else ""
        account = f"{domain} ({who})" if domain else (who or f"tenant {tenant[:8]}")
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, {"tenant": tenant, **token}, user)
        settings = dict((row or {}).get("settings") or {}, tenant=tenant)
        if row and (row.get("settings") or {}).get("tenant") != tenant:
            settings["containers"] = []           # another organisation
        if not row:
            settings.update(access_lock="restricted", members=False)
        store.update(workspace, PROVIDER, settings=settings)

    # ---- tokens and calls -------------------------------------------------------------
    def _access_token(self, workspace, force=False):
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = store.secret(workspace, PROVIDER)
            if not sec or not sec.get("tenant"):
                raise AuthExpired("Teams is not connected")
            if not force and sec.get("access_token") and sec.get("expires_at", 0) > time.time():
                return sec["access_token"], sec["tenant"]
            sec.update(self._app_token(sec["tenant"]))
            store.update(workspace, PROVIDER, secret=sec)
            return sec["access_token"], sec["tenant"]

    def _graph(self, workspace, method, url, token=None, tenant=None, container=False,
               channel=None, **kwargs):
        """One Graph call; a 401 fetches a new app token once. A 403 is the protected
        API refusal (ProtectedApi), a missing permission on a listing the app should
        always be allowed (`container`: MissingScope), or one item's (Forbidden)."""
        for attempt in (0, 1):
            if workspace is not None:
                token, tenant = self._access_token(workspace, force=attempt == 1)
            bucket, rate = ((http.bucket_key(PROVIDER, tenant, channel), CHANNEL_RATE) if channel
                            else (http.bucket_key(PROVIDER, tenant), RATE))
            try:
                r = http.request(method, url, bucket=bucket, rate=rate, burst=max(rate, 1),
                                 ok=(403,), headers={"Authorization": f"Bearer {token}"},
                                 **kwargs)
            except AuthExpired:
                if attempt == 1 or workspace is None:
                    raise
                continue
            if r.status_code == 403:
                where = f"{method} {http.safe_url(url)}: HTTP 403"
                if _protected(r):
                    raise ProtectedApi(where)
                raise (MissingScope if container else Forbidden)(where)
            return r

    def _all(self, ws, url, params=None, **kwargs):
        while url:
            data = self._graph(ws, "GET", url, params=params, **kwargs).json() or {}
            yield from data.get("value") or []
            url, params = data.get("@odata.nextLink"), None

    # ---- picking ----------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        ws = ctx.workspace
        if not parent:
            return sorted(({"id": f"team-{t['id']}", "name": t.get("displayName") or t["id"],
                            "kind": "team", "has_children": True}
                           for t in self._all(ws, f"{GRAPH}/teams", container=True)),
                          key=lambda c: c["name"].lower())
        team = parent.removeprefix("team-")
        return sorted(({"id": f"channel-{team}|{c['id']}", "name": c.get("displayName") or c["id"],
                        "kind": "channel",
                        "private": c.get("membershipType") in ("private", "shared")}
                       for c in self._channels(ws, team)), key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            kind, _, rest = p["id"].partition("-")
            if kind == "team" and rest:
                p["kind"] = "team"
            elif kind == "channel" and "|" in rest:
                p["kind"] = "channel"
            else:
                raise FormError("Pick Teams teams or channels.")
        return out

    def check_options(self, options, settings):
        """"Let members ask" (members): off keeps the connection locked to HR and admins."""
        members = bool((options or {}).get("members"))
        return {"members": members, "access_lock": None if members else "restricted"}

    def options_of(self, settings):
        return {"members": bool((settings or {}).get("members"))}

    def _channels(self, ws, team):
        return list(self._all(ws, f"{GRAPH}/teams/{team}/channels", container=True))

    # ---- syncing ----------------------------------------------------------------------
    def _scope(self, ws, picks):
        """{(team, channel): (team name, channel name, membershipType)} the picks cover."""
        names, out = {}, {}
        for p in picks:
            kind, _, rest = p["id"].partition("-")
            team = rest.split("|", 1)[0]
            if team not in names:
                try:
                    names[team] = {c["id"]: c for c in self._channels(ws, team)}
                except NotFound:
                    names[team] = {}               # the team is gone
            for cid, c in names[team].items():
                if kind == "team" or rest == f"{team}|{cid}":
                    out[(team, cid)] = c
        return out

    def _acl(self, ws, team, channel, cache):
        key = (team, channel["id"]) if channel.get("membershipType") in ("private", "shared") \
            else (team, None)
        if key not in cache:
            url = (f"{GRAPH}/teams/{team}/channels/{channel['id']}/allMembers" if key[1]
                   else f"{GRAPH}/teams/{team}/members")
            try:
                users = sorted({(m.get("email") or "").lower()
                                for m in self._all(ws, url)} - {""})
                cache[key] = {"via": "teams", "channel": channel["id"], "users": users,
                              "groups": [] if key[1] else [f"entra:{team}"]}
            except (Forbidden, NotFound, MissingScope):
                cache[key] = {"via": "teams", "channel": channel["id"], "partial": True}
        return dict(cache[key], channel=channel["id"])

    def changes(self, ctx, cursor):
        picks = (ctx.settings or {}).get("containers") or []
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        ws = ctx.workspace
        state = json.loads(cursor) if cursor else {}
        scope = self._scope(ws, picks)
        sig = hashlib.sha256(json.dumps(sorted(f"{t}|{c}" for t, c in scope)).encode()
                             ).hexdigest()[:16]
        full = (ctx.full or not state or state.get("channels") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        links = {} if full else dict(state.get("delta") or {})
        cache, seen, n = {}, set(), 0
        for (team, cid), channel in sorted(scope.items()):
            key = f"{team}|{cid}"
            url = links.get(key) or (f"{GRAPH}/teams/{team}/channels/"
                                     f"{urllib.parse.quote(cid, safe='')}/messages/delta")
            params = None if links.get(key) else {"$top": PAGE}
            acl = self._acl(ws, team, channel, cache)
            while url:
                data = self._graph(ws, "GET", url, params=params, channel=key).json() or {}
                for m in data.get("value") or []:
                    sid = f"{key}|{m['id']}"
                    if m.get("deletedDateTime") or m.get("messageType", "message") != "message":
                        if sid in ctx.known:
                            yield Delete(sid)
                        continue
                    if ctx.max_items and n >= ctx.max_items:
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)
                        return
                    seen.add(sid)
                    n += 1
                    yield Upsert(self._thread(ws, team, channel, key, m, acl))
                params = None
                if data.get("@odata.deltaLink"):
                    links[key] = data["@odata.deltaLink"]
                url = data.get("@odata.nextLink")
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"channels": sig, "delta": links,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def _thread(self, ws, team, channel, key, root, acl):
        """A root message and its replies as one item (text in hand: no fetch)."""
        replies = [r for r in self._all(
            ws, f"{GRAPH}/teams/{team}/channels/{urllib.parse.quote(channel['id'], safe='')}"
                f"/messages/{root['id']}/replies", channel=key) if _keep(r)]
        replies.sort(key=lambda r: r.get("createdDateTime") or "")
        stamps = [root.get("lastModifiedDateTime") or root.get("createdDateTime") or ""] + [
            r.get("lastModifiedDateTime") or r.get("createdDateTime") or "" for r in replies]
        first = _text(root.get("body"))
        subject = (root.get("subject") or "").strip() or (first.splitlines() or [""])[0][:80]
        when = (root.get("createdDateTime") or "")[:10]
        lines = [f"Channel: {channel.get('displayName') or ''}", f"Started: {when}", ""]
        for m in [root] + replies:
            lines.append(f"### {_author(m)}, {(m.get('createdDateTime') or '')[:16].replace('T', ' ')}"
                         f"\n\n{_text(m.get('body'))}\n")
        return Item(source_id=f"{key}|{root['id']}",
                    title=f"{channel.get('displayName') or 'Channel'}: {subject or 'Thread'}",
                    url=root.get("webUrl") or "", version=max(stamps), kind="thread",
                    mime="text/markdown", modified=max(stamps), parent_id=key, acl=acl,
                    attrs={"replies": len(replies)}, content="\n".join(lines).strip())

    def disconnect(self, workspace):
        """Nothing to revoke: app tokens have no refresh token. The tenant's admin
        removes the app (Entra admin center -> Enterprise applications) to cut access."""
        log.info("Teams messages: disconnected workspace %s; remove the app in Entra to "
                 "revoke its consent", workspace)


CONNECTOR = TeamsMessages()
