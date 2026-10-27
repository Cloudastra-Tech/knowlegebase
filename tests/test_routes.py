"""
The API's routes are a contract with the frontend and the identity providers:
each path, method, endpoint and guard is pinned in tests/routes.txt, so a route
that disappears, moves or loses its guard fails here. FastAPI's own API pages
(/docs, /redoc, /openapi.json) are only served on a developer's machine.
"""

import os

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes.txt")


def route_table(app):
    """One line per endpoint: method, path, endpoint, and the dependencies that
    guard it ("-": anyone may call it)."""
    lines = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute):
            continue
        guards = ",".join(d.call.__name__ for d in route.dependant.dependencies) or "-"
        for method in sorted(context.methods):
            lines.append(f"{method:6} {context.path} {route.endpoint.__name__} {guards}")
    return sorted(lines)


def test_the_routes_are_the_ones_in_the_snapshot():
    import api
    with open(SNAPSHOT, encoding="utf-8") as f:
        expected = [line.rstrip("\n") for line in f if line.strip()]
    actual = route_table(api.app)
    assert actual == expected, ("The API's routes changed. If that's on purpose, make "
                                "tests/routes.txt this:\n" + "\n".join(actual))


# ---- FastAPI's API pages ----------------------------------------------------------------
DOCS_PAGES = ("/docs", "/redoc", "/openapi.json")


@pytest.fixture
def app_with(monkeypatch):
    """A fresh app (api.py's is built once, at import) with these settings."""
    def make(**env):
        for name in ("API_DOCS", "APP_ENV"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        from kb.app import create_app
        return TestClient(create_app())
    return make


def is_api_page(r):
    if r.status_code != 200:
        return False
    if r.headers["content-type"].startswith("application/json"):
        return "openapi" in r.json()
    return "swagger-ui" in r.text or "redoc" in r.text


@pytest.mark.parametrize("env", [{}, {"APP_ENV": "production"}, {"APP_ENV": "staging"},
                                 {"API_DOCS": "off"}, {"APP_ENV": "development"}])
def test_the_api_pages_are_off_unless_asked_for(app_with, env):
    client = app_with(**env)
    for path in DOCS_PAGES + ("/docs/oauth2-redirect",):
        assert not is_api_page(client.get(path)), path


@pytest.mark.parametrize("env", [{"APP_ENV": "dev"}, {"APP_ENV": "local"}, {"APP_ENV": " Dev "},
                                 {"API_DOCS": "on"}, {"APP_ENV": "production", "API_DOCS": "on"}])
def test_the_api_pages_are_on_for_a_developer(app_with, env):
    client = app_with(**env)
    for path in DOCS_PAGES:
        assert is_api_page(client.get(path)), path


def test_turning_the_api_pages_on_adds_only_them(app_with):
    import api
    with_pages = app_with(API_DOCS="on").app
    assert route_table(with_pages) == route_table(api.app)
    extra = {c.path for c in iter_route_contexts(with_pages.routes)} - {
        c.path for c in iter_route_contexts(api.app.routes)}
    assert extra == {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
