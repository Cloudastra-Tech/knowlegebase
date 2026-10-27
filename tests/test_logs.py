"""
Structured logs (kb/logs.py): every request has an id (the proxy's X-Request-ID or
a new one, sent back), one line saying what it was, and its lines say whose it
was without the email; jobs' lines name the job; Sentry events carry the id.
Through the real API, with LOG_FORMAT=json.
"""

import json
import logging
import uuid

import pytest

PASSWORD = "correct-horse"


@pytest.fixture
def json_logs(capsys):
    """The server's log as parsed JSON lines (LOG_FORMAT=json), for one test."""
    from kb import logs
    logs.configure("json")
    capsys.readouterr()

    def lines():
        out = []
        for raw in capsys.readouterr().err.splitlines():
            if raw.startswith("{"):
                out.append(json.loads(raw))
        return out
    yield lines
    logs.configure("text")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


def member(role="public"):
    import auth
    import members_store
    ws = f"logs-{uuid.uuid4().hex[:8]}"
    email = f"someone.private@{ws}.example"
    members_store.set_password(email, "Someone", auth.hash_password(PASSWORD), ws, role)
    return ws, email


def login(client, email):
    r = client.post("/api/auth/login", json={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def request_lines(lines, route):
    return [l for l in lines if l["logger"] == "kb.request" and l["route"] == route]


def test_each_request_gets_an_id_and_sends_it_back(client, json_logs):
    first = client.get("/api/auth/me")
    second = client.get("/api/auth/me")
    ids = [first.headers["X-Request-ID"], second.headers["X-Request-ID"]]
    assert all(len(i) == 32 for i in ids) and ids[0] != ids[1]
    logged = request_lines(json_logs(), "/api/auth/me")
    assert [l["request_id"] for l in logged] == ids
    assert logged[0]["status"] == 401 and logged[0]["method"] == "GET"
    assert isinstance(logged[0]["duration_ms"], int)
    assert logged[0]["level"] == "INFO" and logged[0]["ts"].endswith("Z")


def test_a_proxys_request_id_is_kept_unless_it_could_forge_a_line(client, json_logs):
    kept = client.get("/api/health", headers={"X-Request-ID": "edge-7f3a.2:1"})
    assert kept.headers["X-Request-ID"] == "edge-7f3a.2:1"
    forged = client.get("/api/health", headers={"X-Request-ID": 'x"}\n{"level": "ERROR'})
    assert forged.headers["X-Request-ID"] != 'x"}\n{"level": "ERROR'
    assert len(forged.headers["X-Request-ID"]) == 32
    too_long = client.get("/api/health", headers={"X-Request-ID": "a" * 200})
    assert too_long.headers["X-Request-ID"] != "a" * 200


def test_lines_say_whose_request_it_was_but_never_the_email(client, json_logs):
    from kb import logs
    ws, email = member()
    headers = login(client, email)
    r = client.get("/api/conversations?q=secret-search-words", headers=headers)
    assert r.status_code == 200
    lines = json_logs()
    line = request_lines(lines, "/api/conversations")[-1]
    assert line["workspace"] == ws
    assert line["user"] == logs.user_ref(email) and line["user"].startswith("u_")
    assert line["request_id"] == r.headers["X-Request-ID"]
    everything = "\n".join(json.dumps(l) for l in lines)
    assert email not in everything and "someone.private" not in everything
    assert "secret-search-words" not in everything          # no query strings


def test_routes_with_parameters_are_logged_as_their_template(client, json_logs):
    ws, email = member()
    headers = login(client, email)
    client.get("/api/conversations/not-a-real-conversation", headers=headers)
    client.get("/s/share-token-that-must-not-be-logged")       # the site (or a 404)
    lines = json_logs()
    routes = {l["route"] for l in lines if l["logger"] == "kb.request"}
    assert "/api/conversations/{cid}" in routes
    assert "/s/…" in routes
    assert "not-a-real-conversation" not in json.dumps(lines)
    assert "share-token-that-must-not-be-logged" not in json.dumps(lines)


def test_the_user_id_is_keyed_and_stable(monkeypatch):
    from kb import logs
    monkeypatch.setenv("LOG_USER_KEY", "one")
    a = logs.user_ref("Ada@Example.com ")
    assert a == logs.user_ref("ada@example.com") and len(a) == 18
    monkeypatch.setenv("LOG_USER_KEY", "two")
    assert logs.user_ref("ada@example.com") != a
    assert logs.user_ref(None) is None
    assert logs.main(["user-id", "ada@example.com"]) == 0


def test_lines_written_while_serving_carry_the_request_id(json_logs):
    """A line logged by an endpoint, in the thread its work runs in."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kb import logs

    app = FastAPI()

    @app.get("/work/{item}")
    def work(item: str):
        logs.bind(workspace="acme")
        logging.getLogger("uvicorn.error").warning("Working on it")
        return {"ok": True}

    app.add_middleware(logs.RequestLog)
    r = TestClient(app).get("/work/7")
    lines = json_logs()
    inner = next(l for l in lines if l["msg"] == "Working on it")
    assert inner["request_id"] == r.headers["X-Request-ID"] and inner["workspace"] == "acme"
    outer = next(l for l in lines if l["logger"] == "kb.request")
    assert outer["route"] == "/work/{item}" and outer["workspace"] == "acme"
    assert logs.current() == {}                     # nothing left behind


def test_a_failed_request_is_logged_as_a_warning_with_its_status(json_logs):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kb import logs

    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("no")

    app.add_middleware(logs.RequestLog)
    TestClient(app, raise_server_exceptions=False).get("/boom")
    line = next(l for l in json_logs() if l["logger"] == "kb.request")
    assert line["status"] == 500 and line["level"] == "WARNING"


def test_job_lines_carry_the_job_id_and_kind(jobs_paused, json_logs):
    from kb import jobs

    @jobs.handler("logs-test")
    def run(job):
        logging.getLogger("uvicorn.error").info("Doing the job")

    try:
        job_id = jobs.enqueue("logs-test", workspace="acme")
        assert jobs.Runner(handlers={"logs-test": jobs.HANDLERS["logs-test"]}).run_once()
    finally:
        jobs.HANDLERS.pop("logs-test", None)
    line = next(l for l in json_logs() if l["msg"] == "Doing the job")
    assert line["job_id"] == job_id and line["job_kind"] == "logs-test"
    assert line["workspace"] == "acme" and "request_id" not in line


def test_text_format_appends_the_context():
    from kb import logs
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "Hello %s",
                               ("there",), None)
    with logs.context(request_id="abc", workspace="acme"):
        logs._ContextFilter().filter(record)
    out = logs.TextFormatter().format(record)
    assert out.endswith("Hello there [rid=abc ws=acme]")


def test_json_lines_carry_exceptions():
    from kb import logs
    try:
        raise ValueError("bad")
    except ValueError:
        import sys
        record = logging.LogRecord("kb", logging.ERROR, __file__, 1, "Failed", (), sys.exc_info())
    line = json.loads(logs.JsonFormatter().format(record))
    assert line["msg"] == "Failed" and "ValueError: bad" in line["exc"]


def test_configure_replaces_uvicorns_access_log_and_is_idempotent():
    from kb import logs
    root = logging.getLogger()
    plain = logging.StreamHandler()            # as logging.basicConfig() adds
    root.addHandler(plain)
    logs.configure("json")
    assert plain not in root.handlers          # no line written twice
    logs.configure("json")
    root = logging.getLogger()
    assert len([h for h in root.handlers if getattr(h, "_kb", False)]) == 1
    assert logging.getLogger("uvicorn.access").disabled
    assert logging.getLogger("uvicorn.error").handlers == []
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    logs.configure("text")


def test_sentry_events_are_tagged_with_the_request():
    import observability
    from kb import logs
    with logs.context(request_id="r-1", workspace="acme", user="u_x"):
        event = observability.scrub({"tags": {"kind": "x"}}, {})
    assert event["tags"] == {"kind": "x", "request_id": "r-1", "workspace": "acme"}
    with logs.context(job_id=4, job_kind="index"):
        assert observability.scrub({}, {})["tags"] == {"job_id": "4", "job_kind": "index"}
    assert "tags" not in observability.scrub({}, {})
