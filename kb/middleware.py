"""Middleware on every request: a cap on the request body, security headers, and
CORS for the public API from the browser extension only."""

import json

from fastapi import HTTPException
from fastapi.responses import Response

from kb.settings import UPLOAD_MAX_REQUEST_MB, env_int


class BodyLimit:
    """Refuse a request body over UPLOAD_MAX_REQUEST_MB with a 413: at once when
    its Content-Length says so, else as soon as that much has arrived. So a huge
    upload is never taken in (each file has its own, smaller limit: upload_docs)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit_mb = env_int("UPLOAD_MAX_REQUEST_MB", UPLOAD_MAX_REQUEST_MB)
        limit, received = limit_mb * 1024 * 1024, 0
        too_big = HTTPException(413, f"That's too much to send at once: the limit is {limit_mb} "
                                     "MB. Send fewer or smaller files.")
        try:
            length = int(dict(scope["headers"]).get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > limit:
            response = Response(json.dumps({"detail": too_big.detail}), 413,
                                media_type="application/json", headers={"Connection": "close"})
            return await response(scope, receive, send)

        async def counted():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise too_big           # FastAPI answers it with a 413
            return message

        await self.app(scope, counted, send)


# Content Security Policy for the site (the built React app, frontend/dist) and
# the API. Scripts only from this server: no inline or injected ones. Styles may
# be inline (UI libraries add <style> tags at run time, e.g. the toasts) and come
# from Google Fonts, like the fonts themselves (frontend/index.html). Sign-in needs nothing
# extra: the IdP posts SAML answers from its own page, and every move to a
# provider is a redirect or a top-level navigation, not a form of ours.
CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; "
       "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")
# FastAPI's own API pages (/docs, /redoc) load their scripts from a CDN, inline.
# They are only there when settings.api_docs_on().
API_DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
                "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
                "worker-src 'self' blob:; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                    "Referrer-Policy": "same-origin",
                    "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}
HSTS_SECONDS = 180 * 24 * 60 * 60        # HSTS_MAX_AGE in the environment; 0 turns it off


class SecurityHeaders:
    """Security headers on every response. HSTS only over https: as the browser
    saw it, so behind a proxy that ends TLS this needs uvicorn's --proxy-headers
    (Dockerfile) to trust its X-Forwarded-Proto. `docs_paths`: the app's API
    pages (none when they're off), which get API_DOCS_CSP."""

    def __init__(self, app, docs_paths=()):
        self.app = app
        self.docs_paths = {p for p in docs_paths if p}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        csp = API_DOCS_CSP if path in self.docs_paths else CSP
        headers = {**SECURITY_HEADERS, "Content-Security-Policy": csp}
        hsts = env_int("HSTS_MAX_AGE", HSTS_SECONDS, minimum=0)
        if scope.get("scheme") == "https" and hsts:
            headers["Strict-Transport-Security"] = f"max-age={hsts}"

        async def with_headers(message):
            if message["type"] == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (k.lower().encode(), v.encode()) for k, v in headers.items()
                    if k.lower().encode() not in have]
            await send(message)

        await self.app(scope, receive, with_headers)


class ExtensionCors:
    """CORS for the public API (/api/v1) from the browser extension only: the
    origins in EXTENSION_ORIGINS (kb/routers/extension.origins()), and no other
    path. Credentials are never allowed (the extension sends its API key in
    Authorization, not cookies), so there is no Access-Control-Allow-Credentials.
    Not set: no CORS headers at all. (Chrome lets an extension page with host
    permission for the server call it without CORS; the allowlist keeps every
    other origin out.)"""

    METHODS = "GET, POST, DELETE, OPTIONS"
    HEADERS = "Authorization, Content-Type"
    MAX_AGE = "600"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/v1/"):
            return await self.app(scope, receive, send)
        from kb.routers.extension import origins
        headers = dict(scope["headers"])
        origin = headers.get(b"origin", b"").decode("latin-1").rstrip("/")
        if not origin or origin not in origins():
            return await self.app(scope, receive, send)
        if scope["method"] == "OPTIONS" and b"access-control-request-method" in headers:
            response = Response(status_code=204, headers={
                "Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Methods": self.METHODS,
                "Access-Control-Allow-Headers": self.HEADERS,
                "Access-Control-Max-Age": self.MAX_AGE})
            return await response(scope, receive, send)
        allow = [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")]

        async def with_cors(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message.get("headers", [])
                                      if k.lower() != b"access-control-allow-origin"] + allow
            await send(message)

        await self.app(scope, receive, with_cors)
