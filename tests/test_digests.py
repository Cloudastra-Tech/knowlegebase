"""
Digests (platform.md PL-30; kb/services/digests.py, digests_store.py): the change
feed written by uploads, edits and connector syncs; subscriptions through the API;
each digest filtered for ITS recipient when it's sent (a restricted change never
reaches a public reader, whatever the workspace holds); summaries made once per
version of a document; the email's one-click unsubscribe; Slack DMs. The model
and the mailer are fakes; nothing is indexed.
"""

import re
import time
import uuid

import pytest

import digests_store
import integrations_store
import mailer
from connectors import base
from kb.services import digests, indexing, summaries

PASSWORD = "correct-horse"


@pytest.fixture
def ws(monkeypatch, tmp_path, jobs_paused):
    """A workspace (admin, HR, a public member) in a temp folder; the model writes
    "Summary <n> of <title>" and counts its calls; emails are kept, not sent."""
    import auth
    import members_store
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, docs=None, full=False: None)
    w = f"dg-{uuid.uuid4().hex[:8]}"
    for name, role in (("admin", "admin"), ("hr", "hr"), ("staff", "public"),
                       ("finance", "public")):
        members_store.set_password(f"{name}@{w}.example", name.title(),
                                   auth.hash_password(PASSWORD), w, role)
    calls = []

    def llm(prompt, max_tokens=400):
        calls.append(prompt)
        title = re.search(r'title="([^"]*)"', prompt)
        return f"Summary {len(calls)} of {title.group(1) if title else 'it'}"
    monkeypatch.setattr(summaries, "llm", llm)
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, template, **v: sent.append((to, template, v)))

    class W:
        name = w
        llm_calls = calls
        emails = sent
    return W


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app)


def login(client, w, name):
    r = client.post("/api/auth/login", json={"username": f"{name}@{w}.example",
                                             "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def upload(client, h, access, name, text, acl=None):
    r = client.post("/api/docs", headers=h, json={"name": name, "access": access,
                                                  "content": text, "acl": acl})
    assert r.status_code == 200, r.text


def subscribe(client, h, **body):
    r = client.post("/api/digests", headers=h, json={"frequency": "weekly", **body})
    assert r.status_code == 200, r.text
    return r.json()


def due_now(sub):
    """Make a subscription due, as if a week had passed (its window starts before
    the changes the test made)."""
    import db
    with db.connect() as conn:
        conn.execute("UPDATE digest_subscriptions SET next_due_at = 0, created_at = ? "
                     "WHERE id = ?", (time.time() - 3600, sub["id"]))
    return digests_store.subscription(sub["workspace"], sub["id"])


def email_to(w, name):
    got = [v for to, template, v in w.emails if to == f"{name}@{w.name}.example"]
    return got[-1] if got else None


# ---- The change feed ----------------------------------------------------------------------
def test_uploads_edits_and_deletes_go_in_the_change_feed(client, ws):
    h = login(client, ws.name, "admin")
    upload(client, h, "public", "leave.txt", "Twenty days of leave.")
    client.put("/api/docs/public/leave.txt", headers=h, json={"content": "Twenty-two days."})
    upload(client, h, "restricted", "pay.txt", "Teachers earn sixty thousand.")
    client.delete("/api/docs/restricted/pay.txt", headers=h)
    rows = digests_store.changes(ws.name, 0)
    assert [(r["doc_key"], r["change"], r["acl"]) for r in rows] == [
        ("public/leave.txt", "added", ["everyone"]),
        ("public/leave.txt", "modified", ["everyone"]),
        ("restricted/pay.txt", "added", ["group:hr"]),
        ("restricted/pay.txt", "removed", ["group:hr"])]
    assert all(r["actor"] == f"admin@{ws.name}.example" for r in rows)
    # Folded per document: added then edited is new; added then removed is nothing.
    folded = digests.fold(rows)
    assert {k: e["change"] for k, e in folded.items()} == {"public/leave.txt": "added"}


def test_a_sync_records_what_it_added_changed_and_removed(ws, monkeypatch):
    from connectors import registry
    from kb.services import sync
    from test_connector_sync import Fake
    connector = Fake()
    monkeypatch.setitem(registry.CONNECTORS, "fake", connector)
    integrations_store.connect(ws.name, "fake", "Fake account", {"token": "t"}, "admin")
    connector.add("a", "Alpha", "v1", "Alpha text")
    connector.add("b", "Beta", "v1", "Beta text")
    sync.sync(ws.name, "fake")
    connector.docs["a"] = ("Alpha", "v2", "Alpha, edited")
    connector.feed = [base.Upsert(connector.item("a")), base.Delete("b")]
    sync.sync(ws.name, "fake")
    rows = digests_store.changes(ws.name, 0)
    assert [(r["doc_key"], r["change"], r["title"], r["acl"]) for r in rows] == [
        ("connectors/fake/a.txt", "added", "Alpha", ["group:hr"]),
        ("connectors/fake/b.txt", "added", "Beta", ["group:hr"]),
        ("connectors/fake/a.txt", "modified", "Alpha", ["group:hr"]),
        ("connectors/fake/b.txt", "removed", "Beta", ["group:hr"])]


# ---- One digest per recipient -------------------------------------------------------------
def test_a_restricted_change_never_reaches_a_public_subscriber(client, ws):
    admin, staff = login(client, ws.name, "admin"), login(client, ws.name, "staff")
    staff_sub = subscribe(client, staff, channel="email")
    admin_sub = subscribe(client, admin, channel="email")
    upload(client, admin, "public", "leave.txt", "Twenty days of leave.")
    upload(client, admin, "restricted", "layoffs_2027.txt", "Plans for 2027.")
    # The admin's digest first, so the restricted summary exists when the public one is made.
    assert digests.send(due_now(admin_sub))["sent"]
    assert digests.send(due_now(staff_sub))["sent"]
    mine = email_to(ws, "admin")["body"]
    assert "leave.txt" in mine and "layoffs_2027.txt" in mine
    theirs = email_to(ws, "staff")
    assert "leave.txt" in theirs["body"]
    assert "layoffs" not in str(theirs)                    # not its title, nor its summary
    # Nor was the restricted document summarised for them: one summary per document.
    assert len(ws.llm_calls) == 2


def _group(ws, name, *people):
    import groups_store
    g = groups_store.create(ws.name, name, "", "admin")
    groups_store.set_members(ws.name, g["id"], [f"{p}@{ws.name}.example" for p in people],
                             "admin")
    return f"group:{g['id']}"


def test_a_group_restricted_change_never_reaches_a_non_member(client, ws):
    """A document shared with one group reaches its members only: not a public
    member outside it, and not HR either (the hr group isn't on its ACL)."""
    admin = login(client, ws.name, "admin")
    finance = _group(ws, "Finance", "finance")
    subs = {name: subscribe(client, login(client, ws.name, name), channel="email")
            for name in ("finance", "staff", "hr")}
    upload(client, admin, "restricted", "budget_cuts.txt", "Cut the travel budget.",
           acl=[finance])
    upload(client, admin, "public", "leave.txt", "Leave.")
    for name, sub in subs.items():
        assert digests.send(due_now(sub))["sent"], name
    assert "budget_cuts.txt" in email_to(ws, "finance")["body"]
    for outsider in ("staff", "hr"):
        got = email_to(ws, outsider)
        assert "leave.txt" in got["body"] and "budget" not in str(got), outsider
    # Its removal is news for the group only, too (the ACL it had is kept).
    ws.emails.clear()
    client.delete("/api/docs/restricted/budget_cuts.txt", headers=admin)
    for sub in subs.values():
        digests.send(due_now(sub))
    assert "budget_cuts.txt" in email_to(ws, "finance")["body"]
    assert email_to(ws, "staff") is None and email_to(ws, "hr") is None


def test_a_change_needs_every_acl_it_had_and_the_one_it_has_now(client, ws):
    staff = login(client, ws.name, "staff")
    sub = subscribe(client, staff, channel="email")
    # A synced file the connection shared with everyone when it changed, restricted since.
    integrations_store.connect(ws.name, "notion", "Notion", {"access_token": "t"}, "admin")
    integrations_store.update(ws.name, "notion", settings={"access": "everyone"})
    digests.record(ws.name, "connectors/notion/a.txt", "added", title="Board minutes")
    # An upload whose change was recorded for HR only (it was in restricted/ then).
    digests.record(ws.name, "public/moved.txt", "added", title="Moved", acl=["group:hr"])
    assert [e["title"] for e in digests.visible_changes(due_now(sub), _member(ws, "staff"),
                                                       time.time())] == ["Board minutes"]
    integrations_store.update(ws.name, "notion", settings={"access": "restricted"})
    assert digests.visible_changes(due_now(sub), _member(ws, "staff"), time.time()) == []


def test_a_member_turned_off_gets_nothing(client, ws):
    staff = login(client, ws.name, "staff")
    sub = subscribe(client, staff, channel="email")
    upload(client, login(client, ws.name, "admin"), "public", "leave.txt", "Leave.")
    import members_store
    members_store.set_disabled(f"staff@{ws.name}.example", True, ws.name)
    assert digests.send(due_now(sub)) == {"skipped": "not a member"}


def _member(ws, name):
    import members_store
    return members_store.get(f"{name}@{ws.name}.example")


def test_sources_and_folders_narrow_a_digest(client, ws):
    admin = login(client, ws.name, "admin")
    sub = subscribe(client, admin, channel="email", sources=["uploads"], folders=["restricted"])
    upload(client, admin, "public", "leave.txt", "Leave.")
    upload(client, admin, "restricted", "pay.txt", "Pay.")
    digests.record(ws.name, "connectors/notion/x.txt", "added", title="Wiki page",
                   acl=["everyone"])
    got = digests.build(due_now(sub), _member(ws, "admin"))
    assert [e["title"] for e in got["entries"]] == ["pay.txt"]


def test_an_empty_digest_isnt_sent_and_the_next_one_is_a_period_on(client, ws):
    staff = login(client, ws.name, "staff")
    sub = subscribe(client, staff, channel="email", frequency="daily")
    before = time.time()
    assert digests.send(due_now(sub)) == {"sent": False, "entries": 0}
    assert ws.emails == []
    after = digests_store.subscription(ws.name, sub["id"])
    assert after["next_due_at"] >= before + 86400 - 5


def test_summaries_are_made_once_per_version(client, ws):
    admin, hr = login(client, ws.name, "admin"), login(client, ws.name, "hr")
    a, b = subscribe(client, admin, channel="email"), subscribe(client, hr, channel="email")
    upload(client, admin, "public", "leave.txt", "Twenty days of leave.")
    digests.send(due_now(a))
    digests.send(due_now(b))
    assert len(ws.llm_calls) == 1                         # two digests, one summary
    assert "Summary 1 of leave.txt" in email_to(ws, "hr")["body"]
    client.put("/api/docs/public/leave.txt", headers=admin, json={"content": "Thirty days."})
    digests.send(due_now(a))
    assert len(ws.llm_calls) == 2                         # a new version: summarised again
    # The document reached the model as data (spotlighted), never as instructions.
    assert '<untrusted_data source="company_documents"' in ws.llm_calls[-1]


def test_due_subscriptions_are_queued_as_jobs(client, ws):
    from kb import jobs
    staff = login(client, ws.name, "staff")
    sub = due_now(subscribe(client, staff, channel="email"))
    upload(client, login(client, ws.name, "admin"), "public", "faq.txt", "Answers.")
    assert digests.queue_due()["queued"] >= 1
    runner = jobs.Runner(handlers={"digest": jobs.HANDLERS["digest"]}, threads=0)
    while runner.run_once():
        pass
    assert "faq.txt" in email_to(ws, "staff")["body"]
    assert digests_store.subscription(ws.name, sub["id"])["last_sent_at"]


# ---- Subscribing and unsubscribing --------------------------------------------------------
def test_people_manage_only_their_own_digests(client, ws):
    staff, hr = login(client, ws.name, "staff"), login(client, ws.name, "hr")
    sub = subscribe(client, staff, channel="email")
    got = client.get("/api/digests", headers=staff).json()
    assert [s["id"] for s in got["subscriptions"]] == [sub["id"]]
    assert got["channels"] == {"email": True, "slack": False}
    assert client.patch(f"/api/digests/{sub['id']}", headers=hr,
                        json={"frequency": "daily"}).status_code == 404
    assert client.delete(f"/api/digests/{sub['id']}", headers=hr).status_code == 404
    r = client.patch(f"/api/digests/{sub['id']}", headers=staff, json={"frequency": "daily"})
    assert r.json()["frequency"] == "daily"
    assert client.delete(f"/api/digests/{sub['id']}", headers=staff).status_code == 200
    assert client.get("/api/digests", headers=staff).json()["subscriptions"] == []


def test_what_isnt_allowed_is_refused(client, ws):
    staff = login(client, ws.name, "staff")
    integrations_store.connect(ws.name, "notion", "Notion", {"access_token": "t"}, "admin")
    sources = [s["id"] for s in client.get("/api/digests", headers=staff).json()["sources"]]
    assert sources == ["uploads"]                         # Notion is HR-only here
    post = lambda **b: client.post("/api/digests", headers=staff,  # noqa: E731
                                   json={"frequency": "weekly", "channel": "email", **b})
    assert post(sources=["notion"]).status_code == 400
    assert post(channel="slack").status_code == 400        # the Slack app isn't installed
    assert post(frequency="hourly").status_code == 400
    assert post(channel="pigeon").status_code == 400


def test_the_email_link_unsubscribes_in_one_click(client, ws):
    staff = login(client, ws.name, "staff")
    sub = subscribe(client, staff, channel="email")
    upload(client, login(client, ws.name, "admin"), "public", "leave.txt", "Leave.")
    digests.send(due_now(sub))
    link = email_to(ws, "staff")["unsubscribe_link"]
    token = link.rsplit("/unsubscribe/", 1)[1]
    assert digests_store.by_token(token)["id"] == sub["id"]
    r = client.post("/api/digests/unsubscribe", json={"token": token})    # no sign-in
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert sub["id"] not in [s["id"] for s in digests_store.due()]
    import audit_store
    (event,) = [e for e in audit_store.list_events(ws.name, limit=50)[0]
                if e["action"] == "digest.unsubscribe"]
    assert event["actor"] == f"staff@{ws.name}.example"
    # It can be undone from the same page; a made-up token does nothing.
    assert client.post("/api/digests/resubscribe", json={"token": token}).json()["enabled"]
    assert client.post("/api/digests/unsubscribe", json={"token": "nope"}).status_code == 404


# ---- Slack ---------------------------------------------------------------------------------
def test_a_slack_digest_is_a_dm_to_the_member(client, ws, monkeypatch):
    import slack_store
    from kb.services import slack
    posted = []

    def call(method, token=None, **args):
        posted.append((method, args))
        if method == "users.lookupByEmail":
            return {"user": {"id": "U1", "team_id": "T1"}}
        return {"ok": True}
    monkeypatch.setattr(slack, "call", call)
    monkeypatch.setattr(slack, "dm_channel", lambda team, token, user: f"D-{user}")
    monkeypatch.setattr(slack_store, "get", lambda w: {"status": "installed", "team_id": "T1"})
    monkeypatch.setattr(slack_store, "bot_token", lambda w: "xoxb-test")
    staff = login(client, ws.name, "staff")
    sub = subscribe(client, staff, channel="slack")
    upload(client, login(client, ws.name, "admin"), "public", "leave.txt", "Leave.")
    upload(client, login(client, ws.name, "admin"), "restricted", "pay.txt", "Pay.")
    assert digests.send(due_now(sub))["sent"]
    assert posted[0] == ("users.lookupByEmail", {"email": f"staff@{ws.name}.example"})
    method, args = posted[-1]
    assert (method, args["channel"]) == ("chat.postMessage", "D-U1")
    assert "leave.txt" in args["text"] and "pay.txt" not in args["text"]
    assert ws.emails == []


# ---- The workspace's purge ------------------------------------------------------------------
def test_a_purged_workspace_leaves_nothing_in_these_tables(client, ws):
    import os
    import db
    import drafts_store
    import secret_box
    import webhooks_store
    from kb.services import lifecycle
    admin = login(client, ws.name, "admin")
    sub = subscribe(client, admin, channel="email")
    upload(client, admin, "public", "leave.txt", "Leave.")
    digests.send(due_now(sub))                           # a summary, cached
    draft = drafts_store.create(ws.name, "summary", "Summary", {"provider": "uploads"},
                                f"admin@{ws.name}.example")
    drafts_store.write_text(ws.name, draft["id"], "Draft text")
    hook = webhooks_store.create(ws.name, "https://hooks.example/x", ["audit.event"], None,
                                 "admin", "whsec_secret")
    webhooks_store.add_delivery(ws.name, hook["id"], "audit.event", "evt_1", "{}")
    tables = ("doc_changes", "digest_subscriptions", "doc_summaries", "drafts", "webhooks",
              "webhook_deliveries")

    def counts():
        with db.connect() as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE workspace = ?",
                                    (ws.name,)).fetchone()[0] for t in tables}
    assert all(counts().values())
    with db.connect() as conn:
        sealed = bytes(conn.execute("SELECT secret FROM webhooks WHERE id = ?",
                                    (hook["id"],)).fetchone()[0])
    lifecycle.purge(ws.name)
    assert counts() == dict.fromkeys(tables, 0)
    assert not os.path.exists(drafts_store.folder(ws.name))
    with pytest.raises(secret_box.Shredded):
        secret_box.unseal(sealed, ws.name)
