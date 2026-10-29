"""
Deploy-time safety settings: the toxicity guard's state in /api/health, the
REQUIRE_TOXICITY_GUARD startup check, and the Dockerfile's default
FORWARDED_ALLOW_IPS run through uvicorn's real proxy-headers middleware.

The toxicity check is on unless TOXICITY_GUARD=off (safety.py); tests/conftest.py
turns it off, so these set it either way.
"""

import asyncio
import os
import re

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def api(monkeypatch):
    import api
    from kb import jobs
    # The lifespan starts the job runner; the tests only need startup.
    monkeypatch.setattr(jobs, "start_in_process", lambda: None)
    monkeypatch.setattr(jobs, "stop_in_process", lambda: None)
    monkeypatch.delenv("REQUIRE_TOXICITY_GUARD", raising=False)
    return api


def _client(api):
    from fastapi.testclient import TestClient
    return TestClient(api.app)


@pytest.mark.parametrize("setting, shown", [("off", "off"), ("on", "active")])
def test_health_reports_the_toxicity_guard(api, monkeypatch, setting, shown):
    monkeypatch.setenv("TOXICITY_GUARD", setting)
    r = _client(api).get("/api/health")
    assert r.status_code == 200
    assert r.json()["guards"] == {"toxicity": shown, "toxicity_required": False}
    assert r.json()["ok"] is True


def test_guard_turned_off_still_starts_by_default(api, monkeypatch):
    monkeypatch.setenv("TOXICITY_GUARD", "off")
    with _client(api) as client:
        assert client.get("/api/health").json()["guards"]["toxicity"] == "off"


def test_required_guard_refuses_to_start_turned_off(api, monkeypatch):
    monkeypatch.setenv("TOXICITY_GUARD", "off")
    monkeypatch.setenv("REQUIRE_TOXICITY_GUARD", "1")
    with pytest.raises(RuntimeError, match="REQUIRE_TOXICITY_GUARD"):
        with _client(api):
            pass


def test_required_guard_starts_when_on(api, monkeypatch):
    monkeypatch.setenv("TOXICITY_GUARD", "on")
    monkeypatch.setenv("REQUIRE_TOXICITY_GUARD", "1")
    with _client(api) as client:
        assert client.get("/api/health").json()["guards"] == {"toxicity": "active",
                                                              "toxicity_required": True}


@pytest.mark.sqlite_only("reads the new SQLite file with sqlite3; on Postgres every test database is migrated at first use")
def test_startup_brings_the_database_up_to_date(api, monkeypatch, tmp_path):
    """Migrations run when the server starts, not at its first request."""
    import sqlite3
    import db
    path = str(tmp_path / "app.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_migrated", set())
    with _client(api):
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall()


def _default_forwarded_allow_ips():
    with open(os.path.join(ROOT, "Dockerfile")) as f:
        return re.search(r"--forwarded-allow-ips=\$\{FORWARDED_ALLOW_IPS:-([^}]*)\}", f.read()).group(1)


def _client_seen(trusted, peer, forwarded_for):
    """The client address the app sees for a request from `peer` carrying X-Forwarded-For."""
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"][0]

    scope = {"type": "http", "client": (peer, 40000), "headers": [(b"x-forwarded-for", forwarded_for.encode())]}
    asyncio.run(ProxyHeadersMiddleware(app, trusted_hosts=trusted)(scope, None, None))
    return seen["client"]


@pytest.mark.parametrize("proxy", ["127.0.0.1", "172.17.0.1", "172.18.0.1", "192.168.16.1"])
def test_default_trusts_the_host_proxy_on_a_docker_bridge(proxy):
    # nginx on the host reaches the published port from the bridge gateway.
    assert _client_seen(_default_forwarded_allow_ips(), proxy, "203.0.113.7") == "203.0.113.7"


def test_default_ignores_forwarded_for_from_the_internet():
    # A client connecting straight to port 8501 cannot pose as another address.
    assert _default_forwarded_allow_ips() != "*"
    assert _client_seen(_default_forwarded_allow_ips(), "198.51.100.9", "203.0.113.7") == "198.51.100.9"


def test_health_says_which_version_runs(api, monkeypatch):
    """The image's APP_VERSION and GIT_SHA (Dockerfile build args, set by deploy.sh
    and the release workflow); dev / unknown outside of an image."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    body = _client(api).get("/api/health").json()
    assert (body["version"], body["commit"]) == ("dev", "unknown")
    monkeypatch.setenv("APP_VERSION", "v1.4.0")
    monkeypatch.setenv("GIT_SHA", "0123456789abcdef")
    body = _client(api).get("/api/health").json()
    assert (body["version"], body["commit"]) == ("v1.4.0", "0123456789abcdef")
