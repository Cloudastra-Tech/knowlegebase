"""
The Microsoft Teams bot (platform.md PL-23): people ask in Teams and get the same
checked, cited answers as in the web chat, through answering.ask(). It follows
the Slack app (kb/services/slack.py) wherever Teams allows.

THE BOT. An Azure Bot (Bot Framework) with a Teams app (teams/manifest.json).
Azure stopped creating multi-tenant bots after 31 July 2025, so a customer
normally registers their OWN single-tenant bot and pastes its App ID, tenant and
client secret into Integrations (mode "custom", secret sealed: teams_store.py);
its messaging endpoint is /api/teams/apps/<app_key>/messages. A host with an
existing multi-tenant bot can run it for every workspace (mode "central",
TEAMS_APP_ID / TEAMS_APP_PASSWORD, endpoint /api/teams/messages).

EVERY REQUEST IS CHECKED (verify()) before anything in it is read: the Bot
Connector's JWT in "Authorization: Bearer", signed by a key from Bot
Framework's published keys that is endorsed for the activity's channel (else
403), issued by https://api.botframework.com, for this bot's App ID, in date
(5 minutes' skew), and naming the activity's serviceUrl. Teams wants a quick
answer, so a question is only checked and queued (kb/routers/teams.py); a job
(kb/jobs.py, kind "teams") posts "Looking that up…", answers, and updates that
message with the card.

WHO ASKS. The activity's tenant must be the workspace's. The person is their
Entra object ID (from.aadObjectId), whose email or UPN (the Connector's member
lookup) is the member they are in the workspace; their role, and whether
they're turned off, are looked up at every question. People who aren't members
are refused (the default) or answered as public, by the workspace's setting.

THE AUDIENCE RULE. Only the asker reads their 1:1 chat with the bot, so only
there are they answered with their own access. A channel or a group chat is
answered from PUBLIC documents, and the card says so. When the asker's role
could have found more there, they're answered again, as themselves, in their
1:1 chat with the bot (a proactive message: it needs the app installed for them
personally; if it isn't, Teams says 403 and the channel's note is all they get).
Nothing is said in the channel about restricted sources existing.

THE ANSWER. An Adaptive Card (answer_card, version 1.5; Teams renders up to 1.6):
the answer with its [S#] markers as numbered links to the source (or to the
app's role-checked passage view), the sources, and 👍 / 👎 (Action.Execute, an
adaptiveCard/action invoke; an older client's Action.Submit arrives as a
message with a value and is taken the same way), landing in message_feedback
like the web chat's. Every answer is saved in a conversation of the asker's
and logged in the question log with surface "teams".

Microsoft's documents this follows (checked 2026-10-27):
  auth:      https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication
  bot types: https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-registration
  REST API:  https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-api-reference
  proactive: https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages
  mentions:  https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-and-group-conversations
  cards:     https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-reference
  actions:   https://learn.microsoft.com/en-us/adaptive-cards/authoring-cards/universal-action-model
"""

import email.utils
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import jwt
import requests
from fastapi import HTTPException

import access
import audit_store
import conversations_store
import groups_store
import members_store
import slack_store
import teams_store
from kb import jobs, state
from kb.rate_limit import inbound_limit, user_limit
from kb.services import answering, conversations
from kb.services.slack import restricted_found

log = logging.getLogger("uvicorn.error")

OPENID_METADATA = "https://login.botframework.com/v1/.well-known/openidconfiguration"
JWKS_URL = "https://login.botframework.com/v1/.well-known/keys"
ISSUER = "https://api.botframework.com"
SKEW = 5 * 60                      # "industry-standard clock-skew is 5 minutes"
KEYS_FOR = 24 * 60 * 60            # refresh the keys at least daily
KEYS_AGAIN_AFTER = 5 * 60          # an unknown kid fetches again, at most this often
TOKEN_SCOPE = "https://api.botframework.com/.default"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
MULTI_TENANT = "botframework.com"  # the token authority of a multi-tenant bot
CACHE_SECONDS = 10 * 60            # a person's email / UPN
TIMEOUT = 10
MAX_WAIT = 30                      # a longer Retry-After isn't waited out
THREAD_IDLE = 60 * 60              # a 1:1 chat's question this long after the last starts anew
CARD_VERSION = "1.5"
ANSWER_CHARS = 20_000              # a bot's card may be 100 KB
MAX_SOURCES = 10
JOB = "teams"
FEEDBACK_VERB = "kb_feedback"
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                  r"[0-9a-fA-F]{12}$")

WORKING = "Looking that up…"
UNKNOWN = ("I can only answer people who have an account in the Knowledge assistant, and "
           "I couldn't match your Microsoft account to one. Ask your admin to invite you.")
OTHER_ORG = "I only answer people in the organisation that added me to Teams."
DISABLED = ("Your Knowledge assistant account is turned off, so I can't answer you. Ask "
            "your admin if that's a mistake.")
PUBLIC_NOTE = ("Answered from documents everyone here may see. Ask me in a 1:1 chat for an "
               "answer from everything you can see.")
FAILED = "Sorry, I couldn't answer that just now ({kind}). Please try again in a moment."
LIMITED = "You're asking faster than I can answer. Try again in a minute."
THANKS = {1: "Thanks, glad it helped.", -1: "Thanks for telling us. HR and admins review "
                                            "answers marked not helpful."}

# Swapped by tests (tests/teams_fixtures.py) so nothing reaches Microsoft.
send = requests.request
sleep = time.sleep


# ---- The bot ------------------------------------------------------------------------------
def central_app():
    """The bot run by this server's host, or None if it isn't set up. Without
    TEAMS_APP_TENANT it's a multi-tenant bot."""
    app_id = os.getenv("TEAMS_APP_ID", "").strip()
    secret = os.getenv("TEAMS_APP_PASSWORD", "").strip()
    if not (app_id and secret):
        return None
    return {"app_id": app_id, "client_secret": secret,
            "tenant": os.getenv("TEAMS_APP_TENANT", "").strip() or MULTI_TENANT}


def app_of(install):
    """{app_id, client_secret, tenant} of the bot a workspace uses, or None."""
    if not install or install["mode"] == "central":
        return central_app()
    secret = teams_store.app_secret(install["workspace"])
    if not (secret and install["app_id"] and install["app_tenant"]):
        return None
    return {"app_id": install["app_id"], "client_secret": secret,
            "tenant": install["app_tenant"]}


def uninstall(ws):
    """Stop answering in Teams for a workspace (disconnect, or purging it): the
    bot, its sealed secret, the tenant, and the users, threads and activities
    seen in it are forgotten, with what this process cached for them. There is
    nothing to revoke at Microsoft: the customer's bot and its secret live in
    their Azure (the app is removed in Teams by its admin). Returns the install
    as it was, or None."""
    install = teams_store.remove(ws)
    if install:
        tenant = install["tenant_id"]
        with state.teams_lock:
            for key in [k for k in state.teams_people if k[0] == tenant]:
                del state.teams_people[key]
            for key in [k for k in state.teams_tokens if install["app_id"]
                        and k[0] == install["app_id"]]:
                del state.teams_tokens[key]
    return install


# ---- Checking a request from Teams --------------------------------------------------------
class Refused(Exception):
    """Not a request the Bot Connector sent this bot: `status` 401, or 403 for a key
    not endorsed for the channel (as the documentation says)."""

    def __init__(self, status, why):
        super().__init__(why)
        self.status, self.why = status, why


def _fetch_keys():
    r = send("GET", JWKS_URL, timeout=TIMEOUT)
    r.raise_for_status()
    return {k["kid"]: k for k in r.json().get("keys") or [] if k.get("kid")}


def signing_key(kid):
    """The published key with this id (with its endorsements), or None. Cached a
    day; an id not seen yet fetches the keys again (at most every 5 minutes)."""
    now = time.time()
    with state.teams_lock:
        fetched, keys = state.teams_keys.get("keys", (0, {}))
    stale = now - fetched > KEYS_FOR
    if stale or (kid not in keys and now - fetched > KEYS_AGAIN_AFTER):
        try:
            keys = _fetch_keys()
            with state.teams_lock:
                state.teams_keys["keys"] = (now, keys)
        except (requests.RequestException, ValueError) as e:
            log.warning("Teams: couldn't fetch Bot Framework's signing keys (%s)",
                        type(e).__name__)
            if stale:
                keys = {}
    return keys.get(kid)


def verify(authorization, app_id, activity):
    """The Bot Connector's token on a request, checked as Microsoft documents ->
    its claims. Raises Refused."""
    scheme, _, token = (authorization or "").strip().partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Refused(401, "no bearer token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise Refused(401, "not a JWT")
    if header.get("alg") != "RS256":
        raise Refused(401, "not RS256")
    key = signing_key(header.get("kid"))
    if not key:
        raise Refused(401, "unknown signing key")
    channel = (activity or {}).get("channelId")
    if channel not in (key.get("endorsements") or []):
        raise Refused(403, "key not endorsed for the channel")
    try:
        claims = jwt.decode(token, jwt.PyJWK(key, "RS256").key, algorithms=["RS256"],
                            audience=app_id, issuer=ISSUER, leeway=SKEW,
                            options={"require": ["exp", "iss", "aud"],
                                     "verify_iat": False})
    except jwt.PyJWTError as e:
        raise Refused(401, type(e).__name__)
    if not activity or claims.get("serviceUrl") != activity.get("serviceUrl"):
        raise Refused(401, "serviceUrl doesn't match")
    return claims


# ---- Calling Teams --------------------------------------------------------------------------
class TeamsError(Exception):
    """A call to Microsoft failed: `code` is ours ("http_403", "ratelimited",
    "network", "token"). Never carries a token."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _retry_after(response):
    value = response.headers.get("Retry-After", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        when = email.utils.parsedate_to_datetime(value) if value else None
        return max(0.0, when.timestamp() - time.time()) if when else 1.0


def _request(method, url, **kwargs):
    """One request, waiting out a 429's Retry-After (twice at most, MAX_WAIT each)."""
    for attempt in range(3):
        try:
            r = send(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as e:
            raise TeamsError("network") from e
        if r.status_code != 429:
            return r
        wait = _retry_after(r)
        if attempt == 2 or wait > MAX_WAIT:
            raise TeamsError("ratelimited")
        log.info("Teams: rate limited, waiting %.0f s", wait)
        sleep(wait)


def bot_token(app):
    """The bot's token for the Bot Connector (client credentials; cached until
    5 minutes before it expires)."""
    key = (app["app_id"], app["tenant"])
    with state.teams_lock:
        hit = state.teams_tokens.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    r = _request("POST", TOKEN_URL.format(tenant=quote(app["tenant"], safe="")),
                 data={"grant_type": "client_credentials", "client_id": app["app_id"],
                       "client_secret": app["client_secret"], "scope": TOKEN_SCOPE})
    try:
        got = r.json()
    except ValueError:
        got = {}
    if r.status_code != 200 or not got.get("access_token"):
        log.warning("Teams: Microsoft refused the bot's credentials (HTTP %s, %s)",
                    r.status_code, got.get("error") or "no token")
        raise TeamsError("token")
    with state.teams_lock:
        state.teams_tokens[key] = (time.time() + int(got.get("expires_in") or 3600) - 300,
                                   got["access_token"])
    return got["access_token"]


def connector(app, method, service_url, path, body=None):
    """A Bot Connector REST call on the activity's service URL -> its JSON."""
    parts = urlsplit(service_url or "")
    if parts.scheme != "https" or not parts.hostname:
        raise TeamsError("bad_service_url")
    url = service_url.rstrip("/") + path
    r = _request(method, url, json=body,
                 headers={"Authorization": f"Bearer {bot_token(app)}"})
    if r.status_code >= 400:
        raise TeamsError(f"http_{r.status_code}")
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {}


def _conv(conversation_id):
    return quote(conversation_id, safe="")


# ---- Who is asking ------------------------------------------------------------------------
@dataclass
class Asker:
    tenant_id: str
    aad_object_id: str
    username: str = ""             # the member, or "teams:<tenant>:<object id>" as public
    role: str = "public"           # the access role they may ask as: hr / public
    member: bool = False
    refusal: str | None = None     # set: don't answer, say this
    viewer: object = None          # a member's principals (access.Viewer), looked up now

    def who(self):
        """What they may read where they alone read the answer (their 1:1 chat)."""
        return self.viewer if self.viewer is not None else access.for_role(self.role)

    def user(self, ws):
        return {"username": self.username, "workspace": ws}


def tenant_of(activity):
    return (((activity.get("channelData") or {}).get("tenant") or {}).get("id")
            or (activity.get("conversation") or {}).get("tenantId") or "").lower()


def person(app, activity):
    """{email, upn} of the activity's sender, from the Connector's member lookup
    (cached): the documented way to get them; Teams doesn't put them in messages."""
    sender = activity.get("from") or {}
    key = (tenant_of(activity), sender.get("aadObjectId"))
    now = time.time()
    with state.teams_lock:
        hit = state.teams_people.get(key)
    if hit and hit[0] > now:
        return hit[1]
    conv = (activity.get("conversation") or {}).get("id", "")
    got = connector(app, "GET", activity["serviceUrl"],
                    f"/v3/conversations/{_conv(conv)}/members/{quote(sender['id'], safe='')}")
    found = {"email": (got.get("email") or "").strip().lower(),
             "upn": (got.get("userPrincipalName") or "").strip().lower()}
    with state.teams_lock:
        if len(state.teams_people) > 10_000:
            state.teams_people.clear()
        state.teams_people[key] = (now + CACHE_SECONDS, found)
    return found


def member_role(member):
    """The role a member asks as, from their role now: the same rule as a session
    (kb/deps.current_role) and an API key. What they may READ is member_viewer's."""
    return "hr" if member["role"] in members_store.STAFF_ROLES else "public"


def member_viewer(ws, member):
    """What a member may read (access.Viewer): their principals now, their groups
    too, as a session's (kb/deps.current_viewer)."""
    return groups_store.principals_for(ws, member["username"], member_role(member))


def identify(install, app, activity):
    """The Asker an activity's sender is in the workspace answering its tenant."""
    ws, sender = install["workspace"], activity.get("from") or {}
    asker = Asker(tenant_of(activity), sender.get("aadObjectId") or "")
    if asker.tenant_id != install["tenant_id"] or not asker.aad_object_id:
        asker.refusal = OTHER_ORG          # a guest from another organisation, a bot
        return asker
    p = person(app, activity)
    member = None
    for address in dict.fromkeys(a for a in (p["email"], p["upn"]) if a):
        found = members_store.check_group_admin(members_store.get(address))
        if found and found["workspace"] == ws:
            member = found
            break
    if member:
        if member["disabled"]:
            asker.refusal = DISABLED
            return asker
        asker.username, asker.member, asker.role = member["username"], True, member_role(member)
        asker.viewer = member_viewer(ws, member)
        return asker
    if install["unknown_users"] == "public":
        asker.username = f"teams:{asker.tenant_id}:{asker.aad_object_id}"
        return asker
    asker.refusal = UNKNOWN
    return asker


# ---- The card -----------------------------------------------------------------------------
_MARKER = re.compile(r"[ \t]*\[S(\d{1,3})\]")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)


def source_link(source, base_url):
    """Where a source's number links to: the source itself, or the app's passage
    view (sign-in and the reader's role are checked there)."""
    url = source.get("url") or ""
    if url.startswith(("https://", "http://")):
        return url.replace(" ", "%20").replace(")", "%29")
    if source.get("chunk_id") and base_url:
        return f"{base_url}/passages/{quote(str(source['chunk_id']), safe='')}"
    return None


def card_markdown(answer, sources=(), base_url=None):
    """An answer's markdown as an Adaptive Card TextBlock takes it (bold, italic,
    lists and links; no headings): headings bold, each [S#] a numbered link to its
    source, an [S#] with no such source dropped."""
    numbered = {s["n"]: s for s in sources if isinstance(s.get("n"), int)}

    def cite(m):
        source = numbered.get(int(m.group(1)))
        if not source:
            return ""
        url = source_link(source, base_url)
        return f" [[{m.group(1)}]]({url})" if url else f" [{m.group(1)}]"

    text = _MARKER.sub(cite, answer or "")
    text = _HEADING.sub(lambda m: f"**{m.group(1)}**", text)
    return text.strip()[:ANSWER_CHARS]


def _text(text, **extra):
    return {"type": "TextBlock", "text": text, "wrap": True, **extra}


def answer_card(done, message_id, base_url=None, open_url=None, public=False):
    """The Adaptive Card for an answer: its text, its sources, a note when it was
    answered from public documents only, and the feedback buttons (their data:
    the answer's message id)."""
    body = []
    badge = getattr(done, "verified", None)
    if badge:                        # a verified answer (PL-27): who verified it, and when
        when = time.strftime("%d %b %Y", time.gmtime(badge["at"]))
        body.append(_text(f"✅ Verified by {badge['by']} · {when}", size="Small",
                          isSubtle=True))
    body.append(_text(card_markdown(done.answer, done.sources, base_url) or "(no answer)"))
    if done.sources:
        lines = []
        for s in done.sources:
            n = s.get("n") if isinstance(s.get("n"), int) else None
            title = " ".join(str(s.get("title") or "Untitled").split())[:150]
            title = title.replace("[", "(").replace("]", ")")
            url = source_link(s, base_url)
            name = f"[{title}]({url})" if url else title
            provider = s.get("provider") or ""
            if s.get("kind") == "general_knowledge":
                provider = "General knowledge · " + provider
            lines.append((f"**[{n}]** {name}" if n else name)
                         + (f" · {provider}" if provider else ""))
        if len(lines) > MAX_SOURCES:
            lines = lines[:MAX_SOURCES - 1] + [f"and {len(lines) - MAX_SOURCES + 1} more"]
        body.append({"type": "Container", "separator": True, "spacing": "Medium", "items": [
            _text("Sources", weight="Bolder", size="Small"),
            *(_text(line, size="Small", spacing="Small") for line in lines)]})
    if public:
        body.append(_text(PUBLIC_NOTE, size="Small", isSubtle=True, separator=True))
    actions = []
    if message_id and done.state != "error":
        actions += [{"type": "Action.Execute", "title": "👍 Helpful", "verb": FEEDBACK_VERB,
                     "data": {"kb": FEEDBACK_VERB, "message_id": message_id, "rating": 1}},
                    {"type": "Action.Execute", "title": "👎 Not helpful", "verb": FEEDBACK_VERB,
                     "data": {"kb": FEEDBACK_VERB, "message_id": message_id, "rating": -1}}]
    if open_url:
        actions.append({"type": "Action.OpenUrl", "title": "Open in the app", "url": open_url})
    card = {"type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": CARD_VERSION, "body": body}
    if actions:
        card["actions"] = actions
    return card


def card_activity(card, text=None):
    """A message activity carrying one Adaptive Card (text: the notification's)."""
    return {"type": "message", "text": text or "",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                             "content": card}]}


def question_of(activity):
    """A message's text as a question: the bot's own @mention removed (by the
    activity's mention entities, as Teams says to), other <at> tags as names,
    HTML Teams adds stripped."""
    text = activity.get("text") or ""
    bot = (activity.get("recipient") or {}).get("id")
    for entity in activity.get("entities") or []:
        if entity.get("type") == "mention" and (entity.get("mentioned") or {}).get("id") == bot:
            text = text.replace(entity.get("text") or "\x00", " ")
    text = re.sub(r"</?at>", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&"))
    return " ".join(text.split())


# ---- Taking requests in (kb/routers/teams.py) ----------------------------------------------
def _queue(ws, key, payload):
    return jobs.enqueue(JOB, workspace=ws, payload=payload, lane=f"{JOB}:{ws}:{key}",
                        max_attempts=1)


def feedback_data(activity):
    """{message_id, rating} from a feedback click (an Action.Execute invoke, or an
    older client's Action.Submit message), or None."""
    value = activity.get("value") or {}
    if activity.get("type") == "invoke":
        action = value.get("action") or {}
        if action.get("verb") != FEEDBACK_VERB:
            return None
        value = action.get("data") or {}
    if value.get("kb") != FEEDBACK_VERB or value.get("rating") not in (1, -1):
        return None
    return {"message_id": str(value.get("message_id") or ""), "rating": value["rating"]}


def take(install, activity):
    """An activity Teams sent: queue what needs an answer. Returns what was done
    (for logs and tests): "queued", "duplicate", "ignored" or "limited"."""
    conv = activity.get("conversation") or {}
    tenant = tenant_of(activity)
    key = f"{conv.get('id')}|{activity.get('id')}"
    if not teams_store.first_time(key, tenant):
        return "duplicate"
    teams_store.seen(install["workspace"])
    sender = activity.get("from") or {}
    if sender.get("aadObjectId") and activity.get("serviceUrl"):
        personal = conv.get("id") if conv.get("conversationType") == "personal" else None
        teams_store.remember_user(tenant, sender["aadObjectId"], sender.get("id") or "",
                                  activity["serviceUrl"], personal)
    if activity.get("type") != "message" or sender.get("role") == "bot":
        return "ignored"
    common = {"tenant": tenant, "service_url": activity.get("serviceUrl"),
              "conversation": conv.get("id"), "kind": conv.get("conversationType") or "personal",
              "activity_id": activity.get("id"), "from": sender.get("id"),
              "aad": sender.get("aadObjectId"), "channel": activity.get("channelId")}
    rated = feedback_data(activity)
    if rated:
        payload = {**common, "op": "feedback", **rated}
    else:
        text = question_of(activity)
        if not text:
            return "ignored"
        payload = {**common, "op": "ask", "text": text[:4000]}
    if not inbound_limit("teams", tenant, sender.get("aadObjectId") or sender.get("id")):
        log.warning("Teams: too many requests from a user in tenant %s; dropped one", tenant)
        return "limited"
    _queue(install["workspace"], key, payload)
    return "queued"


def take_invoke(install, app, activity):
    """An adaptiveCard/action invoke (a feedback click): answered in the response,
    as Universal Actions want: {statusCode, type, value}."""
    rated = feedback_data(activity)
    if activity.get("name") != "adaptiveCard/action" or not rated:
        return {"statusCode": 400, "type": "application/vnd.microsoft.error",
                "value": {"code": "BadRequest", "message": "Unknown action."}}
    message = feedback(install, app, {
        "tenant": tenant_of(activity), "service_url": activity.get("serviceUrl"),
        "conversation": (activity.get("conversation") or {}).get("id"),
        "from": (activity.get("from") or {}).get("id"),
        "aad": (activity.get("from") or {}).get("aadObjectId"), **rated}, activity)
    return {"statusCode": 200, "type": "application/vnd.microsoft.activity.message",
            "value": message}


# ---- Answering (the job) ------------------------------------------------------------------
class Reply:
    """Where an answer goes: a reply to the asker's message, in its conversation."""

    def __init__(self, app, p):
        self.app, self.p = app, p
        self.id = None

    def _path(self, suffix=""):
        return f"/v3/conversations/{_conv(self.p['conversation'])}/activities{suffix}"

    def say(self, activity):
        """Reply to the question; returns the new activity's id."""
        got = connector(self.app, "POST", self.p["service_url"],
                        self._path(f"/{quote(self.p['activity_id'] or '', safe='')}"),
                        {**activity, "replyToId": self.p["activity_id"]})
        return got.get("id")

    def start(self):
        self.id = self.say({"type": "message", "text": WORKING})

    def finish(self, activity):
        if self.id:
            try:
                connector(self.app, "PUT", self.p["service_url"],
                          self._path(f"/{quote(self.id, safe='')}"),
                          {**activity, "id": self.id})
                return
            except TeamsError as e:
                log.info("Teams: updating the answer said %s; posting it instead", e.code)
        self.say(activity)


def _base_url():
    return (os.getenv("PUBLIC_URL") or "").rstrip("/") or None


def _ask(ws, asker, role, question, key=None):
    """Answer `question` as `role`, saved in the asker's conversation (`key`: the
    Teams chat or thread it continues). role: the asker's viewer (their 1:1 chat)
    or "public" (a channel or group chat: everyone's content only). Returns
    (Answer or answering.Failed, turn)."""
    idle = THREAD_IDLE if key and key[3] == "personal" else None
    cid = teams_store.thread_conversation(*key[:3], idle=idle) if key else None
    conv = conversations_store.get(ws, asker.username, cid) if cid else None
    if not conv:
        conv = conversations_store.create(ws, asker.username, access.role_of(role))
    turn = conversations.start_turn(ws, asker.username, role, conv["id"], question)
    if key:
        teams_store.remember_thread(*key[:3], turn["conversation"]["id"])
    started = time.monotonic()
    try:
        done = answering.ask(ws, asker.user(ws), role, question, surface="teams", turn=turn)
    except answering.Failed as e:
        conversations_store.finish_message(turn["message_id"], f"Something went wrong: {e.kind}",
                                           "error", max_access="restricted")
        return e, turn
    conversations_store.finish_message(
        turn["message_id"], done.answer, done.state, sources=done.sources,
        steps=[{"tool": t["tool"]} for t in done.trace], max_access=done.max_access,
        acl=done.acl, standalone_question=done.standalone,
        latency_ms=int((time.monotonic() - started) * 1000), verified=done.verified)
    return done, turn


def _rendered(done, turn, private):
    if isinstance(done, answering.Failed):
        return {"type": "message", "text": FAILED.format(kind=done.kind)}
    base = _base_url()
    open_url = f"{base}/chat/{turn['conversation']['id']}" if base and private else None
    card = answer_card(done, turn["message_id"], base, open_url, public=not private)
    return card_activity(card, (done.answer or "")[:200])


def _audit(ws, asker, p, role, done, turn, where):
    audit_store.record(ws, "teams.ask", actor=asker.username, actor_type="user",
                       target_type="question", target_id=turn["message_id"] if turn else None,
                       details={"surface": "teams", "kind": p["kind"],
                                "audience": access.role_of(role),
                                "where": where, "chars": len(p["text"]), "tenant": p["tenant"],
                                "state": "error" if isinstance(done, answering.Failed)
                                else done.state})


def personal_chat(app, p):
    """The bot's 1:1 chat with the asker: known from an earlier message, or made
    now (POST /v3/conversations). Raises TeamsError (403: the app isn't installed
    for them personally)."""
    known = teams_store.user(p["tenant"], p["aad"])
    if known and known["personal_conversation"]:
        return known["personal_conversation"], known["service_url"]
    got = connector(app, "POST", p["service_url"], "/v3/conversations", {
        "bot": {"id": f"28:{app['app_id']}"}, "members": [{"id": p["from"]}],
        "channelData": {"tenant": {"id": p["tenant"]}}, "tenantId": p["tenant"]})
    teams_store.remember_user(p["tenant"], p["aad"], p["from"], p["service_url"], got["id"])
    return got["id"], p["service_url"]


def answer_in_personal_chat(app, ws, asker, p):
    """Answer the asker as themselves in their 1:1 chat with the bot (a proactive
    message). False if Teams won't let the bot write to them."""
    try:
        conversation, service_url = personal_chat(app, p)
        intro = {"type": "message",
                 "text": f"You asked in a {'channel' if p['kind'] == 'channel' else 'group chat'}"
                         f": {p['text'][:300]}\n\nHere's the answer from everything you can see."}
        path = f"/v3/conversations/{_conv(conversation)}/activities"
        connector(app, "POST", service_url, path, intro)
        done, turn = _ask(ws, asker, asker.who(), p["text"])
        connector(app, "POST", service_url, path, _rendered(done, turn, private=True))
    except TeamsError as e:
        log.info("Teams: couldn't answer in the asker's 1:1 chat (%s)", e.code)
        return False
    _audit(ws, asker, p, asker.who(), done, turn, where="personal")
    return True


@jobs.handler(JOB, max_attempts=1)
def run(job):
    p = job.payload
    install = teams_store.get(job.workspace)
    app = app_of(install)
    if not install or not app or install["tenant_id"] != p.get("tenant"):
        return {"skipped": "not connected"}
    try:
        if p["op"] == "feedback":
            message = feedback(install, app, p)
            Reply(app, p).say({"type": "message", "text": message})
            return {"rating": p["rating"]}
        return ask(install, app, p)
    except TeamsError as e:
        log.warning("Teams: answering in workspace %s failed at Microsoft (%s)",
                    job.workspace, e.code)
        raise jobs.Retry(f"Teams said {e.code}")


def ask(install, app, p):
    ws = install["workspace"]
    reply = Reply(app, p)
    activity = {"from": {"id": p["from"], "aadObjectId": p["aad"]}, "serviceUrl": p["service_url"],
                "conversation": {"id": p["conversation"]},
                "channelData": {"tenant": {"id": p["tenant"]}}}
    asker = identify(install, app, activity)
    if asker.refusal:
        reply.say({"type": "message", "text": asker.refusal})
        return {"refused": True}
    try:
        user_limit(asker.user(ws), "chat")
    except HTTPException as e:
        reply.say({"type": "message", "text": str(e.detail)})
        return {"limited": True}
    private = p["kind"] == "personal"
    # In their 1:1 chat: what they may read. Anywhere else: everyone's content only.
    role = asker.who() if private else "public"
    reply.start()
    try:
        done, turn = _ask(ws, asker, role, p["text"],
                          key=(p["tenant"], p["conversation"], p["aad"], p["kind"]))
    except Exception as e:                  # never leave "Looking that up…" behind
        reply.finish({"type": "message", "text": FAILED.format(kind=type(e).__name__)})
        raise
    reply.finish(_rendered(done, turn, private))
    _audit(ws, asker, p, role, done, turn, where="personal" if private else p["kind"])
    # The audience rule's second half: the asker may see more than the channel.
    if not private and asker.member and restricted_found(ws, asker.who(), p["text"]):
        sent = answer_in_personal_chat(app, ws, asker, p)
        return {"answered": "channel+personal" if sent else "channel",
                **({} if sent else {"personal": "unavailable"})}
    return {"answered": "personal" if private else "channel"}


def feedback(install, app, p, activity=None):
    """👍 / 👎 on an answer: into message_feedback, like the web chat's, from the
    person who clicked (who may not be the asker, in a channel). Returns what to
    tell them."""
    ws = install["workspace"]
    clicker = identify(install, app, activity or {
        "from": {"id": p["from"], "aadObjectId": p["aad"]}, "serviceUrl": p["service_url"],
        "conversation": {"id": p["conversation"]}, "channelData": {"tenant": {"id": p["tenant"]}}})
    if clicker.refusal:
        return clicker.refusal
    m = slack_store.answer_message(ws, p.get("message_id") or "")
    if not m or m["author"] != "assistant" or not conversations.visible_to(clicker.who(), m):
        return "That answer isn't available to you any more."
    question = conversations_store.get_message(ws, m["owner"], m["parent_id"])
    snapshot = {"question": question["content"] if question else None,
                "standalone_question": m["standalone_question"], "answer": m["content"],
                "state": m["state"], "sources": m["sources"], "max_access": m["max_access"],
                "acl": access.message_clauses(m)}
    conversations_store.set_feedback(m["id"], clicker.username, ws, p["rating"], [], None,
                                     snapshot)
    audit_store.record(ws, "feedback.set", actor=clicker.username, actor_type="user",
                       target_type="message", target_id=m["id"],
                       details={"rating": p["rating"], "reasons": [], "surface": "teams"})
    return THANKS[p["rating"]]


# ---- The Teams app package -------------------------------------------------------------------
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "teams")
PLACEHOLDER_HOST = "YOUR-KNOWLEDGE-HOST"
PLACEHOLDER_BOT = "00000000-0000-0000-0000-000000000000"


def messaging_path(install):
    """Where Teams sends a workspace's activities: ours for the central bot, the
    bot's own (with its key) for a customer's."""
    if install and install["mode"] == "custom" and install["app_key"]:
        return f"/api/teams/apps/{install['app_key']}/messages"
    return "/api/teams/messages"


def app_package(base_url, app_id):
    """teams/ as the zip Teams uploads: manifest.json with this server's host and
    the bot's App ID filled in, and the two icons."""
    import io
    import json
    import zipfile
    with open(os.path.join(APP_DIR, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    host = urlsplit(base_url).hostname or PLACEHOLDER_HOST
    text = json.dumps(manifest, indent=2).replace(PLACEHOLDER_BOT, app_id)
    text = text.replace(f"https://{PLACEHOLDER_HOST}", base_url).replace(PLACEHOLDER_HOST, host)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", text)
        for icon in ("color.png", "outline.png"):
            z.write(os.path.join(APP_DIR, icon), icon)
    return out.getvalue()
