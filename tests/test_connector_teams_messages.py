"""
Microsoft Teams channel messages (a SOURCE, apart from the Teams bot) against
recorded HTTP (tests/fixtures/connectors/teams_messages/, written from the
Graph reference by make_work_tools.py; an unrecorded request fails the test),
through the real framework (kb/services/sync.py): the sign-in + admin consent
connect flow, saved LOCKED to HR and admins, the team / channel picker, the
"let members ask" opt-in, channel delta (nextLink / deltaLink paging, deleted
roots, system messages), threads with their replies, channel membership into
acl (failing closed), Microsoft's protected-API refusal as a typed error that
waits for an admin, deletes behind the mass-delete guard, 429s.
"""

import base64
import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

G = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TEAM = "11111111-2222-3333-4444-555555555555"
GENERAL = "19:general@thread.tacv2"
Q_GENERAL = GENERAL.replace(":", "%3A").replace("@", "%40")
PICKS = [{"id": f"team-{TEAM}", "name": "Acme", "kind": "team"}]
PROTECTED = {"error": {"code": "Forbidden", "message": (
    "Invoked API requires Protected API access in application-only context when not using "
    "Resource Specific Consent. Visit https://docs.microsoft.com/en-us/graph/teams-protected-apis "
    "for more details.")}}


@pytest.fixture(autouse=True)
def app(monkeypatch):
    monkeypatch.setenv("TEAMS_MESSAGES_CLIENT_ID", "tm-client")
    monkeypatch.setenv("TEAMS_MESSAGES_CLIENT_SECRET", "tm-secret")


def id_token(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"x.{body}.y"


def connect(env, picks=PICKS, **settings):
    import integrations_store
    integrations_store.connect(env.ws, "teams_messages", "acme.example (admin@acme.example)",
                               {"tenant": TENANT, "access_token": "app-1",
                                "expires_at": time.time() + 3000}, "admin")
    integrations_store.update(env.ws, "teams_messages", settings={
        "tenant": TENANT, "containers": list(picks), "access_lock": "restricted",
        "members": False, **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "teams_messages", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "teams_messages")


# ---- connecting ------------------------------------------------------------------------
def test_connect_signs_in_asks_for_consent_and_saves_it_locked(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/teams_messages/connect",
                          headers=env.headers()).json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith(f"{LOGIN}/organizations/oauth2/v2.0/authorize")
    assert q["redirect_uri"].endswith("/api/integrations/teams_messages/callback")
    rec.add("POST", f"{LOGIN}/organizations/oauth2/v2.0/token", json_body={
        "access_token": "user", "id_token": id_token({"tid": TENANT,
                                                      "preferred_username": "admin@acme.example"})})
    rec.add("POST", f"{LOGIN}/{TENANT}/oauth2/v2.0/token", status=400, json_body={
        "error": "invalid_grant", "error_description": "AADSTS65001: The user or administrator has not consented to use the application."})
    r = env.client.get("/api/integrations/teams_messages/callback", follow_redirects=False,
                       params={"state": q["state"], "code": "abc"})
    consent = r.headers["location"]
    assert consent.startswith(f"{LOGIN}/{TENANT}/v2.0/adminconsent?")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(consent).query))["state"]
    rec.add("POST", f"{LOGIN}/{TENANT}/oauth2/v2.0/token",
            json_body={"access_token": "app-1", "expires_in": 3599})
    rec.add("GET", f"{G}/teams", params={"$top": "1"}, json_body={"value": []})
    r = env.client.get("/api/integrations/teams_messages/callback", follow_redirects=False,
                       params={"state": state, "admin_consent": "True", "tenant": TENANT})
    assert r.headers["location"] == "/#integrations?connected=teams_messages&choose=1"
    row = integrations_store.get(env.ws, "teams_messages")
    assert row["settings"]["access_lock"] == "restricted"
    c = card(env)
    assert (c["access_locked"], c["policy"], c["options"]) == (True, "restricted",
                                                               {"members": False})


def test_a_missing_permission_at_connect_is_refused(env, monkeypatch):
    rec = Recorded(monkeypatch)
    url = env.client.post("/api/integrations/teams_messages/connect",
                          headers=env.headers()).json()["url"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
    rec.add("POST", f"{LOGIN}/organizations/oauth2/v2.0/token", json_body={
        "id_token": id_token({"tid": TENANT, "preferred_username": "a@acme.example"})})
    rec.add("POST", f"{LOGIN}/{TENANT}/oauth2/v2.0/token", json_body={"access_token": "app"})
    rec.add("GET", f"{G}/teams", status=403, json_body={"error": {
        "code": "Forbidden", "message": "Missing role permissions on the request."}})
    r = env.client.get("/api/integrations/teams_messages/callback", follow_redirects=False,
                       params={"state": state, "code": "abc"})
    assert r.headers["location"] == "/#integrations?error=missing_scope"


# ---- picking and the opt-in --------------------------------------------------------------
def test_the_picker_lists_teams_then_channels_and_members_may_be_let_in(env, monkeypatch):
    import integrations_store
    rec = Recorded(monkeypatch, "teams_messages/full")
    connect(env, picks=[])
    rec.add("GET", f"{G}/teams", json_body={"value": [{"id": TEAM, "displayName": "Acme"}],
                                            "@odata.nextLink": f"{G}/teams?$skiptoken=2"})
    rec.add("GET", f"{G}/teams", params={"$skiptoken": "2"}, json_body={"value": []})
    h = env.headers()
    top = env.client.get("/api/integrations/teams_messages/containers", headers=h).json()
    assert top["items"] == [{"id": f"team-{TEAM}", "name": "Acme", "kind": "team",
                             "has_children": True}]
    channels = env.client.get(f"/api/integrations/teams_messages/containers?parent=team-{TEAM}",
                              headers=h).json()["items"]
    assert [(c["name"], c["private"]) for c in channels] == [("General", False),
                                                             ("Leadership", True)]
    # Locked: the policy can't be opened up until members are let in.
    r = env.client.put("/api/integrations/teams_messages/policy", headers=h,
                       json={"policy": "source"})
    assert r.status_code == 400
    r = env.client.put("/api/integrations/teams_messages/containers", headers=h, json={
        "containers": [channels[0]], "options": {"members": True}})
    assert r.status_code == 200
    settings = integrations_store.get(env.ws, "teams_messages")["settings"]
    assert (settings["members"], settings.get("access_lock")) == (True, None)
    assert env.client.put("/api/integrations/teams_messages/policy", headers=h,
                          json={"policy": "source"}).status_code == 200
    r = env.client.put("/api/integrations/teams_messages/containers", headers=h,
                       json={"containers": [{"id": "drive-1", "name": "x"}]})
    assert r.status_code == 400


# ---- syncing ---------------------------------------------------------------------------
def test_a_full_sync_reads_threads_with_replies_and_channel_membership(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "teams_messages/full")
    connect(env)
    summary = sync.sync(env.ws, "teams_messages")
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 3)
    thread = item(env, f"{TEAM}|{GENERAL}|1001")
    assert thread["title"] == "General: VPN"
    assert thread["acl"] == {"via": "teams", "channel": GENERAL, "groups": [f"entra:{TEAM}"],
                             "users": ["ann@acme.example", "bob@acme.example"]}
    text = env.text("teams_messages", thread["file_name"])
    assert "Where is the VPN guide?" in text and "<b>" not in text
    assert text.index("It's on the wiki") < text.index("Thanks!")       # oldest first
    assert item(env, f"{TEAM}|{GENERAL}|1002") is None                 # a system event
    private = item(env, f"{TEAM}|{'19:secret@thread.tacv2'}|2001")
    assert private["acl"] == {"via": "teams", "channel": "19:secret@thread.tacv2",
                              "groups": [], "users": ["ann@acme.example"]}
    cursor = json.loads(sync_store.state(env.ws, "teams_messages")["cursor"])
    assert cursor["delta"][f"{TEAM}|{GENERAL}"].endswith("$deltatoken=dt-general-1")
    # One message call a second per channel (Graph's per-channel limit).
    assert rec.requests_to("/messages/delta")[0]["params"] == {"$top": "50"}


def test_then_the_delta_link_brings_only_changes_and_deleted_roots(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "teams_messages/full")
    connect(env)
    sync.sync(env.ws, "teams_messages")
    rec.load("teams_messages/incremental")
    summary = sync.sync(env.ws, "teams_messages")
    assert rec.unused() == []
    assert (summary["mode"], summary["deleted"], summary["upserted"]) == ("incremental", 1, 1)
    thread = item(env, f"{TEAM}|{GENERAL}|1001")
    assert "(Mac)" in env.text("teams_messages", thread["file_name"])
    assert thread["acl"]["users"] == ["ann@acme.example"]               # Bob left the team
    assert item(env, f"{TEAM}|{GENERAL}|1003") is None


def test_the_protected_api_refusal_is_typed_and_waits_for_an_admin(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    rec = Recorded(monkeypatch, "teams_messages/full")
    connect(env)
    rec.exchanges = [e for e in rec.exchanges if "/messages/delta" not in e["request"]["url"]]
    rec.add("GET", f"{G}/teams/{TEAM}/channels/{Q_GENERAL}/messages/delta", status=403,
            json_body=PROTECTED)
    with pytest.raises(base.ProtectedApi):
        sync.sync(env.ws, "teams_messages")
    c = card(env)
    assert c["error"]["code"] == "protected_api"
    assert "protected APIs" in c["error"]["message"]
    assert integrations_store.get(env.ws, "teams_messages")["last_error"] == "protected_api"
    # Not retried by itself: the periodic sync leaves it alone.
    started = []
    monkeypatch.setattr(sync, "start_sync", lambda ws, p, **kw: started.append((ws, p)))
    sync.sync_all()
    assert (env.ws, "teams_messages") not in started


def test_unreadable_members_fail_closed(env, monkeypatch):
    import access
    from kb.services import permissions, sync
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{G}/teams/{TEAM}/members", status=403, json_body={"error": {
        "code": "Forbidden", "message": "Missing role permissions on the request."}})
    rec.load("teams_messages/full")
    connect(env)
    sync.sync(env.ws, "teams_messages")
    acl = item(env, f"{TEAM}|{GENERAL}|1001")["acl"]
    assert acl == {"via": "teams", "channel": GENERAL, "partial": True}
    assert permissions.Resolver(env.ws).resolve(acl) == ([access.NOBODY], 1)


def test_a_full_listing_that_lost_everything_is_held_and_429s_wait(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    rec.add("GET", f"{G}/teams/{TEAM}/channels", status=429, headers={"Retry-After": "6"})
    rec.load("teams_messages/full")
    connect(env)
    sync.sync(env.ws, "teams_messages")
    assert 6 in rec.slept
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    for c in (GENERAL, "19:secret@thread.tacv2"):
        q = c.replace(":", "%3A").replace("@", "%40")
        rec.add("GET", f"{G}/teams/{TEAM}/channels/{q}/messages/delta",
                json_body={"value": [], "@odata.deltaLink": f"{G}/x?$deltatoken=z"})
    rec.add("GET", f"{G}/teams/{TEAM}/members", json_body={"value": []})
    rec.add("GET", f"{G}/teams/{TEAM}/channels/19:secret@thread.tacv2/allMembers",
            json_body={"value": []})
    summary = sync.sync(env.ws, "teams_messages", full=True)
    assert (summary["held_deletes"], summary["deleted"]) == (3, 0)


def test_teams_messages_is_live_and_says_what_it_needs(env):
    c = card(env)
    assert (c["availability"], c["picker"], c["acl"]) == ("live", "tree", True)
    assert "protected APIs" in c["note"]
