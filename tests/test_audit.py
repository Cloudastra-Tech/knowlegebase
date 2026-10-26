"""
The admin audit log (audit_store.py, kb/audit.py, routers/audit.py): every
endpoint that changes something writes an event, the hash chain shows an edited
or deleted row, and the log can be filtered, paged and exported — only by the
workspace's own admins. Through the real API; indexing, syncs, DNS and the
identity provider's settings document are faked.
"""

import csv
import io
import json
import sqlite3
import uuid

import pytest
from fastapi.routing import APIRoute

import audit_store
import db

PASSWORD = "correct-horse"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def new_workspace(prefix="aud"):
    """A workspace of its own with an admin, an HR person and a member."""
    import auth
    import members_store
    ws = f"{prefix}-{uuid.uuid4().hex[:8]}"
    for name, role in (("admin", "admin"), ("hr", "hr"), ("staff", "public")):
        members_store.set_password(f"{name}@{ws}.example", name.title(),
                                   auth.hash_password(PASSWORD), ws, role)
    return ws


def login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def events(ws):
    return audit_store.list_events(ws, limit=200)[0]


# ---- Every endpoint that changes something is audited --------------------------------
# Endpoints that change nothing an admin answers for: signing in and out, a
# sign-in's own steps, questions (the query log is PL-11's next part), and
# looking up an invite.
NOT_AUDITED = {"login", "logout", "sso_exchange", "sso_discover", "saml_acs", "chat",
               "chat_stream", "lookup_invite"}


def test_every_endpoint_that_changes_something_takes_audited():
    import api
    missing = []
    for route in api.app.routes:
        if not isinstance(route, APIRoute) or route.methods <= {"GET", "HEAD"}:
            continue
        names = {d.call.__name__ for d in route.dependant.dependencies}
        if "audited" not in names and route.endpoint.__name__ not in NOT_AUDITED:
            missing.append(f"{sorted(route.methods)} {route.path}")
    assert not missing, "These change something but aren't audited: " + ", ".join(missing)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    """Nothing indexed or synced, documents in a temporary folder, a provider
    whose settings check out, and DNS that says yes."""
    import sso_setup
    import workspaces
    from kb.services import indexing, sync
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, **kwargs: None)
    monkeypatch.setattr(indexing, "schedule_retag", lambda ws, provider: None)
    monkeypatch.setattr(sync, "start_sync", lambda ws, provider, **options: None)
    monkeypatch.setattr(workspaces, "docs_root", lambda ws: str(tmp_path / ws))
    monkeypatch.setattr(sso_setup, "normalize_issuer", lambda issuer: issuer)
    monkeypatch.setattr(sso_setup, "oidc_metadata", lambda issuer: {
        "issuer": issuer, "authorization_endpoint": issuer + "/auth",
        "token_endpoint": issuer + "/token", "jwks_uri": issuer + "/certs"})
    txt = {}
    monkeypatch.setattr(sso_setup, "txt_values", lambda name: txt.get(name, []))
    return txt


def _connection(client, h):
    r = client.post("/api/admin/sign-in/connections", headers=h,
                    json={"label": "Okta", "issuer": "https://idp.example", "client_id": "kb",
                          "client_secret": "s3cret", "enabled": False})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _notion(ws):
    import integrations_store
    integrations_store.connect(ws, "notion", "Acme", {"access_token": "x"}, "admin")


def _drive(ws):
    import integrations_store
    integrations_store.connect(ws, "google_drive", "a@acme.example", {"token": "x"}, "admin")


def _doc(client, h):
    assert client.post("/api/docs", headers=h, json={"name": "a.txt", "access": "public",
                                                     "content": "hello"}).status_code == 200


def _invite(client, h, ws):
    r = client.post("/api/admin/members/invite", headers=h,
                    json={"email": f"new@{ws}-other.example", "role": "public"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _conversation(ws):
    import conversations_store
    return conversations_store.create(ws, f"admin@{ws}.example", "hr", "Leave")["id"]


def _answer(ws, rated=False):
    """An answer in the admin's conversation (rated already, if asked)."""
    import conversations_store
    cid = _conversation(ws)
    q = conversations_store.add_message(cid, "user", "How much leave?")
    mid = conversations_store.add_message(cid, "assistant", "", parent_id=q)
    conversations_store.finish_message(mid, "20 days.", "answered")
    if rated:
        conversations_store.set_feedback(mid, f"admin@{ws}.example", ws, 1)
    return mid


def _verify_setup(client, h, ws, txt):
    row = client.post("/api/admin/sign-in/domains", headers=h,
                      json={"domain": f"{ws}.example"}).json()
    txt[row["txt_name"]] = [row["txt_value"]]


# (action, setup(client, headers, ws, txt) -> anything, request(ws, setup result) ->
#  (method, path, kwargs))
CASES = [
    ("sign_in.policy", None, lambda ws, _: ("PUT", "/api/admin/sign-in/policy",
                                            {"json": {"require_sso": False}})),
    ("sign_in.group_admin_days", None,
     lambda ws, _: ("PUT", "/api/admin/sign-in/group-admin-days", {"json": {"days": 9}})),
    ("domain.add", None, lambda ws, _: ("POST", "/api/admin/sign-in/domains",
                                        {"json": {"domain": f"{ws}.example"}})),
    ("domain.verify", lambda c, h, ws, txt: _verify_setup(c, h, ws, txt),
     lambda ws, _: ("POST", f"/api/admin/sign-in/domains/{ws}.example/verify", {})),
    ("domain.remove",
     lambda c, h, ws, txt: c.post("/api/admin/sign-in/domains", headers=h,
                                  json={"domain": f"{ws}.example"}),
     lambda ws, _: ("DELETE", f"/api/admin/sign-in/domains/{ws}.example", {})),
    ("connection.create", None,
     lambda ws, _: ("POST", "/api/admin/sign-in/connections",
                    {"json": {"label": "Okta", "issuer": "https://idp.example",
                              "client_id": "kb", "client_secret": "s3cret"}})),
    ("connection.update", lambda c, h, ws, txt: _connection(c, h),
     lambda ws, cid: ("PATCH", f"/api/admin/sign-in/connections/{cid}",
                      {"json": {"label": "Okta SSO", "client_secret": "n3w"}})),
    ("connection.roles", lambda c, h, ws, txt: _connection(c, h),
     lambda ws, cid: ("PUT", f"/api/admin/sign-in/connections/{cid}/roles",
                      {"json": {"rules": [{"group": "staff", "role": "hr"}]}})),
    ("connection.test", lambda c, h, ws, txt: _connection(c, h),
     lambda ws, cid: ("POST", f"/api/admin/sign-in/connections/{cid}/test", {})),
    ("connection.delete", lambda c, h, ws, txt: _connection(c, h),
     lambda ws, cid: ("DELETE", f"/api/admin/sign-in/connections/{cid}", {})),
    ("member.update", None, lambda ws, _: ("PATCH", f"/api/admin/members/staff@{ws}.example",
                                           {"json": {"role": "hr", "disabled": True}})),
    ("member.sign_out", None,
     lambda ws, _: ("POST", f"/api/admin/members/staff@{ws}.example/sign-out", {})),
    ("member.remove", None, lambda ws, _: ("DELETE", f"/api/admin/members/staff@{ws}.example",
                                           {})),
    ("invite.create", None, lambda ws, _: ("POST", "/api/admin/members/invite",
                                           {"json": {"email": f"x@{ws}-other.example"}})),
    ("invite.resend", lambda c, h, ws, txt: _invite(c, h, ws),
     lambda ws, iid: ("POST", f"/api/admin/members/invites/{iid}/resend", {})),
    ("invite.revoke", lambda c, h, ws, txt: _invite(c, h, ws),
     lambda ws, iid: ("DELETE", f"/api/admin/members/invites/{iid}", {})),
    ("doc.create", None, lambda ws, _: ("POST", "/api/docs",
                                        {"json": {"name": "b.txt", "access": "restricted",
                                                  "content": "hi"}})),
    ("doc.update", lambda c, h, ws, txt: _doc(c, h),
     lambda ws, _: ("PUT", "/api/docs/public/a.txt", {"json": {"content": "changed"}})),
    ("doc.delete", lambda c, h, ws, txt: _doc(c, h),
     lambda ws, _: ("DELETE", "/api/docs/public/a.txt", {})),
    ("doc.upload", None, lambda ws, _: ("POST", "/api/docs/upload",
                                        {"files": [("files", ("c.txt", b"text", "text/plain"))],
                                         "data": {"access": "public"}})),
    ("index.reindex", None, lambda ws, _: ("POST", "/api/reindex", {})),
    ("integration.sync", lambda c, h, ws, txt: _notion(ws),
     lambda ws, _: ("POST", "/api/integrations/notion/sync", {})),
    ("integration.access", lambda c, h, ws, txt: _notion(ws),
     lambda ws, _: ("PUT", "/api/integrations/notion/access", {"json": {"access": "everyone"}})),
    ("integration.disconnect", lambda c, h, ws, txt: _notion(ws),
     lambda ws, _: ("DELETE", "/api/integrations/notion", {})),
    ("integration.folders", lambda c, h, ws, txt: _drive(ws),
     lambda ws, _: ("PUT", "/api/integrations/google_drive/containers",
                    {"json": {"containers": [{"id": "f1", "name": "HR", "kind": "folder"}]}})),
    ("conversation.create", None, lambda ws, _: ("POST", "/api/conversations", {"json": {}})),
    ("conversation.rename", lambda c, h, ws, txt: _conversation(ws),
     lambda ws, cid: ("PATCH", f"/api/conversations/{cid}", {"json": {"title": "Pay"}})),
    ("conversation.delete", lambda c, h, ws, txt: _conversation(ws),
     lambda ws, cid: ("DELETE", f"/api/conversations/{cid}", {})),
    ("feedback.set", lambda c, h, ws, txt: _answer(ws),
     lambda ws, mid: ("PUT", f"/api/messages/{mid}/feedback",
                      {"json": {"rating": -1, "reasons": [], "comment": "private note"}})),
    ("feedback.delete", lambda c, h, ws, txt: _answer(ws, rated=True),
     lambda ws, mid: ("DELETE", f"/api/messages/{mid}/feedback", {})),
    ("session.sign_out_everywhere", None, lambda ws, _: ("POST", "/api/auth/logout-everywhere",
                                                         {})),
    ("audit.export", None, lambda ws, _: ("GET", "/api/admin/audit/export", {})),
]


@pytest.mark.parametrize("action,setup,call", CASES, ids=[c[0] for c in CASES])
def test_each_change_writes_one_event(client, fakes, monkeypatch, action, setup, call):
    monkeypatch.setenv("MAIL_BACKEND", "console")
    ws = new_workspace()
    h = login(client, f"admin@{ws}.example")
    made = setup(client, h, ws, fakes) if setup else None
    before = {e["id"] for e in events(ws)}
    method, path, kwargs = call(ws, made)
    r = client.request(method, path, headers=h, **kwargs)
    assert r.status_code == 200, r.text
    new = [e for e in events(ws) if e["id"] not in before]
    assert [e["action"] for e in new] == [action]
    (e,) = new
    assert (e["actor"], e["actor_type"], e["workspace"]) == (f"admin@{ws}.example", "user", ws)
    assert e["ip"] == "testclient" and e["user_agent"]
    for private in ("s3cret", "n3w", "private note", "Pay"):    # no secret, nor anyone's words
        assert private not in json.dumps(e)
    assert audit_store.verify(ws)["ok"]


def test_connecting_an_integration_is_audited_at_both_ends(client, fakes, monkeypatch):
    """Connect starts it (by the HR person); the provider's callback, which has no
    session, finishes it in their name."""
    import integrations_store
    from connectors import notion
    from kb.services import sync
    c = notion.CONNECTOR
    monkeypatch.setattr(c, "configured", lambda: True)
    monkeypatch.setattr(c, "auth_url", lambda redirect_uri, state: f"https://n/?s={state}")
    monkeypatch.setattr(c, "finish_connect", lambda ws, code, uri, user:
                        integrations_store.connect(ws, "notion", "Acme Notion", {"t": code}, user))
    monkeypatch.setattr(sync, "ready_to_sync", lambda ws, provider: True)
    ws = new_workspace()
    h = login(client, f"hr@{ws}.example")
    url = client.post("/api/integrations/notion/connect", headers=h).json()["url"]
    r = client.get("/api/integrations/notion/callback",
                   params={"state": url.split("s=")[1], "code": "abc"})
    assert r.status_code == 307 and "connected=notion" in r.headers["location"]
    assert [(e["action"], e["actor"], e["details"]) for e in reversed(events(ws))] == [
        ("integration.connect_start", f"hr@{ws}.example", {}),
        ("integration.connect", f"hr@{ws}.example", {"account": "Acme Notion"})]


def test_a_refused_change_writes_nothing(client):
    ws = new_workspace()
    h = login(client, f"admin@{ws}.example")
    before = len(events(ws))
    assert client.patch(f"/api/admin/members/admin@{ws}.example", headers=h,
                        json={"role": "public"}).status_code == 400      # their own access
    assert client.delete("/api/admin/members/nobody@x.example", headers=h).status_code == 404
    assert len(events(ws)) == before


# ---- The hash chain -----------------------------------------------------------------------
def _db():
    return sqlite3.connect(db.DB_PATH)


def test_the_chain_links_each_workspace_on_its_own():
    a, b = f"chain-a-{uuid.uuid4().hex[:6]}", f"chain-b-{uuid.uuid4().hex[:6]}"
    first = audit_store.record(a, "x.one", actor="pat")
    audit_store.record(b, "x.one", actor="sam")
    second = audit_store.record(a, "x.two", actor="pat", details={"b": 1, "a": [1, 2]})
    assert first["prev_hash"] == audit_store.GENESIS and second["prev_hash"] == first["hash"]
    assert audit_store.verify(a) == {"ok": True, "count": 2, "head": second["hash"],
                                     "broken_at": None}
    assert audit_store.verify("nothing-here") == {"ok": True, "count": 0, "head": None,
                                                  "broken_at": None}


@pytest.mark.parametrize("tamper", [
    "UPDATE audit_events SET actor = 'mallory' WHERE id = ?",
    "UPDATE audit_events SET details = '{\"role\":\"public\"}' WHERE id = ?",
    "UPDATE audit_events SET at = at - 3600 WHERE id = ?",
    "DELETE FROM audit_events WHERE id = ?",
])
def test_an_edited_or_deleted_row_breaks_the_chain(tamper):
    ws = f"tamper-{uuid.uuid4().hex[:6]}"
    rows = [audit_store.record(ws, "member.update", actor="pat", target_id=f"m{i}",
                               details={"role": "admin"}) for i in range(3)]
    assert audit_store.verify(ws)["ok"]
    with _db() as conn:
        conn.execute(tamper, (rows[1]["id"],))
    result = audit_store.verify(ws)
    assert not result["ok"]
    # an edit breaks the row itself; a deletion, the row after it
    assert result["broken_at"] == (rows[2]["id"] if tamper.startswith("DELETE") else rows[1]["id"])


def test_rewriting_a_row_with_a_fresh_hash_still_breaks_the_link():
    ws = f"rehash-{uuid.uuid4().hex[:6]}"
    rows = [audit_store.record(ws, "doc.delete", actor="pat", target_id=f"d{i}")
            for i in range(3)]
    forged = {**rows[1], "actor": "mallory"}
    with _db() as conn:
        conn.execute("UPDATE audit_events SET actor = ?, hash = ? WHERE id = ?",
                     ("mallory", audit_store.row_hash(forged), rows[1]["id"]))
    assert audit_store.verify(ws)["broken_at"] == rows[2]["id"]


def test_the_verify_endpoint_reports_the_chain(client):
    ws = new_workspace()
    h = login(client, f"admin@{ws}.example")
    client.put("/api/admin/sign-in/group-admin-days", headers=h, json={"days": 5})
    r = client.get("/api/admin/audit/verify", headers=h).json()
    assert r["ok"] and r["count"] == 1
    with _db() as conn:
        conn.execute("UPDATE audit_events SET details = '{}' WHERE workspace = ?", (ws,))
    assert client.get("/api/admin/audit/verify", headers=h).json()["ok"] is False


# ---- Reading and exporting ---------------------------------------------------------------
@pytest.fixture(scope="module")
def logged(client):
    """A workspace with five events by two people, a day apart."""
    ws = new_workspace("log")
    h = login(client, f"admin@{ws}.example")
    for i, (actor, action) in enumerate([("pat", "member.update"), ("sam", "member.remove"),
                                         ("pat", "doc.create"), ("pat", "=cmd|calc"),
                                         ("sam", "member.update")]):
        audit_store.record(ws, action, actor=actor, target_id=str(i), details={"n": i})
    with _db() as conn:
        for i, row in enumerate(conn.execute("SELECT id FROM audit_events WHERE workspace = ? "
                                             "ORDER BY id", (ws,)).fetchall()):
            conn.execute("UPDATE audit_events SET at = ? WHERE id = ?", (1000 + i * 86400,
                                                                         row[0]))
    return ws, h


def test_the_log_is_filtered_and_paged_newest_first(client, logged):
    ws, h = logged

    def get(**params):
        r = client.get("/api/admin/audit", headers=h, params=params)
        assert r.status_code == 200, r.text
        return r.json()
    assert [e["target_id"] for e in get()["events"]] == ["4", "3", "2", "1", "0"]
    assert [e["target_id"] for e in get(actor="sa")["events"]] == ["4", "1"]
    assert [e["target_id"] for e in get(action="member.update")["events"]] == ["4", "0"]
    assert [e["target_id"] for e in get(action="member.")["events"]] == ["4", "1", "0"]
    assert [e["target_id"] for e in get(**{"from": 1000 + 86400,
                                           "to": 1000 + 3 * 86400})["events"]] == ["2", "1"]
    page = get(limit=2)
    assert [e["target_id"] for e in page["events"]] == ["4", "3"]
    page = get(limit=2, cursor=page["next_cursor"])
    assert [e["target_id"] for e in page["events"]] == ["2", "1"]
    page = get(limit=2, cursor=page["next_cursor"])
    assert [e["target_id"] for e in page["events"]] == ["0"] and page["next_cursor"] is None
    assert get(actor="%")["events"] == []                   # LIKE wildcards are literal


def test_the_export_streams_every_matching_row(client, logged):
    ws, h = logged
    r = client.get("/api/admin/audit/export", headers=h, params={"format": "jsonl",
                                                                  "actor": "pat"})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith(f'attachment; filename="audit-{ws}-')
    rows = [json.loads(line) for line in r.text.splitlines()]
    assert [x["target_id"] for x in rows] == ["0", "2", "3"]            # oldest first
    r = client.get("/api/admin/audit/export", headers=h, params={"format": "csv"})
    assert r.headers["content-type"].startswith("text/csv")
    table = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["action"] for x in table][:5] == ["member.update", "member.remove", "doc.create",
                                                "'=cmd|calc", "member.update"]  # no formula
    assert table[0]["at"] == "1970-01-01T00:16:40Z"
    assert client.get("/api/admin/audit/export", headers=h,
                      params={"format": "xml"}).status_code == 422
    # The export itself is in the log.
    assert audit_store.list_events(ws, action="audit.export")[0][0]["details"]["format"] == "csv"


def test_the_chain_can_be_checked_from_a_jsonl_export(client):
    ws = new_workspace()
    h = login(client, f"admin@{ws}.example")
    for days in (3, 4):
        client.put("/api/admin/sign-in/group-admin-days", headers=h, json={"days": days})
    rows = [json.loads(line) for line in client.get(
        "/api/admin/audit/export", headers=h, params={"format": "jsonl"}).text.splitlines()]
    prev = audit_store.GENESIS
    for x in rows:
        canonical = json.dumps(x["details"], sort_keys=True, separators=(",", ":"))
        assert x["prev_hash"] == prev
        assert audit_store.row_hash({**x, "details": canonical}) == x["hash"]
        prev = x["hash"]
    assert [x["action"] for x in rows] == ["sign_in.group_admin_days"] * 2 + ["audit.export"]


def test_only_the_workspaces_admins_read_its_log(client, logged):
    ws, _ = logged
    other = new_workspace()
    theirs = client.get("/api/admin/audit", headers=login(client, f"admin@{other}.example"))
    assert all(e["workspace"] == other for e in theirs.json()["events"])
    hr = login(client, f"hr@{ws}.example")
    for path in ("/api/admin/audit", "/api/admin/audit/export", "/api/admin/audit/verify"):
        assert client.get(path, headers=hr).status_code == 403
        assert client.get(path).status_code == 401


def test_the_cli_writes_the_operators_changes(monkeypatch, capsys):
    import auth
    import members_store
    ws = f"cli-{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "long-enough-pw")
    auth._cli(["add", f"op@{ws}.example", "--workspace", ws, "--role", "hr"])
    auth._cli(["disable", f"op@{ws}.example"])
    auth._cli(["remove", f"op@{ws}.example"])
    assert capsys.readouterr().out.splitlines() == [
        f"Added op@{ws}.example (workspace: {ws}, role: hr).",
        f"op@{ws}.example is disabled.", f"Removed op@{ws}.example."]
    assert [(e["action"], e["actor"], e["actor_type"]) for e in reversed(events(ws))] == [
        ("member.add", "cli", "operator"), ("member.disable", "cli", "operator"),
        ("member.remove", "cli", "operator")]
    assert members_store.get(f"op@{ws}.example") is None
