"""
Deploy-time safety settings: the toxicity guard's state in /api/health, the
REQUIRE_TOXICITY_GUARD startup check, and the Dockerfile's default
FORWARDED_ALLOW_IPS run through uvicorn's real proxy-headers middleware.

The Guardrails Hub validator is never installed here; tests flip
safety.toxicity_guard_active to stand for an image built with or without it.
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
    # The lifespan starts these background loops; the tests only need startup.
    monkeypatch.setattr(api, "_sync_scheduler", lambda: None)
    monkeypatch.setattr(api, "_saml_refresh_scheduler", lambda: None)
    monkeypatch.delenv("REQUIRE_TOXICITY_GUARD", raising=False)
    return api


def _client(api):
    from fastapi.testclient import TestClient
    return TestClient(api.app)


@pytest.mark.parametrize("active, shown", [(False, "missing"), (True, "active")])
def test_health_reports_the_toxicity_guard(api, monkeypatch, active, shown):
    monkeypatch.setattr(api.safety, "toxicity_guard_active", active)
    r = _client(api).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "guards": {"toxicity": shown, "toxicity_required": False}}


def test_missing_guard_still_starts_by_default(api, monkeypatch):
    monkeypatch.setattr(api.safety, "toxicity_guard_active", False)
    with _client(api) as client:
        assert client.get("/api/health").json()["guards"]["toxicity"] == "missing"


def test_required_guard_refuses_to_start_without_it(api, monkeypatch):
    monkeypatch.setattr(api.safety, "toxicity_guard_active", False)
    monkeypatch.setenv("REQUIRE_TOXICITY_GUARD", "1")
    with pytest.raises(RuntimeError, match="REQUIRE_TOXICITY_GUARD"):
        with _client(api):
            pass


def test_required_guard_starts_when_installed(api, monkeypatch):
    monkeypatch.setattr(api.safety, "toxicity_guard_active", True)
    monkeypatch.setenv("REQUIRE_TOXICITY_GUARD", "1")
    with _client(api) as client:
        assert client.get("/api/health").json()["guards"] == {"toxicity": "active",
                                                              "toxicity_required": True}


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
