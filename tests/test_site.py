"""
The built React site (frontend/dist) as api.py serves it: every screen's URL
(/chat, /admin/sign-in, ...) gets index.html, so a deep link or a reload works,
while missing assets, files and API routes stay 404s.
"""

import pytest


@pytest.fixture
def site(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api

    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log(1)")
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.mount("/", api._Site(directory=str(tmp_path), html=True), name="site")
    return TestClient(app)


@pytest.mark.parametrize("path", ["/", "/chat", "/documents", "/admin/sign-in",
                                  "/login?error=expired"])
def test_screens_get_the_app(site, path):
    res = site.get(path)
    assert res.status_code == 200
    assert "<title>app</title>" in res.text
    assert res.headers["cache-control"] == "no-cache"


def test_assets_cache_forever(site):
    res = site.get("/assets/app-1a2b.js")
    assert res.status_code == 200
    assert "immutable" in res.headers["cache-control"]


@pytest.mark.parametrize("path", ["/assets/gone-9z8y.js", "/favicon.ico", "/api/nope",
                                  "/api/admin/nope"])
def test_missing_files_and_api_routes_stay_404(site, path):
    assert site.get(path).status_code == 404


def test_api_routes_still_answer(site):
    assert site.get("/api/health").json() == {"ok": True}


def test_only_get_and_head(site):
    assert site.post("/chat").status_code == 405
