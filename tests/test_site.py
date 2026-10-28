"""
The built React site (frontend/dist) as kb/site.py serves it: every screen's URL
(/chat, /admin/sign-in, ...) gets index.html, so a deep link or a reload works,
while missing assets, files and API routes stay 404s.
"""

import json
import pathlib
import shutil

import pytest


@pytest.fixture
def site(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kb import site as kb_site

    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log(1)")
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.mount("/", kb_site._Site(directory=str(tmp_path), html=True), name="site")
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


# ---- Installable as an app (platform.md PL-26) -------------------------------------------------
PUBLIC = pathlib.Path(__file__).parent.parent / "frontend" / "public"


@pytest.fixture
def built(tmp_path):
    """The site as `npm run build` lays it out: frontend/public copied beside index.html."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kb import site as kb_site

    shutil.copytree(PUBLIC, tmp_path, dirs_exist_ok=True)
    (tmp_path / "index.html").write_text((PUBLIC.parent / "index.html").read_text())
    app = FastAPI()
    app.mount("/", kb_site._Site(directory=str(tmp_path), html=True), name="site")
    return TestClient(app)


def test_the_web_manifest_is_served_and_makes_the_site_installable(built):
    res = built.get("/manifest.webmanifest")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/manifest+json")
    manifest = json.loads(res.text)
    assert manifest["name"] and manifest["short_name"]
    assert manifest["display"] == "standalone" and manifest["start_url"].startswith("/")
    sizes = {i["sizes"] for i in manifest["icons"] if "any" in i.get("purpose", "any")}
    assert {"192x192", "512x512"} <= sizes
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])
    for icon in manifest["icons"]:
        got = built.get(icon["src"])
        assert got.status_code == 200 and got.content[:8] == b"\x89PNG\r\n\x1a\n", icon["src"]
    page = built.get("/chat").text
    assert '<link rel="manifest" href="/manifest.webmanifest"' in page
    assert "viewport-fit=cover" in page


def test_the_service_worker_is_served_fresh_and_leaves_the_api_alone(built):
    res = built.get("/sw.js")
    assert res.status_code == 200 and "javascript" in res.headers["content-type"]
    assert res.headers["cache-control"] == "no-cache"         # an update is seen at once
    source = res.text
    assert '"/api/"' in source and '"/mcp"' in source and '"/scim/"' in source
    # It only ever stores built assets (the shell); nothing else is put in its cache.
    assert source.count("cache.put(") == 1
    assert 'url.pathname.startsWith("/assets/")' in source
