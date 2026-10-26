"""
Rate limits, upload caps, security headers and "sign out everywhere" — tested
through the real API. The agent (OpenAI) and indexing are faked; the rest is real.
"""

import asyncio
import json
import os
import sqlite3
import time

import pytest

PASSWORD = "correct-horse"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    import auth
    import members_store
    for name, ws, role in (("lim-a-admin", "lim-a", "admin"), ("lim-a-hr", "lim-a", "hr"),
                           ("lim-a-staff", "lim-a", "public"), ("lim-b-admin", "lim-b", "admin"),
                           ("lim-b-staff", "lim-b", "public")):
        members_store.set_password(name, name, auth.hash_password(PASSWORD), ws, role)
    return TestClient(api.app, follow_redirects=False)


def login(client, username, role="hr"):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": role}


@pytest.fixture
def limits_on(monkeypatch):
    """Rate limits on (conftest turns them off), from a clean slate."""
    from kb import state
    monkeypatch.setenv("RATE_LIMITS", "on")
    monkeypatch.setattr(state, "hits", {})
    return monkeypatch


@pytest.fixture
def fake_agent(monkeypatch):
    from kb.services import agents

    def run(question, with_sources=False, on_event=None):
        return f"About {question}", [], []
    monkeypatch.setattr(agents, "get_agent", lambda ws, role: run)


@pytest.fixture
def no_indexing(monkeypatch, tmp_path):
    """Uploads land in a temporary folder and nothing is re-indexed."""
    from kb.services import indexing
    import workspaces
    monkeypatch.setattr(indexing, "schedule_index", lambda ws: None)
    monkeypatch.setattr(workspaces, "docs_root", lambda ws: str(tmp_path / ws))
    return tmp_path


def ask(client, headers, path="/api/chat"):
    return client.post(path, headers=headers, json={"question": "the refund policy"})


# ---- Rate limits ------------------------------------------------------------------
def test_chat_is_limited_per_person(client, limits_on, fake_agent):
    limits_on.setenv("RATE_LIMIT_CHAT_USER", "2")
    a, b = login(client, "lim-a-staff"), login(client, "lim-a-hr")
    assert [ask(client, a).status_code for _ in range(2)] == [200, 200]
    r = ask(client, a, "/api/chat/stream")
    assert r.status_code == 429
    assert "2 questions in the last minute" in r.json()["detail"]
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    assert ask(client, b).status_code == 200          # someone else in the workspace isn't


def test_chat_is_limited_per_workspace(client, limits_on, fake_agent):
    limits_on.setenv("RATE_LIMIT_CHAT_WORKSPACE", "3")
    a, hr = login(client, "lim-a-staff"), login(client, "lim-a-hr")
    assert ask(client, a).status_code == ask(client, a).status_code == 200
    assert ask(client, hr, "/api/chat/stream").status_code == 200
    r = ask(client, hr)
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Your workspace has asked 3 questions")
    assert "Retry-After" in r.headers
    assert ask(client, login(client, "lim-b-staff")).status_code == 200   # other workspaces aren't


def test_a_refused_question_doesnt_count(client, limits_on, fake_agent):
    """A question the workspace limit turns away doesn't use up the person's own."""
    limits_on.setenv("RATE_LIMIT_CHAT_USER", "2")
    limits_on.setenv("RATE_LIMIT_CHAT_WORKSPACE", "1")
    a = login(client, "lim-a-staff")
    assert ask(client, a).status_code == 200
    assert ask(client, a).json()["detail"].startswith("Your workspace")
    limits_on.setenv("RATE_LIMIT_CHAT_WORKSPACE", "10")
    assert ask(client, a).status_code == 200


def test_reindex_upload_and_sync_are_limited(client, limits_on, no_indexing):
    limits_on.setenv("RATE_LIMIT_REINDEX_USER", "1")
    limits_on.setenv("RATE_LIMIT_UPLOAD_USER", "1")
    limits_on.setenv("RATE_LIMIT_SYNC_USER", "1")
    hr = login(client, "lim-a-hr")
    assert client.post("/api/reindex", headers=hr).status_code == 200
    assert client.post("/api/reindex", headers=hr).status_code == 429
    upload = {"files": ("a.txt", b"hello", "text/plain")}
    assert client.post("/api/docs/upload", headers=hr, files=upload).status_code == 200
    assert client.post("/api/docs/upload", headers=hr, files=upload).status_code == 429
    # Not connected: a 400, but it still counts.
    assert client.post("/api/integrations/notion/sync", headers=hr).status_code == 400
    assert client.post("/api/integrations/notion/sync", headers=hr).status_code == 429


# ---- Upload caps --------------------------------------------------------------------
def test_an_oversize_file_is_refused_and_nothing_saved(client, no_indexing, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_FILE_MB", "1")
    hr = login(client, "lim-a-hr")
    files = [("files", ("small.txt", b"fine", "text/plain")),
             ("files", ("big.txt", b"x" * (1024 * 1024 + 1), "text/plain"))]
    r = client.post("/api/docs/upload", headers=hr, files=files)
    assert r.status_code == 413
    assert "big.txt" in r.json()["detail"] and "up to 1 MB" in r.json()["detail"]
    assert not (no_indexing / "lim-a").exists()
    listed = client.get("/api/docs", headers=hr).json()
    assert listed["upload_limits"] == {"file_mb": 1, "request_mb": 100}


def test_a_request_over_the_limit_is_refused_before_it_is_read(client, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_REQUEST_MB", "1")
    hr = login(client, "lim-a-hr")
    r = client.post("/api/docs/upload", headers={**hr, "Content-Type": "multipart/form-data; "
                                                 "boundary=x"}, content=b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413
    assert "the limit is 1 MB" in r.json()["detail"]
    assert r.headers["content-security-policy"]


def test_a_body_without_a_length_stops_being_read_at_the_limit(monkeypatch):
    """Sent in pieces with no Content-Length: the server stops asking for more
    once it's past the limit, so the rest never arrives (or takes memory)."""
    import api
    monkeypatch.setenv("UPLOAD_MAX_REQUEST_MB", "2")
    chunk, pulled, sent = b"x" * (1024 * 1024), [], []
    body = (b"--b\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.txt\"\r\n"
            b"Content-Type: text/plain\r\n\r\n")

    async def receive():
        pulled.append(1)
        if len(pulled) > 50:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": body if len(pulled) == 1 else chunk,
                "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/api/docs/upload", "raw_path": b"",
             "query_string": b"", "root_path": "", "client": ("127.0.0.1", 1),
             "server": ("testserver", 80),
             "headers": [(b"content-type", b"multipart/form-data; boundary=b"),
                         (b"authorization", b"Bearer " + login_token().encode())]}
    asyncio.run(api.app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert len(pulled) <= 4                          # ~2 MB of the 50 offered


def login_token():
    from fastapi.testclient import TestClient
    import api
    r = TestClient(api.app).post("/api/auth/login",
                                 json={"username": "lim-a-hr", "password": PASSWORD})
    return r.json()["token"]


@pytest.mark.parametrize("name, data", [("report.pdf", b"just some words"),
                                        ("notes.txt", b"%PDF-1.7 binary"),
                                        ("notes.txt", b"text\x00with a NUL"),
                                        ("archive.txt", b"PK\x03\x04zip")])
def test_a_file_must_be_what_its_name_says(client, no_indexing, name, data):
    hr = login(client, "lim-a-hr")
    r = client.post("/api/docs/upload", headers=hr, files={"files": (name, data)})
    assert r.status_code == 415
    assert name in r.json()["detail"]


def test_real_text_and_pdf_files_are_saved(client, no_indexing):
    hr = login(client, "lim-a-hr")
    files = [("files", ("notes.txt", "Café menu\n".encode(), "text/plain")),
             ("files", ("report.pdf", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj", "application/pdf"))]
    r = client.post("/api/docs/upload", headers=hr, files=files, data={"access": "restricted"})
    assert r.status_code == 200 and r.json()["saved"] == ["notes.txt", "report.pdf"]
    folder = no_indexing / "lim-a" / "restricted"
    assert (folder / "notes.txt").read_bytes() == "Café menu\n".encode()
    assert sorted(os.listdir(folder)) == ["notes.txt", "report.pdf"]     # no .part left


# ---- Security headers -----------------------------------------------------------------
def test_every_answer_has_the_security_headers(client):
    for r in (client.get("/api/health"), client.get("/api/auth/me"),
              client.get("/api/auth/sso/nobody/start")):
        csp = r.headers["content-security-policy"]
        assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "same-origin"
        assert "strict-transport-security" not in r.headers          # plain http


def test_hsts_only_over_https(client, monkeypatch):
    from fastapi.testclient import TestClient
    import api
    secure = TestClient(api.app, base_url="https://testserver")
    assert secure.get("/api/health").headers["strict-transport-security"] == "max-age=15552000"
    monkeypatch.setenv("HSTS_MAX_AGE", "0")
    assert "strict-transport-security" not in secure.get("/api/health").headers


def test_the_api_docs_page_gets_the_cdn_it_needs(monkeypatch):
    """When the API pages are on at all (test_routes.py)."""
    from fastapi.testclient import TestClient
    from kb.app import create_app
    monkeypatch.setenv("API_DOCS", "on")
    client = TestClient(create_app())
    for path in ("/docs", "/redoc"):
        csp = client.get(path).headers["content-security-policy"]
        assert "https://cdn.jsdelivr.net" in csp and "frame-ancestors 'none'" in csp
    csp = client.get("/api/health").headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]


def test_sign_in_redirects_still_work_with_the_headers(client, monkeypatch):
    """The OIDC callback and the SAML ACS answer with redirects, unchanged."""
    import sso

    class FakeProvider:
        async def authorize_access_token(self, request):
            return {"userinfo": {"email": "x@nowhere.example", "email_verified": True}}
    monkeypatch.setattr(sso, "client", lambda name: FakeProvider())
    r = client.get("/api/auth/sso/google/callback?code=x&state=y")
    assert r.status_code == 307 and r.headers["location"].startswith("/#login?error=")
    assert r.headers["content-security-policy"]
    r = client.post("/api/auth/saml/000000000000/acs", data={"SAMLResponse": "x"})
    assert r.status_code == 303 and r.headers["location"] == "/#login?error=sso_unavailable"
    assert r.headers["x-frame-options"] == "DENY"


# ---- Sign out everywhere ----------------------------------------------------------------
def test_sign_out_everywhere_ends_all_my_sessions(client):
    laptop, phone = login(client, "lim-b-staff"), login(client, "lim-b-staff")
    other = login(client, "lim-b-admin")
    r = client.post("/api/auth/logout-everywhere", headers=phone)
    assert r.status_code == 200 and r.json()["ended"] >= 2
    for h in (laptop, phone):
        assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.get("/api/auth/me", headers=other).status_code == 200
    assert client.post("/api/auth/logout-everywhere").status_code == 401


def test_an_admin_can_sign_a_member_out_everywhere(client):
    admin, staff = login(client, "lim-a-admin"), login(client, "lim-a-staff")
    assert client.post("/api/admin/members/lim-a-staff/sign-out",
                       headers=login(client, "lim-b-admin")).status_code == 404
    assert client.post("/api/admin/members/lim-a-staff/sign-out",
                       headers=login(client, "lim-a-hr")).status_code == 403
    assert client.post("/api/admin/members/lim-a-admin/sign-out",
                       headers=admin).status_code == 400              # not yourself: see above
    r = client.post("/api/admin/members/lim-a-staff/sign-out", headers=admin)
    assert r.status_code == 200 and r.json()["ended"] >= 1
    assert client.get("/api/auth/me", headers=staff).status_code == 401
    assert client.get("/api/auth/me", headers=login(client, "lim-a-staff")).status_code == 200


def test_turning_access_off_or_removing_ends_their_sessions(client):
    import auth
    import members_store
    import sessions_store
    members_store.set_password("lim-a-leaver", "Leaver", auth.hash_password(PASSWORD),
                               "lim-a", "public")
    admin, leaver = login(client, "lim-a-admin"), login(client, "lim-a-leaver")
    path = "/api/admin/members/lim-a-leaver"
    assert client.patch(path, headers=admin, json={"disabled": True}).status_code == 200
    assert client.patch(path, headers=admin, json={"disabled": False}).status_code == 200
    # Back on, but the session ended when it was turned off.
    assert client.get("/api/auth/me", headers=leaver).status_code == 401
    leaver = login(client, "lim-a-leaver")
    assert sessions_store.delete_user("nobody") == 0
    assert client.delete(path, headers=admin).status_code == 200
    with sqlite3.connect(sessions_store.DB_PATH) as conn:
        left = conn.execute("SELECT COUNT(*) FROM sessions WHERE username = ?",
                            ("lim-a-leaver",)).fetchone()[0]
    assert left == 0


def test_an_old_sessions_table_gets_the_new_columns(tmp_path, monkeypatch):
    import sessions_store
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user TEXT NOT NULL, "
                     "expires REAL NOT NULL)")
        for token, user in (("t1", {"username": "Old@Acme.example", "workspace": "acme"}),
                            ("t2", {"username": "other", "workspace": "acme"})):
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                         (sessions_store._hash(token), json.dumps(user), time.time() + 60))
    monkeypatch.setattr(sessions_store, "DB_PATH", path)
    monkeypatch.setattr(sessions_store, "_migrated", set())
    assert sessions_store.get("t1")["username"] == "Old@Acme.example"   # still signed in
    sessions_store._migrate(sessions_store._db())                      # again: no harm
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT username, workspace FROM sessions ORDER BY username").fetchall()
    assert rows == [("old@acme.example", "acme"), ("other", "acme")]
    assert sessions_store.delete_user("old@acme.example", "globex") == 0
    assert sessions_store.delete_user("OLD@acme.example", "acme") == 1
    assert sessions_store.get("t1") is None and sessions_store.get("t2")
