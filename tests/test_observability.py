"""
Error tracking is off without SENTRY_DSN and scrubs what it sends; failures the
UI only shows as a message (chat, re-index, connector sync) reach the log too.
"""

import logging

import pytest

QUESTION = "what is the salary of the confidential project lead"


# ---- Sentry ------------------------------------------------------------------------
def test_error_tracking_is_off_without_a_dsn(monkeypatch):
    import sentry_sdk
    import observability

    def no_init(**kwargs):
        raise AssertionError("sentry_sdk.init must not run without SENTRY_DSN")

    monkeypatch.setattr(sentry_sdk, "init", no_init)
    for value in (None, "", "   "):
        if value is None:
            monkeypatch.delenv("SENTRY_DSN", raising=False)
        else:
            monkeypatch.setenv("SENTRY_DSN", value)
        assert observability.init_error_tracking() is False
    assert not sentry_sdk.get_client().is_active()


def test_error_tracking_starts_with_a_dsn_and_no_pii(monkeypatch):
    import sentry_sdk
    import observability

    seen = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setenv("SENTRY_DSN", "https://key@glitchtip.example.com/1")
    monkeypatch.setenv("APP_ENV", "staging")
    assert observability.init_error_tracking() is True
    assert seen["dsn"] == "https://key@glitchtip.example.com/1"
    assert seen["environment"] == "staging"
    assert seen["send_default_pii"] is False
    assert seen["include_local_variables"] is False
    assert seen["max_request_body_size"] == "never"
    assert seen["before_send"] is observability.scrub


def test_events_lose_bodies_queries_cookies_and_credentials():
    import observability

    event = {"request": {
        "url": "https://kb.example.com/api/integrations/notion/callback?code=secret-code",
        "method": "POST", "query_string": "code=secret-code&state=s",
        "data": {"question": QUESTION}, "cookies": {"kb_oauth": "c"},
        "headers": {"Authorization": "Bearer tok", "Cookie": "kb_oauth=c",
                    "CF-Access-Client-Secret": "cf", "User-Agent": "pytest"}}}
    out = observability.scrub(event, {})["request"]
    assert out["url"] == "https://kb.example.com/api/integrations/notion/callback"
    assert out["data"] == out["query_string"] == out["cookies"] == "[scrubbed]"
    assert out["headers"] == {"Authorization": "[scrubbed]", "Cookie": "[scrubbed]",
                              "CF-Access-Client-Secret": "[scrubbed]", "User-Agent": "pytest"}
    for secret in (QUESTION, "secret-code", "Bearer tok", "kb_oauth=c"):
        assert secret not in str(out)


def test_breadcrumbs_keep_the_address_not_the_query():
    import observability

    crumb = {"category": "httplib", "data": {
        "url": "https://www.googleapis.com/drive/v3/files?q=secret", "http.query": "q=secret",
        "method": "GET"}}
    assert observability.scrub_breadcrumb(crumb, {})["data"] == {
        "url": "https://www.googleapis.com/drive/v3/files", "method": "GET"}


# ---- Failures reach the log ---------------------------------------------------------
@pytest.fixture
def signed_in():
    """The API with sign-in bypassed: an HR member of workspace acme."""
    from fastapi.testclient import TestClient
    import api
    from kb import deps

    api.app.dependency_overrides[deps.current_user] = lambda: {
        "username": "hr@acme.test", "workspace": "acme", "role": "hr"}
    api.app.dependency_overrides[deps.current_role] = lambda: "hr"
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_a_failed_chat_answer_is_logged_without_the_question(signed_in, monkeypatch, caplog):
    from kb.services import agents

    def broken_agent(question, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(agents, "get_agent", lambda ws, role: broken_agent)
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        r = signed_in.post("/api/chat/stream", json={"question": QUESTION})
    assert "Something went wrong: RuntimeError" in r.text
    [record] = [r for r in caplog.records if r.message.startswith("Chat:")]
    assert record.levelno == logging.ERROR and record.exc_info
    assert "acme" in record.message
    assert QUESTION not in record.getMessage()


def test_a_failed_reindex_is_logged(monkeypatch, caplog, jobs_paused):
    import uuid
    from kb import jobs
    from kb.services import indexing
    import ingest

    def broken_ingest(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ingest, "main", broken_ingest)
    ws = f"acme-{uuid.uuid4().hex[:8]}"
    job_id = jobs.enqueue("index", ws, {}, lane=indexing.lane(ws), max_attempts=1)
    runner = jobs.Runner(handlers={"index": jobs.HANDLERS["index"]}, threads=0)
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        while jobs.get(job_id)["status"] == "queued" and runner.run_once():
            pass
    assert indexing.index_status(ws)["error"] == "OSError: disk full"    # the UI still sees it
    [record] = [r for r in caplog.records if r.message.startswith("Index:")]
    assert ws in record.message and record.exc_info


def test_a_failed_connector_sync_is_logged(monkeypatch, caplog, tmp_path):
    import integrations_store
    from connectors import base
    from kb.services import sync

    class Broken(base.Connector):
        def changes(self, ctx, cursor):
            raise ConnectionError("notion down")
            yield

    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sync.CONNECTORS, "notion", Broken())
    integrations_store.connect("acme", "notion", "Acme", {"access_token": "x"}, "admin")
    try:
        with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
            with pytest.raises(ConnectionError):
                sync.sync("acme", "notion")
    finally:
        integrations_store.remove("acme", "notion")
    [record] = [r for r in caplog.records if r.message.startswith("Sync:")]
    assert "notion" in record.message and "acme" in record.message and record.exc_info


def test_a_failing_agent_tool_is_logged_by_type_only(caplog):
    import agent

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        out = agent._tool_error("wikipedia", ValueError(f"bad url /{QUESTION}"))
    assert out.startswith("Error: ")                 # the model still gets the reason
    [record] = caplog.records
    assert record.getMessage() == "Agent: the wikipedia tool failed (ValueError)"
