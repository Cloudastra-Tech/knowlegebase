"""
Recorded HTTP for the Teams bot's tests: kb/services/teams.py's `send` is swapped
for FakeTeams, which records every request and answers Bot Framework's keys,
Microsoft's token endpoint and the Bot Connector's REST API from
tests/fixtures/teams/*.json. Nothing reaches the network: a request to anywhere
else fails the test.

The Bot Connector's signing key is an RSA key made here when the tests run (never
a file): FakeTeams publishes its public half as Bot Framework's keys, endorsed for
"msteams", and token() signs with its private half as the Connector would.

    teams.people["aad-hr"] = "hr@acme.example"         # the member lookup's email
    teams.fail["POST /v3/conversations"] = 403          # an HTTP error for that call
    teams.calls_to("PUT")                               # [{"method", "url", "json", ...}]
"""

import copy
import itertools
import json
import pathlib
import time
import uuid
from urllib.parse import unquote, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from connector_fixtures import response

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "teams"
ACTIVITIES = json.loads((FIXTURES / "activities.json").read_text())
CONNECTOR = json.loads((FIXTURES / "connector.json").read_text())
SERVICE_URL = "https://smba.trafficmanager.net/teams/"
TENANT = "11111111-2222-3333-4444-555555555555"
BOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"          # the bot's Microsoft App ID
BOT_SECRET = "a-test-client-secret-value~1"
ISSUER = "https://api.botframework.com"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key-1"


def _jwk(key, kid, endorsements):
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**public, "kid": kid, "use": "sig", "endorsements": endorsements}


def token(aud=BOT, iss=ISSUER, service_url=SERVICE_URL, exp_in=600, key=None, kid=KID, **extra):
    """A JWT as the Bot Connector signs one (by default: good for 10 minutes)."""
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "serviceUrl": service_url, "nbf": now - 60,
              "iat": now - 60, "exp": now + exp_in, **extra}
    return jwt.encode(claims, key or _KEY, algorithm="RS256", headers={"kid": kid})


def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


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


_ids = itertools.count(1)


def activity(kind, aad, text="", tenant=TENANT, message_id="", rating=1, activity_id=None,
             service_url=SERVICE_URL):
    """One of tests/fixtures/teams/activities.json, filled in for a user."""
    a = _fill(copy.deepcopy(ACTIVITIES[kind]), {
        "activity_id": activity_id or f"1700000000{next(_ids):06d}", "service_url": service_url,
        "user": aad, "aad": aad, "tenant": tenant, "bot": BOT, "text": text,
        "message_id": message_id})
    value = a.get("value") or {}
    target = (value.get("action") or {}).get("data") if "action" in value else value
    if target and "rating" in target:
        target["rating"] = rating
    return a


class FakeTeams:
    def __init__(self, monkeypatch):
        from kb.services import teams
        self.calls = []
        self.people = {}              # AAD object id -> email
        self.upns = {}                # AAD object id -> UPN, when not the email
        self.fail = {}                # "METHOD /path" -> HTTP status for that call
        self.keys = [_jwk(_KEY, KID, ["msteams"])]
        self.slept = []
        self._ids = itertools.count(1)
        monkeypatch.setattr(teams, "send", self.send)
        monkeypatch.setattr(teams, "sleep", self.slept.append)

    def send(self, method, url, json=None, data=None, headers=None, timeout=None):
        parts = urlsplit(url)
        call = {"method": method, "url": url, "json": json, "data": dict(data or {}),
                "headers": dict(headers or {}), "path": unquote(parts.path)}
        self.calls.append(call)
        if url == "https://login.botframework.com/v1/.well-known/keys":
            return response(200, {"keys": self.keys})
        if url == "https://login.botframework.com/v1/.well-known/openidconfiguration":
            return response(200, CONNECTOR["openid_configuration"])
        if parts.hostname == "login.microsoftonline.com":
            if (data or {}).get("client_secret") != BOT_SECRET:
                return response(401, {"error": "invalid_client"})
            return response(200, CONNECTOR["token"])
        if parts.hostname != urlsplit(SERVICE_URL).hostname:
            raise AssertionError(f"the Teams bot called {url}")
        assert call["headers"].get("Authorization") == "Bearer " + CONNECTOR["token"]["access_token"]
        path = call["path"].removeprefix("/teams")
        for call_name, status in self.fail.items():
            m, p = call_name.split(" ", 1)
            if m == method and path == p:
                return response(status, {"error": {"code": "Forbidden"}})
        new_id = f"1:reply-{next(self._ids)}"
        if method == "GET" and "/members/" in path:
            user = path.rsplit("/", 1)[1].removeprefix("29:")
            fields = {"user": user, "aad": user, "tenant": TENANT,
                      "email": self.people.get(user, ""),
                      "upn": self.upns.get(user, self.people.get(user, ""))}
            return response(200, _fill(CONNECTOR["member"], fields))
        if method == "POST" and path == "/v3/conversations":
            aad = (json or {}).get("members", [{}])[0].get("id", "").removeprefix("29:")
            return response(201, _fill(CONNECTOR["conversation_resource_response"],
                                       {"aad": aad, "new_id": new_id,
                                        "service_url": SERVICE_URL}))
        if method in ("POST", "PUT") and "/activities" in path:
            return response(200, _fill(CONNECTOR["resource_response"], {"new_id": new_id}))
        raise AssertionError(f"no recorded answer for {method} {path}")

    def calls_to(self, method, contains=""):
        return [c for c in self.calls if c["method"] == method and contains in c["path"]
                and "login." not in c["url"]]

    def posted(self):
        """Every activity the bot sent or updated, in order."""
        return [c["json"] for c in self.calls
                if c["method"] in ("POST", "PUT") and "/activities" in c["path"]]

    @staticmethod
    def card(act):
        """The Adaptive Card an activity carries, or None."""
        for a in (act or {}).get("attachments") or []:
            if a.get("contentType") == "application/vnd.microsoft.card.adaptive":
                return a["content"]
        return None

    def text_of(self, act):
        return json.dumps(act)


def headers(tok=None, **kw):
    return {"Authorization": f"Bearer {tok or token(**kw)}", "Content-Type": "application/json"}


def new_guid():
    return str(uuid.uuid4())
