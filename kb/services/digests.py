"""
Digests: "what changed this week" (platform.md PL-30), by email or Slack DM.

THE CHANGE FEED. record() is called where documents change: an upload, an edit or
a delete on the Documents screen, a published draft, and every connector sync
(kb/services/sync.py: a new copy, a new version, a skipped or deleted item). Each
row keeps the document's title and the ACL (access.py) it had at that moment.

SENDING. The hourly `digests_due` job queues a `digest` job per subscription
that's due; that job builds the digest for ONE recipient:
  1. the member is looked up NOW (a member removed or turned off gets nothing);
  2. the changes since their last digest are folded per document (added then
     edited is "added"; added then removed is nothing);
  3. each document is kept only if THIS member's principals (their groups, the
     hr group, themselves: groups_store.principals_for, looked up now) meet EVERY
     ACL it had in the window AND the one it has now (a removed one: the ones it
     had). A document limited to a group at any point never reaches someone
     outside it, and is never summarised into their digest;
  4. what's left is summarised (summaries.summarise_doc: cached per version)
     and sent. Filtering is per recipient, never per workspace.
An empty digest isn't sent. Every email has a one-click unsubscribe link.
"""

import logging
import os
import time

import access
import digests_store
import integrations_store
import mailer
import members_store
import slack_store
import workspaces
from connectors import registry
from kb import jobs
from kb.services import audience, summaries

log = logging.getLogger("uvicorn.error")

DUE_EVERY = 60 * 60            # how often due subscriptions are looked for
MAX_ENTRIES = 50               # documents in one digest (the rest: "and N more")
MAX_SUMMARIES = 20             # of which this many get a summary (model calls)
CHANGE_LABELS = {"added": "New", "modified": "Updated", "removed": "Removed"}


def public_url():
    return (os.getenv("PUBLIC_URL") or "http://localhost:5180").rstrip("/")


def source_label(provider):
    return "Company documents" if provider == audience.UPLOADS else registry.label(provider)


# ---- The change feed ------------------------------------------------------------------
def record(ws, doc_key, change, *, title=None, folder=None, actor=None, acl=None,
           version=None):
    """Note a change to a document (never fails what changed it). acl: who may
    read it now (access.py), if the caller knows; else it's looked up."""
    try:
        provider = audience.provider_of(doc_key)
        if folder is None and provider == audience.UPLOADS:
            folder = doc_key.split("/", 1)[0]
        acl = acl or audience.doc_acl_now(ws, doc_key)
        digests_store.record_change(
            ws, doc_key, change, provider=provider, folder=folder,
            title=title or os.path.basename(doc_key), version=version, actor=actor,
            acl=acl, access=access.level_of_acl(acl))
    except Exception:
        log.exception("Digests: couldn't record a change to %s in workspace %s", doc_key, ws)


# ---- One recipient's digest -----------------------------------------------------------
def fold(rows):
    """{doc_key: entry} of the feed's rows: one change per document (see the top),
    with every ACL it had in the window (acls)."""
    out = {}
    for r in rows:
        e = out.get(r["doc_key"])
        if e is None:
            out[r["doc_key"]] = {"doc_key": r["doc_key"], "provider": r["provider"],
                                 "folder": r["folder"], "title": r["title"],
                                 "first": r["change"], "change": r["change"],
                                 "acls": [r["acl"]], "at": r["at"]}
            continue
        e.update(change=r["change"], at=r["at"], title=r["title"] or e["title"],
                 folder=r["folder"] or e["folder"])
        if r["acl"] not in e["acls"]:
            e["acls"].append(r["acl"])
    folded = {}
    for key, e in out.items():
        if e["first"] == "added" and e["change"] == "removed":
            continue                                  # came and went
        change = ("added" if e["first"] == "added" else
                  "removed" if e["change"] == "removed" else "modified")
        folded[key] = {**e, "change": change}
    return folded


def visible_changes(sub, member, until):
    """The folded changes since the subscription's last digest that `member` may
    read (every ACL it had then, and the one it has now), newest first."""
    ws = sub["workspace"]
    since = sub["last_sent_at"] or sub["created_at"]
    reader = audience.viewer_of(member)
    if reader is None:
        return []
    now = audience.Acls(ws)
    out = []
    for e in fold(digests_store.changes(ws, since, until)).values():
        if sub["sources"] and e["provider"] not in sub["sources"]:
            continue
        if sub["folders"] and (e["folder"] or "") not in sub["folders"]:
            continue
        acls = e["acls"] + ([] if e["change"] == "removed" else [now.now(e["doc_key"])])
        if not audience.may_read(reader, *acls):
            continue
        out.append(e)
    out.sort(key=lambda e: -e["at"])
    return out


def build(sub, member, until=None):
    """The digest a subscription's member gets now: {entries, more}. Each entry:
    {doc_key, title, source, change, summary}."""
    until = time.time() if until is None else until
    found = visible_changes(sub, member, until)
    entries = []
    for n, e in enumerate(found[:MAX_ENTRIES]):
        summary = None
        if e["change"] != "removed" and n < MAX_SUMMARIES:
            try:
                summary = summaries.summarise_doc(sub["workspace"], e["doc_key"], e["title"])
            except Exception:
                log.exception("Digests: couldn't summarise %s", e["doc_key"])
        entries.append({"doc_key": e["doc_key"], "title": e["title"] or e["doc_key"],
                        "source": source_label(e["provider"]), "change": e["change"],
                        "summary": summary})
    return {"entries": entries, "more": max(0, len(found) - MAX_ENTRIES)}


def as_text(digest, bullet="•"):
    lines = []
    for e in digest["entries"]:
        lines.append(f"{bullet} {CHANGE_LABELS[e['change']]}: {e['title']} ({e['source']})")
        if e["summary"]:
            lines.append(f"  {e['summary']}")
    if digest["more"]:
        lines.append(f"…and {digest['more']} more.")
    return "\n".join(lines)


def _period(sub):
    return "today" if sub["frequency"] == "daily" else "this week"


def send_email(sub, member, digest):
    ws = sub["workspace"]
    count = len(digest["entries"]) + digest["more"]
    token = digests_store.unsubscribe_token(sub)
    name = members_store.workspace_name(ws) or ws
    mailer.send(member["username"], "digest",
                title=f"{count} document change{'s' if count != 1 else ''} in {name} "
                      f"{_period(sub)}",
                intro="What changed in the documents you follow, in documents you can read.",
                body=as_text(digest),
                manage_link=f"{public_url()}/digests",
                unsubscribe_link=f"{public_url()}/unsubscribe/{token}")


class NotDelivered(Exception):
    """The digest couldn't go where it was meant to (the message says why)."""


def send_slack(sub, member, digest):
    """A DM from the workspace's Slack app to the member (found by their email)."""
    from kb.services import slack
    ws = sub["workspace"]
    install, token = slack_store.get(ws), slack_store.bot_token(ws)
    if not install or not token:
        raise NotDelivered("The Slack app isn't installed in this workspace.")
    try:
        found = slack.call("users.lookupByEmail", token, email=member["username"])["user"]
        if found.get("deleted") or found.get("is_bot") or (
                found.get("team_id") and found["team_id"] != install["team_id"]):
            raise NotDelivered("Slack has no matching account for this member.")
        channel = slack.dm_channel(install["team_id"], token, found["id"])
        count = len(digest["entries"]) + digest["more"]
        text = (f"*{count} document change{'s' if count != 1 else ''} {_period(sub)}*\n"
                + slack._escape(as_text(digest)) +
                f"\n\n<{public_url()}/digests|Manage your digests>")
        slack.call("chat.postMessage", token, channel=channel, text=text, unfurl_links=False,
                   unfurl_media=False)
    except slack.SlackError as e:
        raise NotDelivered(f"Slack refused the message ({e.code}).") from None


def send(sub, now=None):
    """Build and send one subscription's digest, then move it on a period.
    Returns what happened."""
    now = time.time() if now is None else now
    member = members_store.get(sub["username"])
    if not member or member["workspace"] != sub["workspace"] or member["disabled"]:
        digests_store.mark_sent(sub, now, error="Not a member of the workspace now.")
        return {"skipped": "not a member"}
    digest = build(sub, member, now)
    if not digest["entries"]:
        digests_store.mark_sent(sub, now)
        return {"sent": False, "entries": 0}
    try:
        (send_slack if sub["channel"] == "slack" else send_email)(sub, member, digest)
    except (NotDelivered, mailer.MailError) as e:
        log.warning("Digests: subscription %s wasn't delivered: %s", sub["id"], e)
        digests_store.mark_sent(sub, now, error=str(e)[:300])
        return {"sent": False, "error": str(e)[:300]}
    digests_store.mark_sent(sub, now)
    return {"sent": True, "entries": len(digest["entries"]), "channel": sub["channel"]}


# ---- Jobs -----------------------------------------------------------------------------
@jobs.periodic("digests_due", lambda: DUE_EVERY)
def queue_due(job=None, now=None):
    """Queue a digest job for every subscription that's due; prune the old feed."""
    due = digests_store.due(now)
    for sub in due:
        jobs.enqueue("digest", sub["workspace"], {"id": sub["id"]},
                     lane=f"digest:{sub['workspace']}", dedupe_key=f"digest:{sub['id']}")
    pruned = digests_store.prune_changes(now)
    return {"queued": len(due), "pruned": pruned}


@jobs.handler("digest", max_attempts=2)
def run_digest(job):
    sub = digests_store.subscription(job.workspace, job.payload["id"])
    if not sub or not sub["enabled"] or sub["next_due_at"] > time.time():
        return {"skipped": "not due"}           # unsubscribed, or sent meanwhile
    return send(sub)


# ---- What a member may subscribe to ---------------------------------------------------
def sources_for(ws, member):
    """[{id, label}] of the sources a member may follow: uploaded files, and each
    connection whose files may be for them (a source-policy connection's are, file
    by file; an HR-only one's only for HR). Each file is still checked when sent."""
    out = [{"id": audience.UPLOADS, "label": source_label(audience.UPLOADS)}]
    reader = audience.viewer_of(member)
    for row in sorted(integrations_store.list_for(ws), key=lambda r: r["provider"]):
        policy = integrations_store.policy_of(row)
        level = "public" if policy == "workspace" else "restricted"
        if policy == "source" or audience.may_read(reader, access.level_acl(level)):
            out.append({"id": row["provider"], "label": source_label(row["provider"])})
    return out


def channels_for(ws):
    """Which channels can deliver here: email always, Slack once the app is installed."""
    install = slack_store.get(ws)
    return {"email": True, "slack": bool(install and install["status"] == "installed")}


def has_docs_root(ws):
    return os.path.isdir(workspaces.docs_root(ws))
