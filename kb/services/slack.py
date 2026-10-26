"""
The Slack app (platform.md PL-22, chat.md C26): people ask in Slack and get the
same checked, cited answers as in the web chat, through answering.ask().

HOW IT'S ASKED. A DM to the bot, "@Knowledge ..." in a channel (answered in a
thread), /ask (only the asker sees the answer), and the "Ask about this thread"
message shortcut (the thread goes along as context, spotlighted as untrusted
data: untrusted.spotlight). Slack wants an answer within 3 seconds, so the
request is only checked and queued (kb/routers/slack.py); a job (kb/jobs.py,
kind "slack") posts "Looking that up…", answers, and updates that message.

WHO ASKS. The Slack user's email (users.info, scope users:read.email) is the
member they are in the workspace that installed the app; their role, and
whether they're turned off, are looked up at every question, like a session's.
People who aren't members are refused (the default) or answered as public, by
the workspace's setting. Turned-off members, bots and people from another
organisation (Slack Connect) are never answered.

THE AUDIENCE RULE, the way this could go wrong. Everyone in a channel reads
what's posted there, so an answer posted anywhere but the asker's own DM with
the bot is written with PUBLIC access, whatever the asker's role. When the
asker's role could have found more (a search as them finds restricted
passages), they're told so privately ("I found restricted sources; I've sent
you a DM") and answered again in that DM, as themselves. A channel shared with
another organisation gets no answer in it at all: the asker is answered in a DM.

THE ANSWER. Block Kit (answer_blocks): the answer in Slack's markup, its [S#]
markers as numbered links to the source, or to the app's passage view
(/passages/<chunk id>, role-checked) when the source has no link of its own; a
context block listing the sources; and 👍 / 👎 buttons, which land in
message_feedback like the web chat's. Every answer is saved in a conversation
of the asker's (follow-ups in a Slack thread continue it) and logged in the
question log with surface "slack".

AUTO-ANSWER in help channels an admin picks: off by default, and it posts only
when a verified answer matches (PL-27, not built yet: verified_answer()) or the
answer is confident (confident()). It needs the extra channels:history scope.

Slack API facts used here (checked 2026-10-27 on docs.slack.dev):
  signing:   https://docs.slack.dev/authentication/verifying-requests-from-slack
  events:    https://docs.slack.dev/apis/events-api/ (3 s, retries, x-slack-retry-num)
  commands:  https://docs.slack.dev/interactivity/implementing-slash-commands
  clicks:    https://docs.slack.dev/interactivity/handling-user-interaction
             (response_url: up to 5 times within 30 minutes)
  OAuth v2:  https://docs.slack.dev/authentication/installing-with-oauth
  users.info https://docs.slack.dev/reference/methods/users.info (Tier 4)
"""

import email.utils
import hashlib
import hmac
import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import requests
from fastapi import HTTPException

import audit_store
import conversations_store
import members_store
import slack_store
from kb import jobs, state
from kb.rate_limit import user_limit
from kb.services import answering, conversations, retrieval
from untrusted import spotlight

log = logging.getLogger("uvicorn.error")

API = "https://slack.com/api/"
AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
# The bot scopes the app asks for: the same list as slack/manifest.yml.
SCOPES = ("app_mentions:read", "chat:write", "commands", "im:history", "im:write",
          "users:read", "users:read.email")
SIGNATURE_WINDOW = 5 * 60          # a request signed longer ago than this is a replay
CACHE_SECONDS = 10 * 60            # users.info and conversations.open results
TIMEOUT = 10
MAX_WAIT = 30                      # a longer Retry-After isn't waited out
JOB = "slack"
SHORTCUT = "kb_ask_thread"         # the message shortcut's callback_id
FEEDBACK = {"kb_feedback_up": 1, "kb_feedback_down": -1}
THREAD_CHARS = 6000                # of a thread sent along with "Ask about this thread"
SECTION_CHARS = 2900               # a section block's text is at most 3000
MAX_SOURCES = 10                   # a context block holds 10 elements

WORKING = "Looking that up…"
UNKNOWN = ("I can only answer people who have an account in the Knowledge assistant, and "
           "I couldn't match your Slack email to one. Ask your admin to invite you.")
DISABLED = ("Your Knowledge assistant account is turned off, so I can't answer you. Ask "
            "your admin if that's a mistake.")
RESTRICTED = "I found restricted sources; I've sent you a DM."
SHARED = ("This channel is shared with another organisation, so I've answered you in a DM "
          "instead.")
FAILED = "Sorry, I couldn't answer that just now ({kind}). Please try again in a moment."
SHORTCUT_QUESTION = "Answer the question asked in this Slack thread, from our documents."
COMMAND_HELP = "Type a question after /ask, for example: /ask What is our refund policy?"
THANKS = {1: "Thanks, glad it helped.", -1: "Thanks for telling us. HR and admins review "
                                            "answers marked not helpful."}

# Swapped by tests (tests/slack_fixtures.py) so nothing reaches Slack.
send = requests.post
sleep = time.sleep


# ---- Requests from Slack ----------------------------------------------------------------
def signature_ok(signing_secret, timestamp, body, signature, now=None):
    """Slack's request signature: "v0=" + HMAC-SHA256(signing secret,
    "v0:<timestamp>:<raw body>"), compared in constant time, and the timestamp
    within SIGNATURE_WINDOW of now, so a recorded request can't be sent again."""
    if not (signing_secret and timestamp and signature):
        return False
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    if abs((now or time.time()) - sent) > SIGNATURE_WINDOW:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def central_app():
    """The app run by this server's host (SLACK_CLIENT_ID, SLACK_CLIENT_SECRET,
    SLACK_SIGNING_SECRET), or None if it isn't set up."""
    app = {k: os.getenv(f"SLACK_{k.upper()}", "").strip()
           for k in ("client_id", "client_secret", "signing_secret")}
    return app if all(app.values()) else None


def app_of(install):
    """{client_id, client_secret, signing_secret} of the app a workspace uses, or None."""
    if not install or install["mode"] == "central":
        return central_app()
    sealed = slack_store.app_secrets(install["workspace"])
    if not sealed or not install["client_id"]:
        return None
    return {"client_id": install["client_id"], **sealed}


# ---- The Web API --------------------------------------------------------------------------
class SlackError(Exception):
    """A Slack call failed: `code` is Slack's error ("not_in_channel", ...) or ours
    ("ratelimited", "network"). Never carries a token."""

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


def _post(url, **kwargs):
    """One POST, waiting out a 429's Retry-After (twice at most, MAX_WAIT each)."""
    for attempt in range(3):
        try:
            r = send(url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as e:
            raise SlackError("network") from e
        if r.status_code != 429:
            return r
        wait = _retry_after(r)
        if attempt == 2 or wait > MAX_WAIT:
            raise SlackError("ratelimited")
        log.info("Slack: rate limited, waiting %.0f s", wait)
        sleep(wait)


def call(method, token=None, **args):
    """A Web API method -> its JSON. Arguments are sent form-encoded (lists and
    dicts as JSON, as Slack takes blocks). Raises SlackError."""
    data = {k: json.dumps(v) if isinstance(v, (list, dict)) else v
            for k, v in args.items() if v is not None}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = _post(API + method, data=data, headers=headers)
    try:
        body = r.json()
    except ValueError:
        raise SlackError(f"http_{r.status_code}")
    if not body.get("ok"):
        raise SlackError(str(body.get("error") or f"http_{r.status_code}"))
    return body


def respond(response_url, message):
    """Post to a command's or a click's response_url (Slack's own webhook only:
    it came in a signed request, but nothing else is ever called with it)."""
    parts = urlsplit(response_url or "")
    if parts.scheme != "https" or parts.hostname != "hooks.slack.com":
        raise SlackError("bad_response_url")
    r = _post(response_url, json=message)
    if r.status_code >= 400:
        raise SlackError(f"http_{r.status_code}")


# ---- Installing ---------------------------------------------------------------------------
def authorize_url(client_id, redirect_uri, state_token):
    return AUTHORIZE_URL + "?" + urlencode({"client_id": client_id, "scope": ",".join(SCOPES),
                                            "redirect_uri": redirect_uri,
                                            "state": state_token})


class InstallError(Exception):
    """Installing failed: `code` says why, for the Integrations screen."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def finish_install(ws, code, redirect_uri, user):
    """Swap the code from Slack's redirect for the bot token (oauth.v2.access) and
    keep it, sealed. Returns the install. Raises InstallError."""
    install = slack_store.get(ws)
    app = app_of(install)
    if not app:
        raise InstallError("not_configured")
    try:
        got = call("oauth.v2.access", code=code, client_id=app["client_id"],
                   client_secret=app["client_secret"], redirect_uri=redirect_uri)
    except SlackError as e:
        log.warning("Slack: couldn't finish installing in workspace %s (%s)", ws, e.code)
        raise InstallError("connect_failed")
    team = got.get("team") or {}
    if got.get("is_enterprise_install") or not team.get("id"):
        raise InstallError("org_install")         # one Slack workspace, not a whole org
    if got.get("token_type") != "bot" or not got.get("access_token"):
        raise InstallError("connect_failed")
    try:
        slack_store.installed(ws, team["id"], team.get("name"), got.get("app_id"),
                              got.get("bot_user_id"), got["access_token"], got.get("scope"),
                              user)
    except slack_store.TeamTaken:
        raise InstallError("team_taken")
    _forget_cached(team["id"])
    return slack_store.get(ws)


def uninstall(ws):
    """Revoke the bot token at Slack (best effort) and forget it here."""
    token = slack_store.bot_token(ws)
    install = slack_store.get(ws)
    if token:
        try:
            call("auth.revoke", token)
        except SlackError as e:
            log.info("Slack: revoking workspace %s's token said %s", ws, e.code)
    slack_store.uninstalled(ws)
    if install and install["team_id"]:
        _forget_cached(install["team_id"])


def _forget_cached(team_id):
    with state.slack_lock:
        for cache in (state.slack_users, state.slack_dms):
            for key in [k for k in cache if k[0] == team_id]:
                del cache[key]


# ---- Who is asking ------------------------------------------------------------------------
@dataclass
class Asker:
    team_id: str
    slack_user: str
    username: str = ""             # the member, or "slack:<team>:<user>" answered as public
    role: str = "public"           # the access role they may ask as: hr / public
    member: bool = False
    refusal: str | None = None     # set: don't answer, say this
    ignore: bool = False           # a bot, a deactivated account: say nothing

    def user(self, ws):
        return {"username": self.username, "workspace": ws}


def _cached(cache, key, fetch):
    now = time.time()
    with state.slack_lock:
        hit = cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fetch()
    with state.slack_lock:
        if len(cache) > 10_000:
            cache.clear()
        cache[key] = (now + CACHE_SECONDS, value)
    return value


def profile(team_id, token, slack_user):
    """What users.info says of a Slack user that matters here (cached)."""
    def fetch():
        info = call("users.info", token, user=slack_user)["user"]
        return {"team_id": info.get("team_id"), "deleted": bool(info.get("deleted")),
                "is_bot": bool(info.get("is_bot")) or slack_user == "USLACKBOT",
                "email": ((info.get("profile") or {}).get("email") or "").strip().lower()}
    return _cached(state.slack_users, (team_id, slack_user), fetch)


def identify(install, token, slack_user):
    """The Asker a Slack user is in the workspace that installed the app."""
    ws, team = install["workspace"], install["team_id"]
    asker = Asker(team, slack_user)
    p = profile(team, token, slack_user)
    if p["deleted"] or p["is_bot"]:
        asker.ignore = True
        return asker
    if p["team_id"] and p["team_id"] != team:     # Slack Connect: another organisation
        asker.refusal = UNKNOWN
        return asker
    member = members_store.check_group_admin(members_store.get(p["email"])) if p["email"] else None
    if member and member["workspace"] == ws:
        if member["disabled"]:
            asker.refusal = DISABLED
            return asker
        asker.username, asker.member = member["username"], True
        asker.role = "hr" if member["role"] in members_store.STAFF_ROLES else "public"
        return asker
    if install["unknown_users"] == "public":
        asker.username = f"slack:{team}:{slack_user}"
        return asker
    asker.refusal = UNKNOWN
    return asker


def dm_channel(team_id, token, slack_user):
    """The bot's DM with a Slack user (conversations.open, scope im:write; cached)."""
    return _cached(state.slack_dms, (team_id, slack_user),
                   lambda: call("conversations.open", token, users=slack_user)["channel"]["id"])


def answers_privately(kind, channel, team_id, token, slack_user):
    """True only where the asker alone reads the answer: their own DM with the bot.
    Everywhere else (a channel, a group DM, a DM with someone else, a thread) the
    answer is public-only. A message.im event is the bot's DM by definition;
    anything else in a D... channel is compared with it."""
    if kind == "dm":
        return True
    if not (channel or "").startswith("D"):
        return False
    try:
        return channel == dm_channel(team_id, token, slack_user)
    except SlackError:
        return False


def restricted_found(ws, question):
    """True if a search as HR / Admin finds restricted passages for the question:
    the asker could have been told more than the channel may read."""
    try:
        return any(p.get("access") != "public"
                   for p in retrieval.search(ws, "hr", question, k=5))
    except Exception:          # refused by the question rules, no index yet, ...
        return False


# ---- Slack's markup -----------------------------------------------------------------------
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
_MARKER = re.compile(r"[ \t]*\[S(\d{1,3})\]")


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _link_label(text):
    return _escape(text).replace("|", "¦")


def _url(url):
    """A link's address as Slack takes it: nothing in it can end the <url|label>."""
    return url.replace("<", "%3C").replace(">", "%3E").replace("|", "%7C").replace(" ", "%20")


def source_link(source, base_url):
    """Where a source's number links to: the source itself, or the app's passage
    view (sign-in and the reader's role are checked there)."""
    if source.get("url"):
        return _url(source["url"])
    if source.get("chunk_id") and base_url:
        return _url(f"{base_url}/passages/{source['chunk_id']}")
    return None


def mrkdwn(answer, sources=(), base_url=None):
    """An answer's markdown as Slack mrkdwn: **bold** -> *bold*, *it* -> _it_,
    [text](url) -> <url|text>, headings bold, bullets •, and each [S#] a numbered
    link to its source ([S#] with no such source is dropped). Slack's control
    characters (&, <, >) are escaped first, so the text can't make links or
    mentions of its own."""
    numbered = {s["n"]: s for s in sources if isinstance(s.get("n"), int)}
    links = []

    def keep(markup):
        links.append(markup)
        return f"\x00{len(links) - 1}\x00"

    def cite(m):
        source = numbered.get(int(m.group(1)))
        if not source:
            return ""
        url = source_link(source, base_url)
        n = m.group(1)
        return " " + keep(f"<{url}|[{n}]>" if url else f"[{n}]")

    text = _MARKER.sub(cite, answer or "")
    text = _LINK.sub(lambda m: keep(f"<{_url(m.group(2))}|{_link_label(m.group(1))}>"), text)
    text = _escape(text)
    text = _BOLD.sub(lambda m: "\x01" + (m.group(1) or m.group(2)) + "\x01", text)
    text = _ITALIC.sub(lambda m: "_" + m.group(1) + "_", text)
    text = _HEADING.sub(lambda m: "\x01" + m.group(1) + "\x01", text)
    text = _BULLET.sub(lambda m: m.group(1) + "• ", text)
    text = text.replace("\x01", "*")
    return re.sub("\x00(\\d+)\x00", lambda m: links[int(m.group(1))], text).strip()


def plain(answer):
    """The answer as plain text: the notification and screen-reader fallback."""
    return _MARKER.sub("", answer or "").strip()


def _pieces(text, limit=SECTION_CHARS):
    """Text cut into pieces of at most `limit`, at a line break where there is one."""
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    return out + ([text] if text else [])


def answer_blocks(done, message_id, base_url=None, open_url=None):
    """(fallback text, Block Kit blocks) for an answer: its text, its sources,
    and the feedback buttons (their value: the answer's message id)."""
    text = mrkdwn(done.answer, done.sources, base_url) or "(no answer)"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": piece}}
              for piece in _pieces(text)][:40]
    if done.sources:
        items = []
        for i, s in enumerate(done.sources, 1):
            n = s.get("n") if isinstance(s.get("n"), int) else None
            url = source_link(s, base_url)
            title = _link_label(" ".join(str(s.get("title") or "Untitled").split())[:150])
            name = f"<{url}|{title}>" if url else title
            provider = s.get("provider") or ""
            if s.get("kind") == "general_knowledge":
                provider = "General knowledge · " + provider
            items.append(f"*[{n}]* {name}" if n else name)
            if provider:
                items[-1] += f" · {_escape(provider)}"
        if len(items) > MAX_SOURCES:
            items = items[:MAX_SOURCES - 1] + [f"and {len(items) - MAX_SOURCES + 1} more"]
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": t} for t in items]})
    buttons = []
    if message_id and done.state != "error":
        buttons += [{"type": "button", "action_id": "kb_feedback_up", "value": message_id,
                     "text": {"type": "plain_text", "text": "👍 Helpful", "emoji": True}},
                    {"type": "button", "action_id": "kb_feedback_down", "value": message_id,
                     "text": {"type": "plain_text", "text": "👎 Not helpful", "emoji": True}}]
    if open_url:
        buttons.append({"type": "button", "action_id": "kb_open", "url": open_url,
                        "text": {"type": "plain_text", "text": "Open in the app"}})
    if buttons:
        blocks.append({"type": "actions", "block_id": "kb_feedback", "elements": buttons})
    return plain(done.answer)[:3000] or "(no answer)", blocks


def question_of(text, bot_user_id=None):
    """A Slack message's text as a question: the bot's own mention removed, links
    and channel names as their words, Slack's escapes undone."""
    text = text or ""
    if bot_user_id:
        text = re.sub(rf"<@{re.escape(bot_user_id)}(\|[^>]*)?>", " ", text)
    text = re.sub(r"<#[A-Z0-9]+\|([^>]*)>", r"#\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 (\1)", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    return " ".join(html.unescape(text).split())


# ---- Taking requests in (kb/routers/slack.py, within Slack's 3 seconds) -------------------
def _queue(ws, key, payload):
    return jobs.enqueue(JOB, workspace=ws, payload=payload, lane=f"{JOB}:{ws}:{key}",
                        max_attempts=1)


def take_event(install, envelope):
    """An event_callback: queue what needs an answer. Returns what was done (for
    logs and tests): "queued", "duplicate", "ignored" or "uninstalled"."""
    event = envelope.get("event") or {}
    team = envelope.get("team_id") or event.get("team")
    event_id = envelope.get("event_id") or f"{team}:{event.get('event_ts')}"
    if not slack_store.first_time(f"event:{event_id}", team):
        return "duplicate"
    kind = event.get("type")
    ws = install["workspace"]
    if kind in ("app_uninstalled", "tokens_revoked"):
        if kind == "tokens_revoked" and not (event.get("tokens") or {}).get("bot"):
            return "ignored"
        slack_store.uninstalled(ws, "revoked")
        _forget_cached(install["team_id"])
        audit_store.record(ws, "slack.uninstall", actor="slack", actor_type="system",
                           target_type="slack_team", target_id=install["team_id"],
                           details={"by": "slack", "event": kind})
        return "uninstalled"
    user, bot = event.get("user"), install["bot_user_id"]
    if not user or user == bot or event.get("bot_id") or event.get("subtype"):
        return "ignored"
    common = {"workspace": ws, "team": install["team_id"], "user": user,
              "channel": event.get("channel"), "ts": event.get("ts"),
              "thread_ts": event.get("thread_ts"),
              "ext_shared": bool(envelope.get("is_ext_shared_channel"))}
    if kind == "app_mention":
        payload = {**common, "op": "ask", "kind": "mention",
                   "text": question_of(event.get("text"), bot)}
    elif kind == "message" and event.get("channel_type") == "im":
        payload = {**common, "op": "ask", "kind": "dm", "text": question_of(event.get("text"), bot)}
    elif (kind == "message" and event.get("channel_type") in ("channel", "group")
          and install["auto_answer"] and event.get("channel") in install["auto_answer_channels"]
          and not event.get("thread_ts") and f"<@{bot}" not in (event.get("text") or "")):
        payload = {**common, "op": "ask", "kind": "auto", "text": question_of(event.get("text"))}
    else:
        return "ignored"
    if not payload["text"] or not _within_limits(install, user):
        return "ignored"
    _queue(ws, event_id, payload)
    return "queued"


def _within_limits(install, user):
    from kb.rate_limit import slack_limit
    if slack_limit(install["team_id"], user):
        return True
    log.warning("Slack: too many requests from a user in team %s; dropped one",
                install["team_id"])
    return False


def take_command(install, form):
    """/ask: the immediate (ephemeral) reply; the answer follows on response_url."""
    text = question_of(form.get("text"))
    if not text:
        return {"response_type": "ephemeral", "text": COMMAND_HELP}
    key = form.get("trigger_id") or f"{form.get('user_id')}:{time.time()}"
    if not slack_store.first_time(f"command:{key}", install["team_id"]):
        return {"response_type": "ephemeral", "text": WORKING}
    if not _within_limits(install, form.get("user_id")):
        return {"response_type": "ephemeral",
                "text": "You're asking faster than I can answer. Try again in a minute."}
    _queue(install["workspace"], f"command:{key}", {
        "op": "ask", "kind": "command", "workspace": install["workspace"],
        "team": install["team_id"], "user": form.get("user_id"),
        "channel": form.get("channel_id"), "text": text,
        "response_url": form.get("response_url")})
    return {"response_type": "ephemeral", "text": WORKING}


def take_interaction(install, payload):
    """A click or a shortcut: queue it. Returns "queued" or "ignored"."""
    kind = payload.get("type")
    user = (payload.get("user") or {}).get("id")
    common = {"workspace": install["workspace"], "team": install["team_id"], "user": user,
              "response_url": payload.get("response_url")}
    if kind == "message_action" and payload.get("callback_id") == SHORTCUT:
        message = payload.get("message") or {}
        key = payload.get("trigger_id") or f"{user}:{message.get('ts')}"
        if not slack_store.first_time(f"shortcut:{key}", install["team_id"]) or \
                not _within_limits(install, user):
            return "ignored"
        _queue(install["workspace"], f"shortcut:{key}", {
            **common, "op": "ask", "kind": "shortcut", "text": SHORTCUT_QUESTION,
            "channel": (payload.get("channel") or {}).get("id"),
            "ts": message.get("ts"), "thread_ts": message.get("thread_ts") or message.get("ts"),
            "message_text": message.get("text") or ""})
        return "queued"
    if kind == "block_actions":
        action = next((a for a in payload.get("actions") or []
                       if a.get("action_id") in FEEDBACK), None)
        if not action:
            return "ignored"
        container = payload.get("container") or {}
        _queue(install["workspace"], f"feedback:{user}:{action.get('value')}:{time.time()}", {
            **common, "op": "feedback", "message_id": action.get("value"),
            "rating": FEEDBACK[action["action_id"]],
            "channel": container.get("channel_id") or (payload.get("channel") or {}).get("id")})
        return "queued"
    return "ignored"


# ---- Answering (the job) ------------------------------------------------------------------
class Reply:
    """Where the answer to one question goes, by how it was asked."""

    def __init__(self, token, p):
        self.token, self.p = token, p
        self.kind, self.channel = p["kind"], p.get("channel")
        # A mention is answered in its thread; a DM where it was asked (a thread
        # if it was asked in one); auto-answer under the message.
        self.thread = (p.get("thread_ts") or p.get("ts")) if self.kind in ("mention", "auto") \
            else p.get("thread_ts")
        self.ts = None

    def start(self):
        """Say it's being looked up (a command already said so when it was taken in)."""
        if self.kind in ("dm", "mention"):
            self.ts = call("chat.postMessage", self.token, channel=self.channel,
                           thread_ts=self.thread, text=WORKING)["ts"]
        elif self.kind == "shortcut":
            respond(self.p["response_url"], {"response_type": "ephemeral", "text": WORKING})

    def finish(self, text, blocks=None):
        if self.kind in ("command", "shortcut"):
            respond(self.p["response_url"], {"response_type": "ephemeral",
                                             "replace_original": True, "text": text,
                                             **({"blocks": blocks} if blocks else {})})
        elif self.ts:
            call("chat.update", self.token, channel=self.channel, ts=self.ts, text=text,
                 blocks=blocks or [])
        else:
            call("chat.postMessage", self.token, channel=self.channel, thread_ts=self.thread,
                 text=text, blocks=blocks, unfurl_links=False, unfurl_media=False)

    def private(self, text):
        """A note only the asker sees."""
        if self.kind in ("command", "shortcut"):
            respond(self.p["response_url"], {"response_type": "ephemeral",
                                             "replace_original": False, "text": text})
        elif self.kind == "dm":
            call("chat.postMessage", self.token, channel=self.channel, thread_ts=self.thread,
                 text=text)
        else:
            call("chat.postEphemeral", self.token, channel=self.channel, user=self.p["user"],
                 thread_ts=self.thread, text=text)


def _base_url():
    return (os.getenv("PUBLIC_URL") or "").rstrip("/") or None


def _thread_history(token, p):
    """'Ask about this thread': the thread as context for the model, spotlighted
    as untrusted data (its text is written by anyone in the channel). The whole
    thread when the app may read it (conversations.replies, channels:history),
    else the one message the shortcut was used on."""
    texts = []
    try:
        got = call("conversations.replies", token, channel=p["channel"], ts=p["thread_ts"],
                   limit=50)
        texts = [m.get("text") or "" for m in got.get("messages") or []]
    except SlackError as e:
        log.info("Slack: reading a thread for the shortcut said %s; using its message", e.code)
    if not texts:
        texts = [p.get("message_text") or ""]
    thread = "\n\n".join(question_of(t) for t in texts if t.strip())[-THREAD_CHARS:]
    if not thread:
        return []
    return [{"role": "user", "content": "Here is a Slack thread I want to ask about:\n"
             + spotlight(thread, "slack_thread")},
            {"role": "assistant", "content": "I've read the thread. What would you like to "
                                             "know about it?"}]


def _ask(ws, asker, role, question, key=None, history=None):
    """Answer `question` as `role`, saved in the asker's conversation (`key`: the
    Slack thread it continues). Returns (Answer or answering.Failed, turn)."""
    cid = slack_store.thread_conversation(*key) if key else None
    conv = conversations_store.get(ws, asker.username, cid) if cid else None
    if not conv:
        conv = conversations_store.create(ws, asker.username, role)
    turn = conversations.start_turn(ws, asker.username, role, conv["id"], question)
    if key:
        slack_store.remember_thread(*key, turn["conversation"]["id"])
    if history:
        turn = {**turn, "history": history + turn["history"]}
    started = time.monotonic()
    try:
        done = answering.ask(ws, asker.user(ws), role, question, surface="slack", turn=turn)
    except answering.Failed as e:
        conversations_store.finish_message(turn["message_id"], f"Something went wrong: {e.kind}",
                                           "error", max_access="restricted")
        return e, turn
    conversations_store.finish_message(
        turn["message_id"], done.answer, done.state, sources=done.sources,
        steps=[{"tool": t["tool"]} for t in done.trace], max_access=done.max_access,
        standalone_question=done.standalone,
        latency_ms=int((time.monotonic() - started) * 1000))
    return done, turn


def _deliver(reply, done, turn, private):
    base = _base_url()
    if isinstance(done, answering.Failed):
        reply.finish(FAILED.format(kind=done.kind))
        return
    open_url = f"{base}/chat/{turn['conversation']['id']}" if base and private else None
    text, blocks = answer_blocks(done, turn["message_id"], base, open_url)
    reply.finish(text, blocks)


def _audit(ws, asker, p, role, done, turn, where=None):
    audit_store.record(ws, "slack.ask", actor=asker.username, actor_type="user",
                       target_type="question", target_id=turn["message_id"] if turn else None,
                       details={"surface": "slack", "kind": p["kind"], "audience": role,
                                "where": where or ("dm" if role == asker.role else "channel"),
                                "chars": len(p["text"]), "team": p["team"],
                                "state": "error" if isinstance(done, answering.Failed)
                                else done.state})


def answer_in_dm(token, ws, asker, p, note=None):
    """Answer the asker in their DM with the bot, as themselves."""
    channel = dm_channel(p["team"], token, p["user"])
    where = f" in <#{p['channel']}>" if p.get("channel") and p["channel"] != channel else ""
    dm = Reply(token, {**p, "kind": "dm", "channel": channel, "thread_ts": None})
    dm.private(note or f"You asked{where}: {_escape(p['text'][:300])}")
    done, turn = _ask(ws, asker, asker.role, p["text"])
    _deliver(dm, done, turn, private=True)
    _audit(ws, asker, p, asker.role, done, turn, where="dm")


def verified_answer(ws, question):
    """An admin-verified answer matching the question (PL-27): not built yet, so
    there is none. Auto-answer posts one when it's there."""
    return None


def confident(done):
    """Confident enough to post unasked: answered in full, from cited documents."""
    return (not isinstance(done, answering.Failed) and done.state == "answered"
            and any(isinstance(s.get("n"), int) for s in done.sources))


@jobs.handler(JOB, max_attempts=1)
def run(job):
    p = job.payload
    ws = job.workspace
    install = slack_store.get(ws)
    token = slack_store.bot_token(ws)
    if not install or install["status"] != "installed" or install["team_id"] != p.get("team") \
            or not token:
        return {"skipped": "not installed"}
    try:
        if p["op"] == "feedback":
            return feedback(install, token, p)
        return ask(install, token, p)
    except SlackError as e:
        log.warning("Slack: answering in workspace %s failed at Slack (%s)", ws, e.code)
        raise jobs.Retry(f"Slack said {e.code}")


def ask(install, token, p):
    ws = install["workspace"]
    reply = Reply(token, p)
    asker = identify(install, token, p["user"])
    auto = p["kind"] == "auto"
    if asker.ignore or (auto and (asker.refusal or not asker.member)):
        return {"skipped": "not answered"}
    if asker.refusal:
        reply.private(asker.refusal)
        return {"refused": True}
    try:
        user_limit(asker.user(ws), "chat")
    except HTTPException as e:
        if not auto:
            reply.private(str(e.detail))
        return {"limited": True}

    private = answers_privately(p["kind"], p.get("channel"), p["team"], token, p["user"])
    if p.get("ext_shared") and not private:
        if auto:
            return {"skipped": "shared channel"}
        reply.private(SHARED)
        answer_in_dm(token, ws, asker, p)
        return {"answered": "dm"}
    role = asker.role if private else "public"

    if auto:
        if verified_answer(ws, p["text"]) is None:
            done, turn = _ask(ws, asker, role, p["text"])
            if not confident(done):
                # Not posted: it isn't kept in their chats either (the log has it).
                conversations_store.delete(ws, asker.username, turn["conversation"]["id"])
                return {"skipped": "not confident"}
            _deliver(reply, done, turn, private=False)
            _audit(ws, asker, p, role, done, turn)
        return {"answered": "auto"}

    reply.start()
    try:
        history = _thread_history(token, p) if p["kind"] == "shortcut" else None
        key = ((p["team"], p["channel"], p.get("thread_ts") or p.get("ts"), p["user"])
               if p["kind"] in ("dm", "mention") and p.get("ts") else None)
        done, turn = _ask(ws, asker, role, p["text"], key=key, history=history)
    except Exception as e:                  # never leave "Looking that up…" behind
        reply.finish(FAILED.format(kind=type(e).__name__))
        raise
    _deliver(reply, done, turn, private)
    _audit(ws, asker, p, role, done, turn)
    # The audience rule's second half: the asker may see more than the channel.
    if not private and asker.role != "public" and restricted_found(ws, p["text"]):
        reply.private(RESTRICTED)
        answer_in_dm(token, ws, asker, p)
        return {"answered": "channel+dm"}
    return {"answered": "dm" if private else "channel"}


def feedback(install, token, p):
    """👍 / 👎 on an answer: into message_feedback, like the web chat's, from the
    person who clicked (who may not be the asker, in a channel)."""
    ws = install["workspace"]
    clicker = identify(install, token, p["user"])

    def say(text):
        if p.get("response_url"):
            respond(p["response_url"], {"response_type": "ephemeral", "replace_original": False,
                                        "text": text})
    if clicker.ignore:
        return {"skipped": True}
    if clicker.refusal:
        say(clicker.refusal)
        return {"refused": True}
    m = slack_store.answer_message(ws, p.get("message_id") or "")
    if not m or m["author"] != "assistant" or not conversations.visible_to(clicker.role, m):
        say("That answer isn't available to you any more.")
        return {"refused": True}
    question = conversations_store.get_message(ws, m["owner"], m["parent_id"])
    snapshot = {"question": question["content"] if question else None,
                "standalone_question": m["standalone_question"], "answer": m["content"],
                "state": m["state"], "sources": m["sources"], "max_access": m["max_access"]}
    conversations_store.set_feedback(m["id"], clicker.username, ws, p["rating"], [], None,
                                     snapshot)
    audit_store.record(ws, "feedback.set", actor=clicker.username, actor_type="user",
                       target_type="message", target_id=m["id"],
                       details={"rating": p["rating"], "reasons": [], "surface": "slack"})
    say(THANKS[p["rating"]])
    return {"rating": p["rating"]}


# ---- The manifest -------------------------------------------------------------------------
MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "slack", "manifest.yml")
PLACEHOLDER = "https://YOUR-KNOWLEDGE-HOST"


def request_path(install):
    """Where Slack sends a workspace's requests: ours for the central app, the
    app's own (with its key) for a customer's."""
    if install and install["mode"] == "custom" and install["app_key"]:
        return f"/api/slack/apps/{install['app_key']}"
    return "/api/slack"


def manifest(base_url, install=None):
    """slack/manifest.yml with this server's addresses filled in."""
    with open(MANIFEST, encoding="utf-8") as f:
        text = f.read()
    text = text.replace(f"{PLACEHOLDER}/api/slack/oauth/", f"{base_url}/api/slack/oauth/")
    return text.replace(f"{PLACEHOLDER}/api/slack/", f"{base_url}{request_path(install)}/")
