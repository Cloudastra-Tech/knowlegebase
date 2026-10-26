"""
connectors/http.py  —  the one way connectors call their providers.

    request(method, url, bucket=..., rate=...)   one call, with:
      - a RATE BUCKET per token: every call made with the same access token
        (whichever workspace or thread makes it) waits its turn, so N
        connections don't share one global spacing and one busy connection
        can't use up another's allowance;
      - RETRIES on 429 / 5xx / timeouts, waiting what Retry-After says (seconds
        or a date), else a growing backoff with jitter. A 429 also pauses the
        token's bucket, so the other threads using it wait too;
      - TYPED ERRORS (connectors/base.py): 401 -> AuthExpired, 403 -> Forbidden
        (or RateLimited for Google's 403 rate-limit reasons), 404/410 ->
        NotFound, other 4xx -> ConfigError, retries used up -> RateLimited or
        Transient. Statuses in `ok` come back as a response instead;
      - an optional GUARD (url_guard.check) run before every call, redirects
        not followed: for addresses a workspace admin typed in (Outline's,
        a website's), so the server never calls a private network address.

    refresh_lock(key)   held while a connection's OAuth token is refreshed, so two
                        threads don't both spend a (rotating) refresh token.

Error messages carry the method and the URL without its query string (which
can hold tokens), never a response body.
"""

import datetime
import email.utils
import hashlib
import logging
import random
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import requests

import url_guard
from connectors.base import (AuthExpired, ConfigError, Forbidden, NotFound, RateLimited,
                             Transient)

log = logging.getLogger("uvicorn.error")

RETRIES = 4                    # tries after the first
BACKOFF = 1.0                  # first wait (seconds) without a Retry-After, doubling
MAX_WAIT = 120                 # a longer Retry-After isn't slept through: RateLimited
TIMEOUT = 30

# Swapped by tests (tests/connector_fixtures.py) so nothing reaches the network.
send = requests.request
sleep = time.sleep
clock = time.monotonic


class Bucket:
    """A token bucket: `rate` calls a second on average, up to `burst` at once."""

    def __init__(self, rate, burst=1):
        self.rate, self.burst = float(rate), float(max(burst, 1))
        self.tokens = self.burst
        self.updated = clock()
        self.paused_until = 0.0
        self.lock = threading.Lock()

    def take(self):
        while True:
            with self.lock:
                now = clock()
                if now < self.paused_until:
                    wait = self.paused_until - now
                else:
                    self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
                    self.updated = now
                    if self.tokens >= 1 - 1e-9:          # (rounding: 0.9999999 is a token)
                        self.tokens = max(0.0, self.tokens - 1)
                        return
                    wait = (1 - self.tokens) / self.rate
            sleep(max(wait, 0.001))

    def pause(self, seconds):
        with self.lock:
            self.paused_until = max(self.paused_until, clock() + seconds)


_buckets = {}
_buckets_lock = threading.Lock()
_refresh_locks = {}


def bucket_key(*parts):
    """A bucket name for a token, without keeping the token itself around."""
    return hashlib.sha256("\0".join(str(p) for p in parts).encode()).hexdigest()[:24]


def get_bucket(key, rate, burst=1):
    with _buckets_lock:
        b = _buckets.get(key)
        if b is None or b.rate != rate:
            b = _buckets[key] = Bucket(rate, burst)
        return b


def refresh_lock(key):
    with _buckets_lock:
        return _refresh_locks.setdefault(key, threading.Lock())


def safe_url(url):
    """The URL without its query string or credentials: fine for a log line."""
    try:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.hostname or "", p.path, "", ""))
    except ValueError:
        return "(bad url)"


def retry_after(response):
    """Retry-After in seconds (it may be a number or an HTTP date), or None.
    Without one, Atlassian's X-RateLimit-Reset (an ISO 8601 time,
    https://developer.atlassian.com/cloud/confluence/rate-limiting/) on a 429."""
    value = (response.headers.get("Retry-After") or "").strip()
    if not value:
        reset = (response.headers.get("X-RateLimit-Reset") or "").strip()
        if reset and response.status_code == 429:
            try:
                when = datetime.datetime.fromisoformat(reset.replace("Z", "+00:00"))
                return max(0.0, when.timestamp() - time.time())
            except ValueError:
                return None
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError, IndexError):
        return None


def _google_rate_limited(response):
    """Google answers some rate limits with 403 and a reason instead of 429."""
    try:
        errors = (response.json().get("error") or {}).get("errors") or []
    except ValueError:
        return False
    return any(e.get("reason") in ("rateLimitExceeded", "userRateLimitExceeded") for e in errors)


def _backoff(attempt):
    return BACKOFF * 2 ** attempt * random.uniform(0.8, 1.2)


def guard_public(url):
    """The guard for addresses an admin typed in: https, public IPs only (url_guard,
    CONNECTOR_ALLOW_PRIVATE_HOSTS for a test server on this machine)."""
    try:
        url_guard.check_connector(url)
    except url_guard.UnsafeURL as e:
        raise ConfigError(str(e)) from None


def request(method, url, *, bucket=None, rate=None, burst=1, retries=RETRIES, guard=None,
            ok=(), timeout=TIMEOUT, **kwargs):
    """One call to a provider (see the top of this file). Returns the response
    for 2xx / 3xx and any status in `ok`; raises a ConnectorError otherwise."""
    where = f"{method} {safe_url(url)}"
    tokens = get_bucket(bucket, rate, burst) if bucket and rate else None
    if guard:
        kwargs.setdefault("allow_redirects", False)
    for attempt in range(retries + 1):
        if tokens:
            tokens.take()
        if guard:
            guard(url)
        try:
            r = send(method, url, timeout=timeout, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt == retries:
                raise Transient(f"{where}: {type(e).__name__}") from None
            sleep(_backoff(attempt))
            continue
        status = r.status_code
        if status in ok:
            return r
        limited = status in (429, 529) or (status == 403 and _google_rate_limited(r))
        if limited or (status >= 500 and status != 501):
            wait = retry_after(r)
            wait = _backoff(attempt) if wait is None else wait
            if limited and tokens:
                tokens.pause(wait)
            if attempt == retries or wait > MAX_WAIT:
                error = RateLimited if limited else Transient
                raise error(f"{where}: HTTP {status}", retry_after=wait)
            log.info("Connector: %s answered %d, trying again in %.1f s", where, status, wait)
            sleep(wait)
            continue
        if status < 400:
            return r
        error = {401: AuthExpired, 403: Forbidden, 404: NotFound, 410: NotFound}.get(
            status, ConfigError)
        e = error(f"{where}: HTTP {status}")
        e.status = status
        raise e
    raise Transient(f"{where}: no answer")       # not reached: the loop returns or raises
