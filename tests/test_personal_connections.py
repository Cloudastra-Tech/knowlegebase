"""
THE LEAK SUITE FOR PERSONAL CONNECTIONS (Gmail, Google Calendar:
kb/routers/my_integrations.py, connectors/google_personal.py). A member's own
mail and events are theirs alone: never searched, cited, listed, counted,
digested or webhooked for anyone else in the workspace, admins and HR
included; no admin endpoint reaches them; removing or erasing the member, or
purging the workspace, revokes the token and deletes the copies.

Real index (test_indexing.env), real agent, a model that cites everything it
is shown (spaces_fixtures.Citing): what's cited is what the search let it read.
Google answers from recorded HTTP (tests/fixtures/connectors/gmail/).
"""

import urllib.parse

import pytest

from connector_fixtures import Recorded
from spaces_fixtures import ask, citing, new_chat  # noqa: F401  (citing: a fixture)
from test_indexing import env  # noqa: F401  (a fixture)

TOKEN = "https://oauth2.googleapis.com/token"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
GM = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
LABEL = {"id": "Label_7", "name": "HR", "kind": "label"}


@pytest.fixture(autouse=True)
def google(monkeypatch):
    from kb.services import sync
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("GMAIL_SYNC", "on")
    monkeypatch.setenv("CALENDAR_SYNC", "on")
    monkeypatch.setattr(sync, "start_sync", lambda *a, **kw: None)   # the tests sync themselves


def connect_gmail(env, rec, headers, email):  # noqa: F811
    """Connect a member's own Gmail through the routes; returns the redirect."""
    r = env.client.post("/api/me/integrations/gmail/connect", headers=headers)
    assert r.status_code == 200, r.text
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.json()["url"]).query))
    assert q["redirect_uri"].endswith("/api/integrations/gmail/callback")
    assert GMAIL_SCOPE in q["scope"] and q["access_type"] == "offline"
    rec.add("POST", TOKEN, json_body={"access_token": "g-at", "refresh_token": "g-rt",
                                      "expires_in": 3599,
                                      "scope": f"openid {GMAIL_SCOPE} email"})
    rec.add("GET", USERINFO, json_body={"email": email})
    r = env.client.get("/api/integrations/gmail/callback", follow_redirects=False,
                       params={"state": q["state"], "code": "abc"})
    return r.headers["location"]


def staff_with_mail(env, monkeypatch):  # noqa: F811
    """A workspace whose staff member synced their HR label; (w, admin, staff, key)."""
    from connectors import registry
    from kb.services import sync
    w = env.workspace(("public", "handbook.txt", "# Handbook\n\nThe office opens at nine."))
    admin, staff = env.login(w, "admin"), env.login(w, "staff")
    rec = Recorded(monkeypatch)
    assert connect_gmail(env, rec, staff, f"staff@{w}.example") == (
        "/#connections?connected=gmail&choose=1")
    rec.add("GET", f"{GM}/labels", json_body={"labels": [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "SPAM", "name": "SPAM", "type": "system"},
        {"id": "CATEGORY_SOCIAL", "name": "CATEGORY_SOCIAL", "type": "system"},
        {"id": "Label_7", "name": "HR", "type": "user"}]})
    picks = env.client.get("/api/me/integrations/gmail/containers", headers=staff).json()
    assert [i["id"] for i in picks["items"]] == ["INBOX", "Label_7"]
    r = env.client.put("/api/me/integrations/gmail/containers", headers=staff,
                       json={"containers": [LABEL]})
    assert r.status_code == 200, r.text
    key = registry.personal_key("gmail", f"staff@{w}.example")
    rec.load("gmail/full")
    summary = sync.sync(w, key)
    assert (summary["upserted"], rec.unused()) == (2, [])
    env.wait(w)
    return w, admin, staff, key, rec


def test_a_members_mail_is_searched_and_cited_for_them_alone(env, citing, monkeypatch):  # noqa: F811
    import access
    w, admin, staff, key, _ = staff_with_mail(env, monkeypatch)
    # Indexed, tagged for the member alone (no access level opens it).
    chunks = [m for m in __import__("ingest")._store(
        __import__("kb.services.indexing", fromlist=["x"]).current(w)[0])._collection.get(
        include=["metadatas"])["metadatas"] if m["doc_key"].startswith(f"connectors/{key}/")]
    assert chunks and all(m["acl"] == access.user(f"staff@{w}.example") or
                          m["acl"] == [access.user(f"staff@{w}.example")] for m in chunks)
    assert all(m["access"] == "restricted" for m in chunks)

    done = ask(env.client, staff, "What is Bob's salary?", new_chat(env.client, staff))
    mine = [s for s in done["sources"] if s["title"] == "Offer letter for Bob"]
    assert mine, done["sources"]

    # The admin, viewing as HR: not searched, not cited, not the passage.
    done = ask(env.client, admin, "What is Bob's salary in the offer letter?",
               new_chat(env.client, admin))
    assert not [s for s in done["sources"] if "Offer letter" in (s.get("title") or "")]
    assert "90,000" not in done["answer"]
    assert env.client.get(f"/api/passages/{mine[0]['chunk_id']}",
                          headers=admin).status_code == 404
    assert env.client.get(f"/api/passages/{mine[0]['chunk_id']}",
                          headers=staff).status_code == 200


def test_no_admin_route_lists_or_reaches_a_members_connection(env, monkeypatch):  # noqa: F811
    w, admin, staff, key, _ = staff_with_mail(env, monkeypatch)
    titles = lambda h: {i["title"] for i in env.client.get(  # noqa: E731
        "/api/knowledge", headers=h).json()["items"]}
    assert "Offer letter for Bob" in titles(staff)
    assert "Offer letter for Bob" not in titles(admin) and "handbook.txt" in titles(admin)
    cards = env.client.get("/api/integrations", headers=admin).json()["integrations"]
    assert not [c for c in cards if c["id"] in ("gmail", "google_calendar")
                or "~" in c["id"]]
    for path in (f"/api/integrations/{key}/runs", f"/api/integrations/{key}/containers",
                 "/api/integrations/gmail/runs", "/api/integrations/gmail/containers"):
        assert env.client.get(path, headers=admin).status_code == 404, path
    for method, path in (("delete", f"/api/integrations/{key}"),
                         ("delete", "/api/integrations/gmail"),
                         ("post", f"/api/integrations/{key}/sync"),
                         ("put", f"/api/integrations/{key}/policy")):
        r = getattr(env.client, method)(path, headers=admin,
                                        **({"json": {"policy": "workspace"}}
                                           if method == "put" else {}))
        assert r.status_code == 404, path
    # The admin's own My connections is theirs: Gmail not connected.
    mine = env.client.get("/api/me/integrations", headers=admin).json()["integrations"]
    gmail = next(i for i in mine if i["id"] == "gmail")
    assert (gmail["status"], gmail["account"], gmail["doc_count"]) == ("disconnected", None, 0)
    staff_view = env.client.get("/api/me/integrations", headers=staff).json()["integrations"]
    gmail = next(i for i in staff_view if i["id"] == "gmail")
    assert (gmail["status"], gmail["doc_count"], gmail["personal"]) == ("connected", 2, True)
    assert gmail["containers"] == [LABEL]
    # Nor the audit log: no mailbox address, no label names.
    events = env.client.get("/api/admin/audit", headers=admin).json()
    assert {(e["action"], str(e["details"])) for e in events["events"]
            if e["action"].startswith("integration.")} >= {
        ("integration.connect", "{'personal': True}"),
        ("integration.folders", "{'personal': True, 'picked': 1}")}
    assert f"staff@{w}.example" not in str([e.get("details") for e in events.get("events", [])])
    assert "HR" not in str([e.get("details") for e in events.get("events", [])
                            if e.get("action") == "integration.folders"])


def test_insights_digests_webhooks_and_billing_never_see_it(env, citing, monkeypatch):  # noqa: F811
    import integrations_store
    from kb.services import billing, digests, indexing, insights, question_log, webhooks
    w, admin, staff, key, _ = staff_with_mail(env, monkeypatch)
    ask(env.client, staff, "What is Bob's salary?", new_chat(env.client, staff))
    # The question log (Insights) keeps no source from anyone's own mail.
    assert question_log._sources([{"doc_key": f"connectors/{key}/t.txt", "title": "Offer"},
                                  {"doc_key": "public/handbook.txt", "title": "handbook"}]) == [
        {"doc_key": "public/handbook.txt", "title": "handbook"}]
    assert not [d for d in insights.current_docs(w) if key in str(d)]
    assert not [h for h in insights.connector_health(w) if h.get("provider") == key]
    assert all(s["id"] != key for s in digests.sources_for(w, {"username": f"admin@{w}.example",
                                                              "role": "admin", "workspace": w}))
    assert key not in billing.connectors(w)
    sent = []
    monkeypatch.setattr(webhooks, "emit", lambda ws, event, body: sent.append(body))
    indexing._tell_webhooks(w, [f"connectors/{key}/x.txt"], {"added": 1})
    assert sent == []
    # A policy can't open it up, even written straight into its settings.
    row = integrations_store.get(w, key)
    integrations_store.update(w, key, settings=dict(row["settings"], policy="workspace"))
    from kb.services import permissions
    acls = permissions.connector_acls(w)
    assert {tuple(a) for k, a in acls.items() if key in k} == {(f"user:staff@{w}.example",)}


def test_another_member_can_not_reach_it_through_my_connections(env, monkeypatch):  # noqa: F811
    import auth
    import members_store
    w, admin, staff, key, _ = staff_with_mail(env, monkeypatch)
    members_store.set_password(f"eve@{w}.example", "Eve", auth.hash_password("correct-horse"),
                               w, "public")
    eve = env.login(w, "eve")
    mine = env.client.get("/api/me/integrations", headers=eve).json()["integrations"]
    assert next(i for i in mine if i["id"] == "gmail")["status"] == "disconnected"
    assert env.client.get("/api/me/integrations/gmail/containers",
                          headers=eve).status_code == 400
    assert env.client.post("/api/me/integrations/gmail/sync", headers=eve).status_code == 400
    assert env.client.delete("/api/me/integrations/gmail", headers=eve).status_code == 200
    import integrations_store
    assert integrations_store.get(w, key) is not None       # Eve's "disconnect" was her own
    assert env.client.get(f"/api/me/integrations/{key}/containers",
                          headers=eve).status_code == 404
    # Signed out: nothing.
    assert env.client.get("/api/me/integrations").status_code == 401


def test_disconnecting_removing_or_erasing_the_member_deletes_it(env, monkeypatch):  # noqa: F811
    import integrations_store
    import sync_store
    w, admin, staff, key, rec = staff_with_mail(env, monkeypatch)
    # A data export of the member says which of their own they connected (no content).
    from kb.services import dsar
    exported = dsar.export_member(w, f"staff@{w}.example")["personal_connections"]
    assert [(c["provider"], c["items"]) for c in exported] == [("gmail", 2)]
    rec.add("POST", "https://oauth2.googleapis.com/revoke")
    r = env.client.delete(f"/api/admin/members/staff@{w}.example", headers=admin)
    assert r.status_code == 200, r.text
    assert rec.calls[-1]["data"] == {"token": "g-rt"}        # revoked at Google
    assert integrations_store.get(w, key) is None
    assert sync_store.counts(w, key) == (0, 0)
    env.wait(w)
    assert not [k for k, _ in env.chunks(w).values() if key in k]


def test_the_member_disconnects_their_own(env, monkeypatch):  # noqa: F811
    import integrations_store
    w, admin, staff, key, rec = staff_with_mail(env, monkeypatch)
    rec.add("POST", "https://oauth2.googleapis.com/revoke")
    assert env.client.delete("/api/me/integrations/gmail", headers=staff).status_code == 200
    assert integrations_store.get(w, key) is None
    env.wait(w)
    assert not [k for k, _ in env.chunks(w).values() if key in k]


def test_a_purged_workspace_revokes_members_tokens_too(env, monkeypatch):  # noqa: F811
    from kb.services import lifecycle
    w, admin, staff, key, rec = staff_with_mail(env, monkeypatch)
    rec.add("POST", "https://oauth2.googleapis.com/revoke")
    assert key in lifecycle._revoke_outside(w)
    assert rec.calls[-1]["data"] == {"token": "g-rt"}


def test_a_refused_gmail_permission_and_the_server_switch(env, monkeypatch):  # noqa: F811
    w = env.workspace()
    staff = env.login(w, "staff")
    rec = Recorded(monkeypatch)
    r = env.client.post("/api/me/integrations/gmail/connect", headers=staff)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.json()["url"]).query))
    rec.add("POST", TOKEN, json_body={"access_token": "a", "refresh_token": "r",
                                      "scope": "openid email"})          # gmail unticked
    r = env.client.get("/api/integrations/gmail/callback", follow_redirects=False,
                       params={"state": q["state"], "code": "abc"})
    assert r.headers["location"] == "/#connections?error=missing_scope"
    monkeypatch.setenv("GMAIL_SYNC", "off")
    assert env.client.post("/api/me/integrations/gmail/connect",
                           headers=staff).status_code == 400
    mine = env.client.get("/api/me/integrations", headers=staff).json()["integrations"]
    assert next(i for i in mine if i["id"] == "gmail")["status"] == "not_configured"
