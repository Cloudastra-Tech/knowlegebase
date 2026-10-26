"""
observability.py  —  optional error tracking (Sentry, or self-hosted GlitchTip).

Off unless SENTRY_DSN is set. GlitchTip speaks the same protocol, so its DSN
works here too. Once on, unhandled errors and every log.exception() /
log.error() line are sent, with:

    environment    APP_ENV (e.g. production, staging); default "production"
    release        SENTRY_RELEASE if set (e.g. the git SHA of the image)

Questions, answers and documents are tenant data, and requests carry session
tokens, so what leaves the server is cut down hard (see scrub()):
    - no request bodies (chat questions, uploads, sign-in forms, SAML answers)
    - no query strings (OAuth codes and states ride in them), here or on
      outgoing HTTP calls in the breadcrumbs
    - no cookies, no Authorization or other credential headers
    - no local variables from the stack (they hold questions and tokens)
    - send_default_pii=False: no client addresses or user details
"""

import os

SCRUBBED = "[scrubbed]"
_SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie",
                   "x-api-key", "x-auth-token", "cf-access-client-secret"}


def scrub(event, hint=None):
    """Sentry's before_send: drop anything that could hold tenant data or secrets."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string"):
            if key in request:
                request[key] = SCRUBBED
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in headers:
                if name.lower() in _SECRET_HEADERS:
                    headers[name] = SCRUBBED
        url = request.get("url")
        if isinstance(url, str) and "?" in url:
            request["url"] = url.split("?", 1)[0]
    return event


def scrub_breadcrumb(crumb, hint=None):
    """Sentry's before_breadcrumb: outgoing HTTP calls keep their address, not their query."""
    data = crumb.get("data")
    if isinstance(data, dict):
        data.pop("http.query", None)
        data.pop("http.fragment", None)
        if isinstance(data.get("url"), str):
            data["url"] = data["url"].split("?", 1)[0]
    return crumb


def init_error_tracking():
    """Start Sentry if SENTRY_DSN is set. Returns whether it started."""
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    import sentry_sdk
    sentry_sdk.init(dsn=dsn,
                    environment=(os.getenv("APP_ENV") or "production").strip(),
                    send_default_pii=False,
                    include_local_variables=False,
                    max_request_body_size="never",
                    before_send=scrub,
                    before_send_transaction=scrub,
                    before_breadcrumb=scrub_breadcrumb)
    return True
