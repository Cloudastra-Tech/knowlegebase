"""
Gmail and Google Calendar, a member's OWN connections, against recorded HTTP
(tests/fixtures/connectors/gmail/ and google_calendar/, written from Google's
API reference by make_work_tools.py; an unrecorded request fails the test),
through the real framework (kb/services/sync.py) under the member's personal
key: the access token refreshed under that key (and a revoked grant typed),
threads.list paging, threads as Markdown (text and HTML parts, attachments
left out), history.list increments and a history id Gmail no longer has (404:
full), calendar events with attendees, recurring series, the window, sync
tokens and a 410, deletes behind the mass-delete guard, 429s and typed errors.
Who can see it: tests/test_personal_connections.py.
"""

import json
import time

import pytest

from connector_fixtures import Recorded
from connectors import base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

GM = "https://gmail.googleapis.com/gmail/v1/users/me"
GC = "https://www.googleapis.com/calendar/v3"
TOKEN = "https://oauth2.googleapis.com/token"
OWNER = "ann@acme.example"


@pytest.fixture(autouse=True)
def google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("GMAIL_SYNC", "on")
    monkeypatch.setenv("CALENDAR_SYNC", "on")


def connect(env, provider, picks, **secret):
    import integrations_store
    from connectors import registry
    key = registry.personal_key(provider, OWNER)
    integrations_store.connect(env.ws, key, OWNER, {
        "access_token": "g-at", "refresh_token": "g-rt", "expires_at": time.time() + 3000,
        **secret}, OWNER)
    integrations_store.update(env.ws, key, settings={"owner": OWNER, "google": OWNER,
                                                     "containers": picks})
    return key


def item(env, key, sid):
    import sync_store
    return sync_store.item(env.ws, key, sid)


GMAIL = [{"id": "Label_7", "name": "HR", "kind": "label"}]
CALENDAR = [{"id": OWNER, "name": "Ann", "kind": "calendar"}]


# ---- Gmail -----------------------------------------------------------------------------
def test_gmail_full_sync_renders_threads_for_their_owner(env, monkeypatch):
    import access
    from kb.services import permissions, sync
    rec = Recorded(monkeypatch, "gmail/full")
    key = connect(env, "gmail", GMAIL)
    assert key.startswith("gmail~") and OWNER not in key       # no address in paths
    summary = sync.sync(env.ws, key)
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 2)
    thread = item(env, key, "th1")
    assert thread["title"] == "Offer letter for Bob"              # the subject, not the snippet
    text = env.text(key, thread["file_name"])
    assert "Bob's salary will be 90,000." in text and "Approved & signed." in text
    assert "From: hr@acme.example" in text and "invoice.pdf" not in text
    assert permissions.connector_acls(env.ws)[f"connectors/{key}/{thread['file_name']}"] == [
        access.user(OWNER)]
    listing = rec.requests_to(f"{GM}/threads")[0]["params"]
    assert (listing["labelIds"], listing["q"]) == ("Label_7", "newer_than:180d")
    assert json.loads(__import__("sync_store").state(env.ws, key)["cursor"])["history"] == "5000"


def test_gmail_history_brings_changes_and_a_removed_label_deletes(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "gmail/full")
    key = connect(env, "gmail", GMAIL)
    sync.sync(env.ws, key)
    rec.load("gmail/incremental")
    summary = sync.sync(env.ws, key)
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"], summary["deleted"]) == ("incremental", 1, 1)
    assert "Start date moved to November." in env.text(key, item(env, key, "th1")["file_name"])
    assert item(env, key, "th2") is None                         # no longer labelled HR


def test_a_history_id_gmail_no_longer_has_means_a_full_sync(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "gmail/full")
    key = connect(env, "gmail", GMAIL)
    sync.sync(env.ws, key)
    rec.add("GET", f"{GM}/history", status=404, json_body={"error": {"code": 404}})
    rec.load("gmail/full")
    assert sync.sync(env.ws, key)["mode"] == "full"


def test_gmail_mass_delete_is_held_and_spam_can_not_be_picked(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "gmail/full")
    key = connect(env, "gmail", GMAIL)
    sync.sync(env.ws, key)
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.add("GET", f"{GM}/profile", json_body={"historyId": "6000"})
    rec.add("GET", f"{GM}/threads", json_body={"threads": []})
    summary = sync.sync(env.ws, key, full=True)
    assert (summary["held_deletes"], summary["deleted"]) == (2, 0)
    from connectors import gmail
    with pytest.raises(base.FormError):
        gmail.CONNECTOR.check_containers([{"id": "SPAM", "name": "Spam"}])


def test_the_token_is_refreshed_under_the_members_key_and_a_revoked_grant_is_typed(
        env, monkeypatch):
    import integrations_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    key = connect(env, "gmail", GMAIL, expires_at=time.time() - 1)
    rec.add("POST", TOKEN, json_body={"access_token": "g-at-2", "expires_in": 3599})
    rec.add("GET", f"{GM}/profile", status=429, headers={"Retry-After": "4"})
    rec.load("gmail/full")
    sync.sync(env.ws, key)
    assert rec.calls[0]["data"]["refresh_token"] == "g-rt"
    assert integrations_store.secret(env.ws, key)["access_token"] == "g-at-2"
    assert 4 in rec.slept
    assert rec.requests_to(f"{GM}/profile")[-1]["headers"]["Authorization"] == "Bearer g-at-2"
    integrations_store.update(env.ws, key, secret={**integrations_store.secret(env.ws, key),
                                                   "expires_at": 0})
    rec.add("POST", TOKEN, status=400, json_body={"error": "invalid_grant"})
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, key, full=True)
    assert integrations_store.get(env.ws, key)["last_error"] == "auth_expired"


def test_nothing_is_synced_until_labels_are_picked(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    key = connect(env, "gmail", [])
    assert not sync.ready_to_sync(env.ws, key)
    sync.sync(env.ws, key)
    assert rec.calls == []


# ---- Google Calendar -------------------------------------------------------------------
def test_calendar_full_sync_keeps_events_in_the_window_with_attendees(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "google_calendar/full")
    key = connect(env, "google_calendar", CALENDAR)
    summary = sync.sync(env.ws, key)
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"]) == ("full", 2)
    review = item(env, key, f"{OWNER}|ev1")
    text = env.text(key, review["file_name"])
    assert review["title"] == "Salary review with Bob"
    assert "Bob <bob@acme.example> (accepted)" in text and "Where: Room 4" in text
    assert "Discuss the raise." in text
    assert "RRULE:FREQ=WEEKLY" in env.text(key, item(env, key, f"{OWNER}|ev2")["file_name"])
    assert item(env, key, f"{OWNER}|ev3") is None              # ended years ago
    assert json.loads(__import__("sync_store").state(env.ws, key)["cursor"])["tokens"] == {
        OWNER: "cs-1"}


def test_calendar_sync_token_changes_and_cancelled_events(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "google_calendar/full")
    key = connect(env, "google_calendar", CALENDAR)
    sync.sync(env.ws, key)
    rec.load("google_calendar/incremental")
    summary = sync.sync(env.ws, key)
    assert rec.unused() == []
    assert (summary["mode"], summary["upserted"], summary["deleted"]) == ("incremental", 1, 1)
    assert item(env, key, f"{OWNER}|ev1")["title"] == "Salary review with Bob (moved)"
    assert item(env, key, f"{OWNER}|ev2") is None


def test_a_gone_sync_token_lists_everything_again(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch, "google_calendar/full")
    key = connect(env, "google_calendar", CALENDAR)
    sync.sync(env.ws, key)
    rec.add("GET", f"{GC}/calendars/ann%40acme.example/events", status=410,
            params={"syncToken": "cs-1"}, json_body={"error": {"code": 410}})
    rec.load("google_calendar/full")
    assert sync.sync(env.ws, key)["mode"] == "full"


def test_the_calendar_picker_lists_readable_calendars(env, monkeypatch):
    from connectors import google_calendar
    rec = Recorded(monkeypatch)
    key = connect(env, "google_calendar", [])
    rec.add("GET", f"{GC}/users/me/calendarList", json_body={"items": [
        {"id": OWNER, "summary": "Ann", "accessRole": "owner", "primary": True},
        {"id": "x@group.calendar.google.com", "summary": "Busy only",
         "accessRole": "freeBusyReader"}], "nextPageToken": "p2"})
    rec.add("GET", f"{GC}/users/me/calendarList", params={"pageToken": "p2"},
            json_body={"items": [{"id": "team@group.calendar.google.com", "summary": "Team",
                                  "accessRole": "reader"}]})
    ctx = base.SyncContext(workspace=env.ws, provider=key, settings={}, known={})
    assert [c["id"] for c in google_calendar.CONNECTOR.list_containers(ctx)] == [
        OWNER, "team@group.calendar.google.com"]


def test_both_are_personal_and_never_on_the_integrations_screen(env):
    cards = env.client.get("/api/integrations", headers=env.headers()).json()["integrations"]
    assert not [c for c in cards if c["id"] in ("gmail", "google_calendar")]
    catalog = env.client.get("/api/integrations/catalog").json()["connectors"]
    assert {"gmail", "google_calendar"} <= {c["id"] for c in catalog}
