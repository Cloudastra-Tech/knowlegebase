"""
The Slack app (kb/services/slack.py has how it answers):

  Set up, from the Integrations screen
    GET    /api/admin/slack           the workspace's Slack app: which app, installed in
                                      which Slack team, its settings and request URLs (HR / Admin)
    PUT    /api/admin/slack/app       {mode: central | custom, client_id, client_secret,
                                      signing_secret}: ours, or the customer's own app   (admin)
    GET    /api/admin/slack/manifest  slack/manifest.yml with this server's URLs       (admin)
    POST   /api/admin/slack/install   -> {url}: Slack's OAuth v2 consent screen        (admin)
    GET    /api/slack/oauth/callback  Slack sends the browser back here
    DELETE /api/admin/slack/install   uninstall: the bot token is revoked and forgotten (admin)
    PUT    /api/admin/slack/settings  {unknown_users, auto_answer, auto_answer_channels} (admin)

  Called by Slack: every request's signature is checked (with the signing
  secret of the app it's for) before anything in it is read
    POST   /api/slack/events          the Events API (url_verification, app_mention, message.im, ...)
    POST   /api/slack/commands        /ask
    POST   /api/slack/interactions    the feedback buttons and the message shortcut
    POST   /api/slack/apps/{app_key}/events | commands | interactions
                                      the same, for a customer's own app

Slack wants an answer within 3 seconds: these only check, note and queue
(kb/services/slack.take_*); the answer follows from a job.
"""

import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import kb.audit
import kb.state
import lifecycle_store
import slack_store
from kb.audit import audited
from kb.deps import public_url, require_admin, require_hr, ws_of
from kb.services import slack

router = APIRouter()
log = logging.getLogger("uvicorn.error")

OAUTH_STATE_SECONDS = 10 * 60
PROVIDER = "slack_app"            # in kb.state.oauth_states
CLIENT_ID = re.compile(r"^\d{4,20}\.\d{4,20}$")
SECRET = re.compile(r"^[A-Za-z0-9]{16,128}$")
CHANNEL = re.compile(r"^[CG][A-Z0-9]{2,20}$")


# ---- Set up ---------------------------------------------------------------------------------
def _status(install):
    if install and install["status"] in ("installed", "revoked"):
        return install["status"]
    return "ready" if slack.app_of(install) else "not_configured"


def _view(request, ws, user):
    install = slack_store.get(ws)
    base = public_url(request, "")
    path = slack.request_path(install)
    keep = ("mode", "client_id", "has_app_secrets", "team_id", "team_name", "installed_by",
            "installed_at", "unknown_users", "auto_answer", "auto_answer_channels",
            "updated_by", "updated_at")
    return {"status": _status(install), "can_manage": user["role"] == "admin",
            "central_available": slack.central_app() is not None,
            **{k: (install or {}).get(k) for k in keep},
            "mode": (install or {}).get("mode") or "central",
            "unknown_users": (install or {}).get("unknown_users") or "refuse",
            "auto_answer": bool((install or {}).get("auto_answer")),
            "auto_answer_channels": (install or {}).get("auto_answer_channels") or [],
            "scopes": list(slack.SCOPES),
            "urls": {"events": f"{base}{path}/events", "commands": f"{base}{path}/commands",
                     "interactions": f"{base}{path}/interactions",
                     "redirect": f"{base}/api/slack/oauth/callback"}}


@router.get("/api/admin/slack")
def slack_settings(request: Request, user=Depends(require_hr)):
    return _view(request, ws_of(user), user)


class AppIn(BaseModel):
    mode: str = Field(pattern=r"^(central|custom)$")
    client_id: str = Field(default="", max_length=60)
    client_secret: str = Field(default="", max_length=200)
    signing_secret: str = Field(default="", max_length=200)


@router.put("/api/admin/slack/app")
def set_slack_app(body: AppIn, request: Request, user=Depends(require_admin),
                  audit=audited("slack.app")):
    """Use our app (set in the environment) or the customer's own internal app,
    made from slack/manifest.yml in their Slack. Its secrets are sealed; a
    different app ends the install of the one before."""
    ws = ws_of(user)
    if body.mode == "central":
        if not slack.central_app():
            raise HTTPException(400, "This server has no Slack app of its own yet "
                                     "(SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_SIGNING_SECRET). "
                                     "Use your own app instead.")
        slack_store.use_central_app(ws, user["username"])
    else:
        client_id = body.client_id.strip()
        client_secret, signing_secret = body.client_secret.strip(), body.signing_secret.strip()
        if not CLIENT_ID.match(client_id):
            raise HTTPException(400, "That isn't a Slack client ID: it looks like "
                                     "1234567890.1234567890 (Basic Information → App Credentials).")
        if not SECRET.match(client_secret) or not SECRET.match(signing_secret):
            raise HTTPException(400, "Paste the Client Secret and the Signing Secret from Basic "
                                     "Information → App Credentials (letters and digits).")
        slack_store.set_custom_app(ws, client_id, client_secret, signing_secret,
                                   user["username"])
    # The client ID is recorded; the secrets never are.
    audit.record("slack_app", body.mode, {"mode": body.mode,
                                          "client_id": body.client_id.strip() or None},
                 user=user)
    return _view(request, ws, user)


@router.get("/api/admin/slack/manifest")
def slack_manifest(request: Request, user=Depends(require_admin)):
    """The app manifest to create the app from, with this server's URLs."""
    install = slack_store.get(ws_of(user))
    return {"manifest": slack.manifest(public_url(request, ""), install),
            "filename": "knowledge-slack-manifest.yml"}


@router.post("/api/admin/slack/install")
def slack_install(request: Request, user=Depends(require_admin),
                  audit=audited("slack.install_start")):
    """{url} of Slack's consent screen, to install the app in a Slack workspace."""
    ws = ws_of(user)
    install = slack_store.get(ws)
    app = slack.app_of(install)
    if not app:
        raise HTTPException(400, "Choose the Slack app first: this server's, or your own.")
    states = kb.state.oauth_states
    now = time.time()
    for s in [k for k, v in states.items() if v["expires"] < now]:
        del states[s]
    token = secrets.token_urlsafe(24)
    redirect_uri = public_url(request, "/api/slack/oauth/callback")
    states[token] = {"workspace": ws, "user": user["username"], "provider": PROVIDER,
                     "expires": now + OAUTH_STATE_SECONDS, "redirect_uri": redirect_uri}
    audit.record("slack_app", (install or {}).get("mode") or "central", user=user)
    return {"url": slack.authorize_url(app["client_id"], redirect_uri, token)}


CALLBACK_ERRORS = {"access_denied": "consent_denied"}


@router.get("/api/slack/oauth/callback")
def slack_oauth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    """Slack sends the browser back here. The one-time `state` ties it to the
    workspace and admin who clicked Install (a redirect carries no session)."""
    pending = kb.state.oauth_states.pop(state, None)
    if not pending or pending["expires"] < time.time() or pending.get("provider") != PROVIDER:
        return RedirectResponse("/integrations?slack_error=expired")
    if error or not code:
        return RedirectResponse("/integrations?slack_error="
                                + CALLBACK_ERRORS.get(error, "cancelled" if not error
                                                      else "connect_failed"))
    ws = pending["workspace"]
    try:
        install = slack.finish_install(ws, code, pending["redirect_uri"], pending["user"])
    except slack.InstallError as e:
        return RedirectResponse(f"/integrations?slack_error={e.code}")
    kb.audit.record(request, ws, "slack.install", pending["user"], "slack_team",
                    install["team_id"], {"team": install["team_name"], "mode": install["mode"],
                                         "scopes": install["scopes"]})
    return RedirectResponse("/integrations?slack=installed")


@router.delete("/api/admin/slack/install")
def slack_uninstall(request: Request, user=Depends(require_admin),
                    audit=audited("slack.uninstall")):
    """Remove the app from the Slack workspace: its bot token is revoked at Slack
    and forgotten here. The settings, and your own app's secrets, stay."""
    ws = ws_of(user)
    install = slack_store.get(ws)
    if not install or install["status"] != "installed":
        raise HTTPException(400, "Slack isn't installed.")
    slack.uninstall(ws)
    audit.record("slack_team", install["team_id"], {"team": install["team_name"], "by": "admin"},
                 user=user)
    return _view(request, ws, user)


class SettingsIn(BaseModel):
    unknown_users: str | None = Field(default=None, pattern=r"^(refuse|public)$")
    auto_answer: bool | None = None
    auto_answer_channels: list[str] | None = Field(default=None,
                                                   max_length=slack_store.MAX_AUTO_CHANNELS)


@router.put("/api/admin/slack/settings")
def set_slack_settings(body: SettingsIn, request: Request, user=Depends(require_admin),
                       audit=audited("slack.settings")):
    """Who gets answered (people Slack knows but this workspace doesn't: refused,
    the default, or answered from public documents only), and auto-answer in
    help channels (off by default)."""
    ws = ws_of(user)
    channels = None
    if body.auto_answer_channels is not None:
        channels = [c.strip().upper() for c in body.auto_answer_channels if c.strip()]
        bad = [c for c in channels if not CHANNEL.match(c)]
        if bad:
            raise HTTPException(400, f"Not a channel ID: {bad[0][:30]}. Copy it from the channel's "
                                     "details in Slack (it starts with C).")
    before = slack_store.get(ws) or {}
    slack_store.set_settings(ws, user["username"], body.unknown_users, body.auto_answer, channels)
    after = slack_store.get(ws)
    audit.record("workspace", ws, {k: [before.get(k), after[k]] for k in (
        "unknown_users", "auto_answer", "auto_answer_channels") if before.get(k) != after[k]},
        user=user)
    return _view(request, ws, user)


# ---- Requests from Slack -----------------------------------------------------------------------
@dataclass
class SlackRequest:
    body: bytes
    install: dict | None          # a custom app's row (found from its URL), else None
    custom: bool

    def install_for(self, team_id):
        """The install this request is for: the custom app's, if it's installed in
        that team; for the central app, the workspace that installed it there."""
        if self.custom:
            i = self.install
            i = i if i and i["status"] == "installed" and i["team_id"] == team_id else None
        else:
            i = slack_store.by_team(team_id)
            i = i if i and i["mode"] == "central" else None
        # A suspended workspace, or one being deleted, takes nothing from Slack.
        return i if i and lifecycle_store.status(i["workspace"]) == lifecycle_store.ACTIVE \
            else None


async def slack_signature(request: Request):
    """The request, once its Slack signature checks out against the signing secret
    of the app it was sent to (X-Slack-Signature, X-Slack-Request-Timestamp, at
    most 5 minutes old). Anything else is refused before it's read."""
    body = await request.body()
    key = request.path_params.get("app_key")
    install = slack_store.by_app_key(key) if key else None
    app = slack.app_of(install) if (install or not key) else None
    if not app or not slack.signature_ok(app["signing_secret"],
                                         request.headers.get("x-slack-request-timestamp"), body,
                                         request.headers.get("x-slack-signature")):
        raise HTTPException(401, "This request isn't signed by Slack.")
    return SlackRequest(body, install, bool(key))


def _form(body):
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}


@router.post("/api/slack/events")
@router.post("/api/slack/apps/{app_key}/events")
async def slack_events(req: SlackRequest = Depends(slack_signature),
                       audit=audited("slack.event")):
    try:
        envelope = json.loads(req.body)
    except ValueError:
        raise HTTPException(400, "Not JSON.")
    if envelope.get("type") == "url_verification":
        return {"challenge": envelope.get("challenge")}
    if envelope.get("type") != "event_callback":
        return {"ok": True}
    install = req.install_for(envelope.get("team_id"))
    if not install:                     # not installed here (any more): nothing to do
        return {"ok": True}
    await run_in_threadpool(slack.take_event, install, envelope)
    return {"ok": True}


@router.post("/api/slack/commands")
@router.post("/api/slack/apps/{app_key}/commands")
async def slack_commands(req: SlackRequest = Depends(slack_signature),
                         audit=audited("slack.command")):
    form = _form(req.body)
    install = req.install_for(form.get("team_id"))
    if not install:
        return {"response_type": "ephemeral",
                "text": "The Knowledge assistant isn't installed for this Slack workspace."}
    return await run_in_threadpool(slack.take_command, install, form)


@router.post("/api/slack/interactions")
@router.post("/api/slack/apps/{app_key}/interactions")
async def slack_interactions(req: SlackRequest = Depends(slack_signature),
                             audit=audited("slack.interaction")):
    try:
        payload = json.loads(_form(req.body).get("payload") or "{}")
    except ValueError:
        raise HTTPException(400, "Not JSON.")
    install = req.install_for((payload.get("team") or {}).get("id"))
    if install:
        await run_in_threadpool(slack.take_interaction, install, payload)
    return {}
