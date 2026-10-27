"""
logs.py  —  the server's log: one format for every line, and which request or job
each line belongs to.

LOG_FORMAT (environment):
    text   (default, for a developer's console) uvicorn's usual lines, with the
           request id, workspace and user appended:  INFO: ... [rid=… ws=acme user=u_…]
    json   (production) one JSON object per line, for a log shipper:
           {"ts": "2026-10-27T10:00:00.123Z", "level": "INFO", "logger": "kb.request",
            "msg": "GET /api/conversations/{cid} 200 41ms",
            "request_id": "…", "workspace": "acme", "user": "u_3f2a…",
            "method": "GET", "route": "/api/conversations/{cid}",
            "status": 200, "duration_ms": 41}
LOG_LEVEL      the app's loggers (default INFO). Libraries log WARNING and up only.

Every request gets an id: the proxy's X-Request-ID if it sent a sane one, else a
new one. It is sent back in the X-Request-ID response header, so a user's error
report can be matched to the server's lines, and Sentry events carry it as the
request_id tag (observability.py). RequestLog writes one line per request
(it replaces uvicorn's access log, which printed query strings: OAuth codes ride
in them). Lines are never given:
    - an email address: `user` is a keyed hash of the account (user_ref())
    - a concrete path where the route has parameters: the route template is
      logged (/s/{token} and /invite/{token} carry secrets in the path)
    - a query string, a question, an answer or a document's text
Jobs (kb/jobs.py) run inside job_context(): their lines carry job_id, job_kind
and workspace instead of a request id.

    python -m kb.logs user-id someone@example.com   the `user` value of an account
"""

import contextlib
import contextvars
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
import uuid

# The fields of the request or job being served. A dict, shared (not copied) with
# the threads a request's work runs in, so what current_user binds after routing
# shows on the request's own log line too.
_context = contextvars.ContextVar("kb_log_context", default=None)
FIELDS = ("request_id", "workspace", "user", "job_id", "job_kind")
# A proxy's id is kept only if it looks like one: nothing that could forge a log line.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# The loggers the app writes to; everything else (httpx, chromadb, ...) only WARNING up.
APP_LOGGERS = ("uvicorn", "kb", "db", "members_store", "migrations")
# LogRecord's own attributes: anything else on a record came from extra={...}.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


def current():
    """The current request's or job's fields (a copy; empty outside of one)."""
    return dict(_context.get() or {})


def bind(**fields):
    """Add fields (e.g. workspace=…) to the current request's or job's log lines."""
    ctx = _context.get()
    if ctx is not None:
        ctx.update({k: v for k, v in fields.items() if v is not None})


@contextlib.contextmanager
def context(**fields):
    """Log lines inside carry these fields (a new context: nothing is inherited)."""
    token = _context.set({k: v for k, v in fields.items() if v is not None})
    try:
        yield
    finally:
        _context.reset(token)


def job_context(job):
    return context(job_id=job.id, job_kind=job.kind, workspace=job.workspace)


def user_ref(username):
    """A stable id for an account that isn't its email: the first 16 hex digits of
    an HMAC of the username, keyed by LOG_USER_KEY (or SESSION_SECRET). Set one of
    them in production, or the id is a plain hash anyone could recompute from a
    list of addresses."""
    if not username:
        return None
    key = (os.getenv("LOG_USER_KEY") or os.getenv("SESSION_SECRET") or "kb-logs").encode()
    name = username.strip().lower().encode()
    return "u_" + hmac.new(key, name, hashlib.sha256).hexdigest()[:16]


def bind_user(user):
    """After sign-in is checked: whose request this is (never the email itself)."""
    bind(workspace=user.get("workspace"), user=user_ref(user.get("username")))


def request_id_from(headers):
    """The proxy's X-Request-ID if it's a sane one, else a new id."""
    given = (headers.get(b"x-request-id") or b"").decode("latin-1").strip()
    return given if _REQUEST_ID.fullmatch(given) else uuid.uuid4().hex


# ---- Formats -------------------------------------------------------------------------
def _extras(record):
    """The fields of the request or job, and any extra={...} given to this line."""
    out = {}
    for name in FIELDS:
        value = getattr(record, name, None)
        if value is not None:
            out[name] = value
    for name, value in vars(record).items():
        if name not in _STANDARD and name not in out and not name.startswith("_"):
            out[name] = value
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record):
        line = {"ts": datetime.datetime.fromtimestamp(record.created, datetime.UTC)
                .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "level": record.levelname, "logger": record.name,
                "msg": record.getMessage()}
        line.update(_extras(record))
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            line["stack"] = self.formatStack(record.stack_info)
        return json.dumps(line, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """uvicorn's console format (coloured level on a terminal), the context at the end."""
    SHORT = {"request_id": "rid", "workspace": "ws", "job_id": "job", "job_kind": "kind"}

    def __init__(self):
        from uvicorn.logging import DefaultFormatter
        super().__init__()
        self._inner = DefaultFormatter("%(levelprefix)s %(message)s%(kb_suffix)s")

    def format(self, record):
        fields = {k: v for k, v in _extras(record).items() if k in FIELDS}
        record.kb_suffix = (" [" + " ".join(f"{self.SHORT.get(k, k)}={v}"
                                             for k, v in fields.items()) + "]"
                            if fields else "")
        try:
            return self._inner.format(record)
        finally:
            del record.kb_suffix


class _ContextFilter(logging.Filter):
    """Puts the current request's or job's fields on each record (made in its thread)."""
    def filter(self, record):
        for name, value in (_context.get() or {}).items():
            if not hasattr(record, name):
                setattr(record, name, value)
        return True


class _Stderr(logging.StreamHandler):
    """stderr as it is when the line is written (tests swap it out)."""
    def emit(self, record):
        self.stream = sys.stderr
        super().emit(record)


def configure(fmt=None, level=None):
    """Send every log line through one handler in LOG_FORMAT. Safe to call again
    (create_app() and kb/worker.py call it; uvicorn has set its own up by then)."""
    fmt = (fmt or os.getenv("LOG_FORMAT") or "text").strip().lower()
    level = (level or os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    root = logging.getLogger()
    # Ours from an earlier call, and plain console handlers (guardrails calls
    # logging.basicConfig() on import): each line would be written twice. Other
    # handlers (pytest's, a log shipper's) stay.
    for h in [h for h in root.handlers
              if getattr(h, "_kb", False) or type(h) is logging.StreamHandler]:
        root.removeHandler(h)
    handler = _Stderr()
    handler._kb = True
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)
    # uvicorn's loggers hand their lines to the root's handler instead of their own.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    for name in APP_LOGGERS:
        logging.getLogger(name).setLevel(level)
    # RequestLog's line replaces the access log.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    return fmt


# ---- One line per request ---------------------------------------------------------------
request_log = logging.getLogger("kb.request")
_QUIET = ("/api/health",)       # Docker's HEALTHCHECK, every 30 s: DEBUG only


def _route_of(scope):
    """The route's template (/api/conversations/{cid}) if one matched,
    else the path cut after its first segment (/assets/…, /s/…): never a token."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template and getattr(route, "methods", None) is not None:
        return template
    path = scope.get("path", "")
    head, sep, rest = path.lstrip("/").partition("/")
    return "/" + head + ("/…" if sep and rest else "")


class RequestLog:
    """Gives each request an id (X-Request-ID, both ways), the context its lines
    carry, and one log line when it ends: method, route, status, duration."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = request_id_from(dict(scope.get("headers") or []))
        started = time.perf_counter()
        status = {"code": 500}

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message["headers"] = [*message.get("headers", []),
                                      (b"x-request-id", rid.encode())]
            await send(message)

        token = _context.set({"request_id": rid})
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            ms = round((time.perf_counter() - started) * 1000)
            route = _route_of(scope)
            method = scope.get("method", "")
            level = (logging.DEBUG if scope.get("path") in _QUIET and status["code"] < 500
                     else logging.WARNING if status["code"] >= 500 else logging.INFO)
            request_log.log(level, "%s %s %d %dms", method, route, status["code"], ms,
                            extra={"method": method, "route": route,
                                   "status": status["code"], "duration_ms": ms})
            _context.reset(token)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "user-id":
        print(user_ref(argv[1]))
        return 0
    print("usage: python -m kb.logs user-id EMAIL_OR_USERNAME", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
