"""
Recorded HTTP for the Slack app's tests: kb/services/slack.py's `send` is swapped
for FakeSlack, which records every request and answers slack.com's Web API from
tests/fixtures/slack/web_api.json (Slack's own example answers), and Slack's
response_url webhooks (hooks.slack.com) with 200. Nothing reaches the network:
a request to anywhere else fails the test.

    slack.people["U1"] = "hr@acme.example"        # users.info's profile.email
    slack.fail["conversations.open"] = "missing_scope"
    slack.limited["chat.postMessage"] = [2]        # a 429 with Retry-After: 2, then ok
    slack.calls_to("chat.update")                  # [{"method", "data", "json", ...}]

Also: requests signed as Slack signs them (signed()), and the events, commands
and clicks Slack sends.
"""

import hashlib
import hmac
import itertools
import json
import pathlib
import time
from urllib.parse import urlencode, urlsplit

from connector_fixtures import response

RECORDED = json.loads((pathlib.Path(__file__).parent / "fixtures" / "slack" /
                       "web_api.json").read_text())
SIGNING_SECRET = "testsigningsecret0"
CLIENT_ID = "1111111111.2222222222"
CLIENT_SECRET = "testclientsecret00"
TEAM = "T0ACME"
BOT = "UBOT"


def _fill(value, fields):
    if isinstance(value, str):
        for k, v in fields.items():
            value = value.replace("{" + k + "}", str(v))
        return value
    if isinstance(value, dict):
        return {k: _fill(v, fields) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, fields) for v in value]
    return value


class FakeSlack:
    def __init__(self, monkeypatch):
        from kb.services import slack
        self.calls = []
        self.people = {}              # Slack user -> email
        self.teams = {}               # Slack user -> their team, when not TEAM
        self.bots = set()
        self.fail = {}                # method -> Slack error
        self.limited = {}             # method -> [Retry-After seconds, ...] to answer first
        self.answers = {}             # method -> an answer instead of the recorded one
        self.slept = []
        self._ts = itertools.count(1)
        monkeypatch.setattr(slack, "send", self.send)
        monkeypatch.setattr(slack, "sleep", self.slept.append)

    def send(self, url, data=None, json=None, headers=None, timeout=None):
        parts = urlsplit(url)
        method = parts.path.removeprefix("/api/") if parts.hostname == "slack.com" else url
        call = {"method": method, "data": dict(data or {}), "json": json,
                "headers": dict(headers or {})}
        self.calls.append(call)
        if parts.hostname == "hooks.slack.com":
            return response(200, text="ok")
        if parts.hostname != "slack.com" or not parts.path.startswith("/api/"):
            raise AssertionError(f"the Slack app called {url}")
        if self.limited.get(method):
            return response(429, {"ok": False, "error": "ratelimited"},
                            headers={"Retry-After": str(self.limited[method].pop(0))})
        if method in self.fail:
            return response(200, {"ok": False, "error": self.fail[method]})
        if method not in RECORDED and method not in self.answers:
            raise AssertionError(f"no recorded answer for {method}")
        args = call["data"]
        user = args.get("user") or args.get("users") or ""
        body = _fill(self.answers.get(method) or RECORDED[method], {"channel": args.get("channel", ""), "user": user,
                                        "ts": f"1700000000.{next(self._ts):06d}"})
        if method == "users.info":
            body["user"]["profile"]["email"] = self.people.get(user)
            body["user"]["team_id"] = self.teams.get(user, TEAM)
            body["user"]["is_bot"] = user in self.bots
        return response(200, body)

    def calls_to(self, method):
        return [c for c in self.calls if c["method"] == method]

    def blocks(self, call):
        """The blocks a call posted, as a list."""
        raw = (call["json"] or {}).get("blocks") if call["json"] else call["data"].get("blocks")
        return json.loads(raw) if isinstance(raw, str) else (raw or [])

    def text_of(self, call):
        """Everything a call posted, as one string (its text and its blocks)."""
        payload = call["json"] or call["data"]
        return json.dumps(payload.get("text", "")) + json.dumps(self.blocks(call))


def signed(body, secret=SIGNING_SECRET, at=None, content_type="application/json"):
    """Headers for `body` (bytes), signed as Slack signs a request."""
    ts = str(int(at if at is not None else time.time()))
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body,
                           hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": content_type}


def event(kind, user, text="", channel="C0GENERAL", ts=None, event_id=None, team=TEAM,
          **extra):
    ts = ts or f"{time.time():.6f}"
    inner = {"type": kind, "user": user, "text": text, "channel": channel, "ts": ts,
             "event_ts": ts, **{k: v for k, v in extra.items() if k != "shared"}}
    return {"type": "event_callback", "team_id": team, "api_app_id": "A0KNOWLEDGE",
            "event": inner, "event_id": event_id or f"Ev{abs(hash((user, ts, text)))}",
            "event_time": int(time.time()), "is_ext_shared_channel": bool(extra.get("shared"))}


def command_form(user, text, channel="C0GENERAL", team=TEAM, trigger=None):
    return urlencode({"command": "/ask", "text": text, "team_id": team, "user_id": user,
                      "channel_id": channel, "trigger_id": trigger or f"tr{time.time()}",
                      "response_url": f"https://hooks.slack.com/commands/{team}/1/{user}"}).encode()


def interaction_form(payload):
    return urlencode({"payload": json.dumps(payload)}).encode()
