"""
connectors/google_calendar.py  —  a member's OWN Google Calendar, for them alone (a
personal connector: connectors/google_personal.py, kb/routers/my_integrations.py).

Opt-in: the operator turns it on for the server (CALENDAR_SYNC=on; calendar.readonly
is a sensitive scope, see google_personal.py), then each member connects their
own account and picks the CALENDARS to sync; nothing is synced until they do.

Read-only (calendar.readonly). What is synced: each EVENT (a recurring event once,
as its series) whose end is within the last WINDOW_DAYS or later: its title,
when and where, organizer, attendees (names, emails and answers) and
description, as Markdown. Everything synced is for the member alone: user:<them>
(kb/services/permissions.py personal_acl), never the workspace's.

How a sync finds what changed, per calendar (events.list with syncToken):
  FULL (first run, the picks changed, every RECONCILE seconds, asked for, or a
  410 Gone for that calendar's sync token): every event (showDeleted=false);
  anything known but not listed is deleted (behind the mass-delete guard). The
  last page's nextSyncToken is kept.
  INCREMENTAL: events.list with the calendar's syncToken: only events changed
  since; one with status "cancelled" is deleted.

Quota: per project and per user per minute, answered with 403 / 429
usageLimits reasons (retried by connectors/http.py). Calls are spaced at RATE a
second per member.

API references (checked 2026-10-27):
  scopes         https://developers.google.com/workspace/calendar/api/auth
  calendars      https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list
                 (id, summary, primary, accessRole)
  events         https://developers.google.com/workspace/calendar/api/v3/reference/events/list
                 (syncToken, pageToken, nextSyncToken, showDeleted, maxResults <= 2500;
                 syncToken can't be combined with timeMin / updatedMin / q),
                 https://developers.google.com/workspace/calendar/api/v3/reference/events
                 (summary, description, location, start, end, organizer, attendees,
                 status cancelled, updated, htmlLink, recurrence)
  sync           https://developers.google.com/workspace/calendar/api/guides/sync (410: full)
  errors, quota  https://developers.google.com/workspace/calendar/api/guides/errors,
                 https://developers.google.com/workspace/calendar/api/guides/quota
"""

import datetime
import hashlib
import json
import re
import time
import urllib.parse

from connectors.base import Cursor, Delete, FormError, Item, LimitReached, NotFound, Upsert
from connectors.google_personal import GooglePersonal

PROVIDER = "google_calendar"
API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
WINDOW_DAYS = 365
RECONCILE = 24 * 60 * 60


def _when(t):
    """An event time ({dateTime} or {date}) as text."""
    t = t or {}
    return (t.get("dateTime") or t.get("date") or "").replace("T", " ")[:16]


def _ended_before(event, cutoff):
    end = (event.get("end") or {})
    value = end.get("dateTime") or end.get("date") or ""
    if event.get("recurrence"):
        return False                       # a series: its later occurrences may still come
    try:
        when = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when < cutoff


def _text(event, calendar):
    lines = [f"Calendar: {calendar}", f"When: {_when(event.get('start'))} to "
                                      f"{_when(event.get('end'))}"]
    if event.get("recurrence"):
        lines.append("Repeats: " + "; ".join(event["recurrence"]))
    if event.get("location"):
        lines.append(f"Where: {event['location']}")
    org = event.get("organizer") or {}
    if org.get("email") or org.get("displayName"):
        lines.append(f"Organizer: {org.get('displayName') or ''} <{org.get('email') or ''}>"
                     .replace(" <>", "").strip())
    people = [f"- {a.get('displayName') or a.get('email')}"
              + (f" <{a['email']}>" if a.get("displayName") and a.get("email") else "")
              + (f" ({a['responseStatus']})" if a.get("responseStatus") else "")
              for a in event.get("attendees") or () if a.get("email") or a.get("displayName")]
    if people:
        lines += ["", "Attendees:"] + people
    description = re.sub(r"<[^>]+>", "", event.get("description") or "").strip()
    if description:
        lines += ["", "## Description", "", description]
    return "\n".join(lines)


class GoogleCalendar(GooglePersonal):
    PROVIDER = PROVIDER
    LABEL = "Google Calendar"
    DESCRIPTION = "Your own events: titles, descriptions and attendees. Only you can ask."
    SCOPE = SCOPE
    SWITCH = "CALENDAR_SYNC"
    UNIT = "events"
    NOTE = ("Only you can ask about your events: they never reach anyone else in the "
            "workspace, admins included. Nothing is synced until you pick calendars.")

    def list_containers(self, ctx, parent=None):
        out, params = [], {"maxResults": 250}
        while True:
            body = self._get(ctx, f"{API}/users/me/calendarList", params).json() or {}
            out += [{"id": c["id"], "name": c.get("summaryOverride") or c.get("summary") or c["id"],
                     "kind": "calendar"} for c in body.get("items") or ()
                    if c.get("accessRole") in ("owner", "writer", "reader")]
            if not body.get("nextPageToken"):
                return sorted(out, key=lambda c: c["name"].lower())
            params = {**params, "pageToken": body["nextPageToken"]}

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            if not re.fullmatch(r"[^\s/]+", p["id"]):
                raise FormError("Pick Google calendars.")
            p["kind"] = "calendar"
        return out

    def _events(self, ctx, calendar, token):
        """(events, next sync token); (None, None) when the token is gone (410)."""
        params = {"maxResults": 250, "showDeleted": "true" if token else "false",
                  "singleEvents": "false"}
        if token:
            params["syncToken"] = token
        url = f"{API}/calendars/{urllib.parse.quote(calendar, safe='')}/events"
        events = []
        while True:
            r = self._get(ctx, url, params, ok=(410,))
            if r.status_code == 410:
                return None, None
            body = r.json() or {}
            events += body.get("items") or []
            if body.get("nextPageToken"):
                params = {**params, "pageToken": body["nextPageToken"]}
                continue
            return events, body.get("nextSyncToken")

    def changes(self, ctx, cursor):
        picks = (ctx.settings or {}).get(self.SETTINGS_KEY) or []
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        names = {p["id"]: p.get("name") or p["id"] for p in picks}
        state = json.loads(cursor) if cursor else {}
        sig = hashlib.sha256(json.dumps(sorted(names)).encode()).hexdigest()[:16]
        full = (ctx.full or not state or state.get("calendars") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        tokens = {} if full else dict(state.get("tokens") or {})
        if not full:                       # an expired token lists everything again
            fetched = {}
            for cal in sorted(names):
                try:
                    fetched[cal] = self._events(ctx, cal, tokens.get(cal))
                except NotFound:
                    fetched[cal] = ([], None)
                if fetched[cal][0] is None:
                    full, tokens = True, {}
                    break
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=WINDOW_DAYS)
        seen, n = set(), 0
        for cal in sorted(names):
            if full:
                try:
                    events, tokens[cal] = self._events(ctx, cal, None)
                except NotFound:
                    continue                # a calendar no longer shared with them
            else:
                events, tokens[cal] = fetched[cal]
            for e in events:
                sid = f"{cal}|{e['id']}"
                if e.get("status") == "cancelled" or _ended_before(e, cutoff):
                    if not full and sid in ctx.known:
                        yield Delete(sid)
                    continue
                if ctx.max_items and n >= ctx.max_items:
                    yield LimitReached(ctx.max_items)
                    yield Cursor(cursor)
                    return
                seen.add(sid)
                n += 1
                yield Upsert(Item(
                    source_id=sid, title=e.get("summary") or "(no title)", kind="event",
                    url=e.get("htmlLink") or "", version=e.get("updated") or "",
                    modified=e.get("updated"), mime="text/markdown", parent_id=cal,
                    content=_text(e, names[cal])))
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"calendars": sig, "tokens": tokens,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)


CONNECTOR = GoogleCalendar()
