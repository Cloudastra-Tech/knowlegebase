"""
A workspace's life and its people's data (kb/services/lifecycle.py, dsar.py,
kb/routers/workspace.py, dsar.py; platform.md PL-06, PL-12): the export and
that no secret is in it, deleting after a grace period and cancelling, the purge
leaving nothing of the workspace in any table or on disk, a member's data
export and erasure (with the audit chain still verifying), and audit
retention. Through the API; providers' revoke calls are faked.
"""

import io
import json
import os
import time
import uuid
import zipfile

import pytest

import audit_store
import db
import lifecycle_store
import secret_box

PASSWORD = "correct-horse"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def new_workspace(prefix="life"):
    import auth
    import members_store
    ws = f"{prefix}-{uuid.uuid4().hex[:8]}"
    for name, role in (("admin", "admin"), ("other", "admin"), ("staff", "public")):
        members_store.set_password(f"{name}@{ws}.example", name.title(),
                                   auth.hash_password(PASSWORD), ws, role)
    with db.connect() as conn:
        conn.execute("UPDATE workspaces SET name = ? WHERE slug = ?", (f"Acme {ws}", ws))
    return ws


def login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


@pytest.fixture
def places(monkeypatch, tmp_path):
    """Documents, exports and the search index in a temporary folder; nothing is
    indexed or synced; the providers' revoke calls are recorded, not made."""
    import ingest
    import workspaces
    from connectors import registry
    from kb.services import indexing, slack, sync
    monkeypatch.setattr(workspaces, "docs_root",
                        lambda ws: str(tmp_path / "data" / "workspaces" / ws / "docs"))
    monkeypatch.setattr(lifecycle_store, "EXPORT_ROOT", str(tmp_path / "exports"))
    monkeypatch.setattr(ingest, "DB_FOLDER", str(tmp_path / "chroma"))
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, **kwargs: None)
    monkeypatch.setattr(sync, "start_sync", lambda ws, provider, **options: None)
    monkeypatch.setenv("MAIL_BACKEND", "console")
    revoked = []
    for provider, c in registry.CONNECTORS.items():
        monkeypatch.setattr(c, "disconnect", lambda ws, p=provider: revoked.append(p))
    monkeypatch.setattr(slack, "call", lambda method, token=None, **a: revoked.append(
        (method, token)) or {"ok": True})
    return {"root": tmp_path, "revoked": revoked}


def populate(ws, root):
    """Something in every part of a workspace: secrets in each store that seals
    them, documents, chats, questions, keys, tokens, invites, settings."""
    import api_keys_store
    import conversations_store
    import integrations_store
    import invites_store
    import ingest
    import members_store
    import question_log_store
    import scim_store
    import slack_store
    import verified_store
    import workspaces
    admin, staff = f"admin@{ws}.example", f"staff@{ws}.example"
    secrets = [f"notion-SECRET-{ws}", f"outline-SECRET-{ws}", f"oidc-SECRET-{ws}",
               f"slackclient-SECRET-{ws}", f"slacksign-SECRET-{ws}", f"xoxb-SECRET-{ws}",
               f"jira-SECRET-{ws}", f"zendesk-SECRET-{ws}", f"github-SECRET-{ws}"]
    integrations_store.connect(ws, "notion", "Acme Notion", {"access_token": secrets[0]}, admin)
    integrations_store.connect(ws, "outline", "Acme Outline", {"token": secrets[1]}, admin)
    for provider, secret in zip(("jira", "zendesk", "github"), secrets[6:]):
        integrations_store.connect(ws, provider, f"Acme {provider}", {"token": secret}, admin)
    members_store.create_connection(ws, "oidc", "Okta", "https://idp.example", "kb",
                                    {"client_secret": secrets[2]},
                                    {"issuer": "https://idp.example"}, admin)
    slack_store.set_custom_app(ws, "123.456", secrets[3], secrets[4], admin)
    slack_store.installed(ws, f"T-{ws}", "Acme Slack", "A1", "U1", secrets[5], "chat:write",
                          admin)
    _, api_key = api_keys_store.create(ws, admin, "CI", ["ask"], "admin")
    _, scim_token = scim_store.create_token(ws, admin)
    invites_store.create(ws, f"new@{ws}.example", "public", admin)
    cid = conversations_store.create(ws, staff, "public", "Leave")["id"]
    q = conversations_store.add_message(cid, "user", "How much leave do I get?")
    a = conversations_store.add_message(cid, "assistant", "", parent_id=q)
    conversations_store.finish_message(a, "20 days a year.", "answered")
    conversations_store.set_feedback(a, staff, ws, 1)
    question_log_store.add(ws, staff, "web", "answered", text_mode="full",
                           question="How much leave do I get?", answer="20 days a year.")
    verified_store.create(ws, {"title": "Leave", "variants": ["leave?"], "answer_md": "20 days",
                               "sources": [], "access": "public", "owner": admin,
                               "review_days": 90}, admin)
    now = time.time()
    with db.connect() as conn:
        conn.execute("INSERT INTO glossary (workspace, term, definition, created_by, created_at, "
                     "updated_at) VALUES (?, 'PTO', 'Paid time off', ?, ?, ?)",
                     (ws, admin, now, now))
        conn.execute("INSERT INTO doc_rules (workspace, match_type, match_value, action, "
                     "created_by, created_at, updated_at) VALUES (?, 'provider', 'notion', "
                     "'boost', ?, ?, ?)", (ws, admin, now, now))
        conn.execute("INSERT INTO assistant_settings (workspace, assistant_name, updated_by) "
                     "VALUES (?, 'Ada', ?)", (ws, admin))
    members_store.log_event(ws, None, staff, "signed_in")
    docs = workspaces.docs_root(ws)
    os.makedirs(os.path.join(docs, "public"))
    with open(os.path.join(docs, "public", "handbook.txt"), "w") as f:
        f.write("Everyone gets 20 days of leave.")
    os.makedirs(os.path.join(docs, "connectors", "notion"))
    with open(os.path.join(docs, "connectors", "notion", "page.txt"), "w") as f:
        f.write("A synced page.")
    import chromadb
    chroma = chromadb.PersistentClient(path=ingest.DB_FOLDER)
    for name in (workspaces.collection(ws), workspaces.collection(ws) + "__g2"):
        chroma.get_or_create_collection(name).add(ids=["1"], documents=["leave"],
                                                  embeddings=[[0.1, 0.2]])
    return {"secrets": secrets, "api_key": api_key, "scim_token": scim_token}


def run_jobs(kind):
    """Run the queued jobs of one kind, here and now."""
    from kb import jobs
    runner = jobs.Runner(handlers={kind: jobs.HANDLERS[kind]}, threads=0)
    while runner.run_once():
        pass


def leftovers(needles):
    """(table, column, value) of every stored value containing one of `needles`,
    except the operators' chain and the purge job's own record."""
    found = []
    conn = db.connect()
    try:
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                keys = row.keys()
                if table == "audit_events" and row["workspace"] == audit_store.PLATFORM:
                    continue
                if table == "jobs" and row["kind"] == "workspace_purge":
                    continue
                for k in keys:
                    v = row[k]
                    text = v.decode("latin-1") if isinstance(v, bytes) else str(v)
                    if any(n in text for n in needles):
                        found.append((table, k, text[:80]))
    finally:
        conn.close()
    return found


def events(ws):
    return audit_store.list_events(ws, limit=200)[0]


# ---- Export -----------------------------------------------------------------------------
def test_the_export_has_the_workspace_and_no_secret(client, places, jobs_paused):
    ws = new_workspace()
    made = populate(ws, places["root"])
    h = login(client, f"admin@{ws}.example")
    r = client.post("/api/admin/workspace/export", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert client.post("/api/admin/workspace/export", headers=h).status_code == 409
    run_jobs("workspace_export")
    view = client.get("/api/admin/workspace", headers=h).json()
    (export,) = view["exports"]
    assert export["status"] == "done" and export["counts"]["documents"] == 1
    assert export["expires_at"] - export["finished_at"] == pytest.approx(24 * 3600)
    r = client.get(f"/api/admin/workspace/exports/{export['id']}/download", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"

    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"manifest.json", "members.csv", "audit.jsonl", "conversations.jsonl",
            "verified_answers.jsonl", "settings.json", "docs/public/handbook.txt"} <= names
    assert not any(n.startswith("docs/connectors") for n in names)   # synced copies aren't
    members = z.read("members.csv").decode()
    assert f"admin@{ws}.example" in members and "pbkdf2" not in members
    chats = [json.loads(line) for line in z.read("conversations.jsonl").decode().splitlines()]
    assert chats[0]["messages"][1]["content"] == "20 days a year."
    settings = json.loads(z.read("settings.json"))
    assert {i["provider"] for i in settings["integrations"]} == {"notion", "outline", "jira",
                                                                 "zendesk", "github"}
    assert settings["sign_in_providers"][0]["label"] == "Okta"
    assert settings["slack"]["team_name"] == "Acme Slack"
    assert settings["glossary"][0]["term"] == "PTO"
    assert settings["source_rules"][0]["action"] == "boost"
    assert settings["assistant"]["assistant_name"] == "Ada"
    audit = [json.loads(line) for line in z.read("audit.jsonl").decode().splitlines()]
    assert audit and all(e["workspace"] == ws for e in audit)
    manifest = json.loads(z.read("manifest.json"))
    assert set(manifest["files"]) == names - {"manifest.json"}

    everything = b"".join(z.read(n) for n in names)
    for secret in made["secrets"] + [made["api_key"], made["scim_token"]]:
        assert secret.encode() not in everything
    assert secret_box.PREFIX not in everything and b"gAAAAA" not in everything
    import api_keys_store
    assert api_keys_store._hash(made["api_key"]).encode() not in everything
    assert [e["action"] for e in events(ws)[:2]] == ["workspace.export_download",
                                                     "workspace.export"]


def test_an_export_expires_and_is_deleted(client, places, jobs_paused):
    from kb.services import lifecycle
    ws = new_workspace()
    h = login(client, f"admin@{ws}.example")
    eid = client.post("/api/admin/workspace/export", headers=h).json()["id"]
    run_jobs("workspace_export")
    path = os.path.join(lifecycle_store.export_dir(ws), f"{eid}.zip")
    assert os.path.exists(path)
    lifecycle.prune_exports(now=time.time() + 25 * 3600)
    assert not os.path.exists(path)
    assert client.get(f"/api/admin/workspace/exports/{eid}/download",
                      headers=h).status_code == 404


def test_only_admins_export(client, places):
    ws = new_workspace()
    h = login(client, f"staff@{ws}.example")
    assert client.post("/api/admin/workspace/export", headers=h).status_code == 403


# ---- Delete: grace period, cancel -------------------------------------------------------
def test_deleting_needs_the_name_and_waits_a_grace_period(client, places):
    ws = new_workspace()
    admin = login(client, f"admin@{ws}.example")
    staff = login(client, f"staff@{ws}.example")
    r = client.request("DELETE", "/api/admin/workspace", headers=admin, json={"confirm": ws})
    assert r.status_code == 400                                       # the slug isn't the name
    assert lifecycle_store.status(ws) == "active"
    r = client.request("DELETE", "/api/admin/workspace", headers=admin,
                       json={"confirm": f"Acme {ws}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "deleting"
    assert body["deleting_at"] == pytest.approx(time.time() + 7 * 86400, abs=60)
    # Members are out now, and can't sign in; admins can, to export or cancel.
    assert client.get("/api/auth/me", headers=staff).status_code == 401
    assert client.post("/api/auth/login", json={"username": f"staff@{ws}.example",
                                                "password": PASSWORD}).status_code == 403
    me = client.get("/api/auth/me", headers=admin).json()
    assert (me["workspace_status"], me["deleting_at"]) == ("deleting", body["deleting_at"])
    assert client.get("/api/docs", headers=admin).status_code == 423
    assert client.get("/api/admin/workspace", headers=admin).status_code == 200
    actions = [e["action"] for e in events(ws)]
    assert actions[0] == "workspace.delete"
    platform = [e for e in events(audit_store.PLATFORM) if e["target_id"] == ws]
    assert platform[0]["action"] == "workspace.delete_requested"

    r = client.post("/api/admin/workspace/cancel-deletion", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert client.get("/api/docs", headers=admin).status_code == 200
    login(client, f"staff@{ws}.example")                              # back in
    assert events(ws)[0]["action"] == "workspace.delete_cancel"


def test_the_admins_are_emailed(client, places, monkeypatch):
    import mailer
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, template, **v: sent.append((to, v["title"])))
    ws = new_workspace()
    admin = login(client, f"admin@{ws}.example")
    client.request("DELETE", "/api/admin/workspace", headers=admin, json={"confirm": f"Acme {ws}"})
    assert sorted(to for to, _ in sent) == [f"admin@{ws}.example", f"other@{ws}.example"]
    assert all("will be deleted" in title for _, title in sent)
    client.post("/api/admin/workspace/cancel-deletion", headers=admin)
    assert sum("won't be deleted" in title for _, title in sent) == 2


def test_purge_leaves_nothing_of_the_workspace(client, places, jobs_paused):
    import chromadb
    import ingest
    import workspaces
    from kb.services import lifecycle
    ws, bystander = new_workspace(), new_workspace()
    made = populate(ws, places["root"])
    kept = populate(bystander, places["root"])
    admin = login(client, f"admin@{ws}.example")
    login(client, f"staff@{ws}.example")
    client.post("/api/admin/workspace/export", headers=admin)
    run_jobs("workspace_export")
    with db.connect() as conn:
        sealed = [bytes(r[0]) for r in conn.execute(
            "SELECT secret FROM integrations WHERE workspace = ?", (ws,))]
    assert client.request("DELETE", "/api/admin/workspace", headers=admin,
                          json={"confirm": f"Acme {ws}"}).status_code == 200

    assert lifecycle.purge_due()["queued"] == 0            # not yet: the grace period
    with db.connect() as conn:
        conn.execute("UPDATE workspaces SET deleting_at = ? WHERE slug = ?",
                     (time.time() - 1, ws))
    assert lifecycle.purge_due()["queued"] == 1
    run_jobs("workspace_purge")

    # Revoked at the providers: each connector, and Slack's bot token.
    assert sorted(p for p in places["revoked"] if isinstance(p, str)) == [
        "github", "jira", "notion", "outline", "zendesk"]
    assert ("auth.revoke", made["secrets"][5]) in places["revoked"]
    # Nothing of it in any table (its name, slug, people, secrets), nor on disk.
    assert leftovers([ws, *made["secrets"]]) == []
    assert not os.path.exists(os.path.dirname(workspaces.docs_root(ws)))
    assert not os.path.exists(lifecycle_store.export_dir(ws))
    names = [getattr(c, "name", c) for c in
             chromadb.PersistentClient(path=ingest.DB_FOLDER).list_collections()]
    assert not [n for n in names if ws in n]
    assert workspaces.collection(bystander) in names
    # Crypto-shredded: its secrets, even copied out before, can't be opened.
    with pytest.raises(secret_box.Shredded):
        secret_box.unseal(sealed[0], ws)
    # The operators' record of it survives, with no content.
    (purged,) = [e for e in events(audit_store.PLATFORM)
                 if e["action"] == "workspace.purged" and e["target_id"] == ws]
    assert purged["details"]["requested_by"] == f"admin@{ws}.example"
    assert purged["details"]["rows"]["members"] == 3 and purged["details"]["keys_shredded"] == 1
    assert not any(s in json.dumps(purged) for s in made["secrets"])
    assert audit_store.verify(audit_store.PLATFORM)["ok"]
    # The workspace next door is untouched.
    import integrations_store
    assert integrations_store.secret(bystander, "notion") == {"access_token": kept["secrets"][0]}
    assert lifecycle_store.status(bystander) == "active"
    assert client.get("/api/auth/me", headers=login(client, f"staff@{bystander}.example")
                      ).status_code == 200


def test_a_cancelled_deletion_isnt_purged(client, places, jobs_paused):
    from kb.services import lifecycle
    ws = new_workspace()
    admin = login(client, f"admin@{ws}.example")
    client.request("DELETE", "/api/admin/workspace", headers=admin, json={"confirm": f"Acme {ws}"})
    with db.connect() as conn:
        conn.execute("UPDATE workspaces SET deleting_at = ? WHERE slug = ?",
                     (time.time() - 1, ws))
    lifecycle.purge_due()
    client.post("/api/admin/workspace/cancel-deletion", headers=admin)
    run_jobs("workspace_purge")
    assert lifecycle_store.status(ws) == "active"
    assert client.get("/api/auth/me", headers=admin).status_code == 200


def test_the_default_workspace_cant_be_deleted(client, places):
    from kb.services import lifecycle
    with pytest.raises(ValueError):
        lifecycle.purge("default")


# ---- Suspended ---------------------------------------------------------------------------
def test_a_suspended_workspace_takes_nothing(client, places):
    import api_keys_store
    import scim_store
    ws = new_workspace()
    admin = login(client, f"admin@{ws}.example")
    _, key = api_keys_store.create(ws, f"admin@{ws}.example", "CI", ["search"], "admin")
    _, scim = scim_store.create_token(ws, f"admin@{ws}.example")
    lifecycle_store.suspend(ws, "unpaid", "op@cloudastra.example")
    assert client.get("/api/auth/me", headers=admin).status_code == 401
    assert client.post("/api/auth/login", json={"username": f"admin@{ws}.example",
                                                "password": PASSWORD}).status_code == 403
    assert client.post("/api/v1/search", json={"query": "leave"},
                       headers={"Authorization": f"Bearer {key}"}).status_code == 403
    assert client.get("/scim/v2/Users",
                      headers={"Authorization": f"Bearer {scim}"}).status_code == 403
    lifecycle_store.unsuspend(ws)
    login(client, f"admin@{ws}.example")


# ---- A member's data (DSAR) -------------------------------------------------------------
def test_a_members_data_export(client, places):
    ws = new_workspace()
    populate(ws, places["root"])
    staff = f"staff@{ws}.example"
    h = login(client, staff)
    client.post("/api/auth/logout-everywhere", headers=h)        # an audit event of theirs
    admin = login(client, f"admin@{ws}.example")
    r = client.get(f"/api/admin/members/{staff}/export", headers=admin)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["member"]["username"] == staff and "password_hash" not in data["member"]
    assert [q["question"] for q in data["questions"]] == ["How much leave do I get?"]
    assert data["conversations"][0]["messages"][1]["content"] == "20 days a year."
    assert [f["rating"] for f in data["feedback"]] == [1]
    assert [e["action"] for e in data["audit_events"]] == ["session.sign_out_everywhere"]
    assert events(ws)[0]["action"] == "member.data_export"
    assert client.get(f"/api/admin/members/admin@{ws}-x.example/export",
                      headers=admin).status_code == 404


def test_erasing_a_member_keeps_the_audit_chain(client, places):
    import members_store
    ws = new_workspace()
    populate(ws, places["root"])
    staff = f"staff@{ws}.example"
    login(client, staff)
    admin = login(client, f"admin@{ws}.example")
    # Things that name them: an admin change about them, an event of their own.
    client.patch(f"/api/admin/members/{staff}", headers=admin, json={"role": "hr"})
    client.post("/api/auth/logout-everywhere", headers=login(client, staff))
    assert client.post(f"/api/admin/members/{staff}/erase", headers=admin,
                       json={"confirm": "someone"}).status_code == 400
    assert client.post(f"/api/admin/members/admin@{ws}.example/erase", headers=admin,
                       json={"confirm": f"admin@{ws}.example"}).status_code == 400   # themselves
    r = client.post(f"/api/admin/members/{staff}/erase", headers=admin, json={"confirm": staff})
    assert r.status_code == 200, r.text
    pseudonym = r.json()["pseudonym"]
    assert pseudonym.startswith("erased:")
    assert r.json()["deleted"]["questions"] == 1 and r.json()["deleted"]["conversations"] == 1

    assert members_store.get(staff) is None
    # Their rows in the audit log are as they were (it still verifies), but every
    # read shows the pseudonym; nowhere else is their address kept.
    assert audit_store.verify(ws)["ok"]
    shown = json.dumps(events(ws))
    assert staff not in shown and pseudonym in shown
    erased = [e for e in events(ws) if e["actor"] == pseudonym]
    assert erased and all(e["ip"] is None for e in erased)
    assert events(ws)[0]["action"] == "member.erase" and events(ws)[0]["target_id"] == pseudonym
    assert [e["actor"] for e in audit_store.list_events(ws, actor="erased:")[0]] == \
        [pseudonym] * len(erased)
    assert audit_store.list_events(ws, actor=staff)[0] == []
    found = [f for f in leftovers([staff]) if f[0] not in ("audit_events",)]
    assert found == []
    r = client.get("/api/admin/audit/export", headers=admin, params={"format": "jsonl"})
    assert staff not in r.text
    assert client.post("/api/auth/login", json={"username": staff,
                                                "password": PASSWORD}).status_code == 401


# ---- Audit retention ---------------------------------------------------------------------
def test_audit_retention_keeps_at_least_a_year_and_the_chain(client, places, monkeypatch):
    from kb.services import lifecycle
    ws = new_workspace()
    admin = login(client, f"admin@{ws}.example")
    assert client.get("/api/admin/workspace", headers=admin).json()["audit_days"] is None
    assert client.put("/api/admin/workspace/audit-retention", headers=admin,
                      json={"days": 30}).status_code == 400
    old = time.time() - 400 * 86400
    real = time.time
    monkeypatch.setattr(audit_store.time, "time", lambda: old)
    for n in range(3):
        audit_store.record(ws, "test.old", actor="system", actor_type="system", details={"n": n})
    monkeypatch.setattr(audit_store.time, "time", real)
    r = client.put("/api/admin/workspace/audit-retention", headers=admin, json={"days": 365})
    assert r.status_code == 200 and r.json()["audit_days"] == 365
    before = audit_store.verify(ws)["count"]
    result = lifecycle.purge_audit()
    assert result["deleted"][ws] == 3
    v = audit_store.verify(ws)
    assert v["ok"] and v["count"] == before - 3 + 1            # + the purge's own event
    assert not [e for e in events(ws) if e["action"] == "test.old"]
    assert events(ws)[0]["action"] == "audit.retention_purge"
    lifecycle.purge_audit()                                      # nothing more to delete
    assert audit_store.verify(ws)["ok"]


def test_the_audit_log_is_kept_for_good_by_default():
    from kb.services import lifecycle
    ws = new_workspace()
    audit_store.record(ws, "test.event", actor="system", actor_type="system")
    assert ws not in lifecycle.purge_audit(now=time.time() + 4000 * 86400)["deleted"]
    assert audit_store.verify(ws)["count"] == 1
