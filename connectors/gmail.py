"""
connectors/gmail.py  —  a member's OWN Gmail, for them alone (a personal connector:
connectors/google_personal.py, kb/routers/my_integrations.py).

Opt-in twice: the operator turns it on for the server (GMAIL_SYNC=on, after
Google's restricted-scope review: see google_personal.py), then each member
connects their own mailbox and picks the LABELS to sync; nothing is synced
until they pick one. Spam, trash, drafts and chats are never offered. Only
threads from the last WINDOW_DAYS are synced.

Read-only (gmail.readonly). What is synced: each THREAD with a picked label, as
one Markdown document: its subject, then every message's sender, recipients,
date and text (text/plain, else the HTML's text; attachments aren't read).

Everything synced is for the member who connected it: its files carry
user:<them> (kb/services/permissions.py personal_acl); the workspace's admins,
policies, digests, webhooks, Insights and the knowledge listing never see it.

How a sync finds what changed:
  FULL (first run, the picks changed, every RECONCILE seconds, a history id
  Gmail no longer has, asked for)
      users.getProfile's historyId is taken first (nothing changed meanwhile is
      lost), then threads.list per picked label (q newer_than:<WINDOW_DAYS>d);
      a thread known but not listed is deleted (behind the mass-delete guard).
  INCREMENTAL
      users.history.list from the stored historyId: every thread with a message
      added or deleted, or a label added or removed, is looked at again
      (threads.get, minimal): still carrying a picked label -> upserted with its
      new historyId; not any more, trashed or gone (404) -> deleted. A 404 on
      history.list (the id is too old: "typically valid for at least a week")
      means a full sync.

Quota: 6,000 units a minute per user (threads.get with format=full costs the
most); 429 and 403 rateLimitExceeded are retried by connectors/http.py. Calls
are spaced at RATE a second per member.

API references (checked 2026-10-27):
  scopes         https://developers.google.com/workspace/gmail/api/auth/scopes (gmail.readonly:
                 restricted)
  labels         https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/list
  threads        https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/list
                 (labelIds, q, maxResults, pageToken; id, snippet, historyId),
                 .../users.threads/get (format full | metadata | minimal)
  messages       https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages
                 (labelIds, payload.headers, payload.parts, body.data base64url)
  profile        https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile
  history        https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
                 (startHistoryId; messagesAdded | messagesDeleted | labelsAdded | labelsRemoved;
                 404: full sync), https://developers.google.com/workspace/gmail/api/guides/sync
  quota          https://developers.google.com/workspace/gmail/api/reference/quota
  errors         https://developers.google.com/workspace/gmail/api/guides/handle-errors
"""

import base64
import hashlib
import html
import json
import re
import time

from connectors.base import (Cursor, Delete, Fetched, FormError, Item, LimitReached, NotFound,
                             Upsert)
from connectors.google_personal import GooglePersonal

PROVIDER = "gmail"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
WINDOW_DAYS = 180
RECONCILE = 24 * 60 * 60
NEVER = {"SPAM", "TRASH", "DRAFT", "CHAT"}       # labels never offered, threads never synced
SYSTEM = {"INBOX": "Inbox", "SENT": "Sent", "IMPORTANT": "Important", "STARRED": "Starred"}


def _decode(data):
    try:
        return base64.urlsafe_b64decode((data or "") + "=" * (-len(data or "") % 4)).decode(
            "utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _body(payload):
    """A message's text: its text/plain part, else its text/html part's text."""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType") or ""
        data = (part.get("body") or {}).get("data")
        if data and not (part.get("filename") or ""):
            (plain if mime == "text/plain" else rich if mime == "text/html" else []).append(
                _decode(data))
        for sub in part.get("parts") or ():
            walk(sub)

    walk(payload or {})
    if plain:
        return "\n".join(plain).strip()
    text = re.sub(r"<\s*(br|/p|/div|/tr|/li)\s*/?>", "\n", "\n".join(rich), flags=re.I)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.I | re.S)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def _headers(m):
    return {h.get("name", "").lower(): h.get("value", "")
            for h in (m.get("payload") or {}).get("headers") or ()}


class Gmail(GooglePersonal):
    PROVIDER = PROVIDER
    LABEL = "Gmail"
    DESCRIPTION = "Your own email, from the labels you pick. Only you can ask about it."
    SCOPE = SCOPE
    SWITCH = "GMAIL_SYNC"
    UNIT = "threads"
    NOTE = ("Only you can ask about your mail: it never reaches anyone else in the "
            "workspace, admins included. Nothing is synced until you pick labels.")

    # ---- picking ------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        labels = (self._get(ctx, f"{API}/labels").json() or {}).get("labels") or []
        out = []
        for lb in labels:
            if lb.get("id") in NEVER or lb.get("id", "").startswith("CATEGORY_"):
                continue
            if lb.get("type") == "system" and lb.get("id") not in SYSTEM:
                continue
            out.append({"id": lb["id"], "name": SYSTEM.get(lb["id"]) or lb.get("name") or lb["id"],
                        "kind": "label"})
        return sorted(out, key=lambda c: (c["id"] not in SYSTEM, c["name"].lower()))

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            if p["id"] in NEVER or not re.fullmatch(r"[A-Za-z0-9_]+", p["id"]):
                raise FormError("Pick Gmail labels (not spam, trash, drafts or chats).")
            p["kind"] = "label"
        return out

    # ---- syncing ------------------------------------------------------------------
    def _threads(self, ctx, label):
        params = {"labelIds": label, "q": f"newer_than:{WINDOW_DAYS}d", "maxResults": 100}
        while True:
            body = self._get(ctx, f"{API}/threads", params).json() or {}
            yield from body.get("threads") or []
            if not body.get("nextPageToken"):
                return
            params = {**params, "pageToken": body["nextPageToken"]}

    def _history(self, ctx, start):
        """Thread ids touched since `start`, and the newest history id; None when
        Gmail no longer has `start` (404: a full sync)."""
        touched, params, newest = set(), {"startHistoryId": start, "maxResults": 500}, start
        while True:
            r = self._get(ctx, f"{API}/history", params, ok=(404,))
            if r.status_code == 404:
                return None, None
            body = r.json() or {}
            newest = body.get("historyId") or newest
            for h in body.get("history") or ():
                for kind in ("messagesAdded", "messagesDeleted", "labelsAdded", "labelsRemoved"):
                    for entry in h.get(kind) or ():
                        tid = (entry.get("message") or {}).get("threadId")
                        if tid:
                            touched.add(tid)
            if not body.get("nextPageToken"):
                return touched, newest
            params = {**params, "pageToken": body["nextPageToken"]}

    def changes(self, ctx, cursor):
        picks = sorted(p["id"] for p in (ctx.settings or {}).get(self.SETTINGS_KEY) or [])
        if not picks:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        state = json.loads(cursor) if cursor else {}
        sig = hashlib.sha256(json.dumps(picks).encode()).hexdigest()[:16]
        full = (ctx.full or not state.get("history") or state.get("labels") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        if not full:
            touched, newest = self._history(ctx, state["history"])
            if touched is None:
                full = True
        n = 0
        if full:
            newest = (self._get(ctx, f"{API}/profile").json() or {}).get("historyId")
            seen = set()
            for label in picks:
                for t in self._threads(ctx, label):
                    if t["id"] in seen:
                        continue
                    if ctx.max_items and n >= ctx.max_items:
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)
                        return
                    seen.add(t["id"])
                    n += 1
                    yield Upsert(self._item(t["id"], t.get("historyId"), t.get("snippet")))
            yield from ctx.missing(seen)
        else:
            for tid in sorted(touched):
                try:
                    t = self._get(ctx, f"{API}/threads/{tid}", {"format": "minimal"}).json() or {}
                except NotFound:
                    t = {}
                labels = {lb for m in t.get("messages") or () for lb in m.get("labelIds") or ()}
                live = [m for m in t.get("messages") or ()
                        if not NEVER & set(m.get("labelIds") or ())]
                if live and labels & set(picks):
                    if ctx.max_items and n >= ctx.max_items:
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)
                        return
                    n += 1
                    yield Upsert(self._item(tid, t.get("historyId"), t.get("snippet")))
                elif tid in ctx.known:
                    yield Delete(tid)
        yield Cursor(json.dumps({"labels": sig, "history": newest,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    @staticmethod
    def _item(tid, history, snippet):
        return Item(source_id=tid, title=(snippet or "Email")[:80], kind="email",
                    url=f"https://mail.google.com/mail/u/0/#all/{tid}", version=str(history or ""),
                    mime="text/markdown", attrs={"thread": tid})

    def fetch(self, ctx, item):
        t = self._get(ctx, f"{API}/threads/{item.source_id}", {"format": "full"}).json() or {}
        messages = [m for m in t.get("messages") or ()
                    if not NEVER & set(m.get("labelIds") or ())]
        if not messages:
            return None
        subject = _headers(messages[0]).get("subject") or "(no subject)"
        parts = [f"# {subject}"]
        for m in messages:
            h = _headers(m)
            lines = [f"From: {h.get('from', '')}", f"To: {h.get('to', '')}"]
            if h.get("cc"):
                lines.append(f"Cc: {h['cc']}")
            lines.append(f"Date: {h.get('date', '')}")
            parts.append("## " + (h.get("subject") or subject) + "\n\n" + "\n".join(lines)
                         + "\n\n" + _body(m.get("payload")))
        item.title = subject[:200]
        return Fetched("\n\n".join(parts), "text/markdown")


CONNECTOR = Gmail()
