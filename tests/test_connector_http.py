"""
connectors/http.py: retries on 429 / 5xx honouring Retry-After, typed errors,
per-token rate buckets, and the address guard. Recorded answers only
(tests/connector_fixtures.py); the fake clock shows how long it would wait.
"""

import email.utils
import time

import pytest

from connector_fixtures import Recorded
from connectors import base, http

URL = "https://api.example.com/v1/items"


@pytest.fixture
def rec(monkeypatch):
    monkeypatch.setattr(http.random, "uniform", lambda a, b: 1.0)    # no jitter
    return Recorded(monkeypatch)


def test_a_429_waits_for_retry_after_then_succeeds(rec):
    rec.add("GET", URL, 429, headers={"Retry-After": "7"})
    rec.add("GET", URL, 200, json_body={"ok": True})
    assert http.request("GET", URL).json() == {"ok": True}
    assert rec.slept == [7.0]


def test_retry_after_may_be_a_date(rec):
    later = email.utils.formatdate(time.time() + 30, usegmt=True)
    rec.add("GET", URL, 503, headers={"Retry-After": later})
    rec.add("GET", URL, 200, json_body={})
    http.request("GET", URL)
    assert 25 <= rec.slept[0] <= 31


def test_5xx_and_timeouts_back_off_and_then_give_up(rec, monkeypatch):
    for _ in range(http.RETRIES + 1):
        rec.add("GET", URL, 502)
    with pytest.raises(base.Transient):
        http.request("GET", URL)
    assert rec.slept == [http.BACKOFF * 2 ** n for n in range(http.RETRIES)]

    def timeout(*args, **kwargs):
        raise http.requests.Timeout("slow")
    monkeypatch.setattr(http, "send", timeout)
    with pytest.raises(base.Transient, match="Timeout"):
        http.request("GET", URL, retries=1)


def test_rate_limits_that_never_clear_are_rate_limited(rec):
    for _ in range(3):
        rec.add("GET", URL, 429)
    with pytest.raises(base.RateLimited):
        http.request("GET", URL, retries=2)


def test_a_retry_after_too_long_to_sleep_through_is_handed_back(rec):
    rec.add("GET", URL, 429, headers={"Retry-After": "3600"})
    with pytest.raises(base.RateLimited) as e:
        http.request("GET", URL)
    assert e.value.retry_after == 3600 and rec.slept == []


def test_googles_403_rate_limit_is_retried_but_a_real_403_is_forbidden(rec):
    rec.add("GET", URL, 403, json_body={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}})
    rec.add("GET", URL, 200, json_body={})
    http.request("GET", URL)
    rec.add("GET", URL, 403, json_body={"error": {"errors": [{"reason": "insufficientFilePermissions"}]}})
    with pytest.raises(base.Forbidden):
        http.request("GET", URL)


@pytest.mark.parametrize("status, error", [(401, base.AuthExpired), (404, base.NotFound),
                                           (410, base.NotFound), (400, base.ConfigError)])
def test_statuses_become_typed_errors(rec, status, error):
    rec.add("GET", URL, status)
    with pytest.raises(error) as e:
        http.request("GET", URL + "?access_token=secret")
    assert e.value.status == status
    assert "secret" not in str(e.value)               # no query string in messages


def test_statuses_asked_for_come_back_as_responses(rec):
    rec.add("GET", URL, 304)
    assert http.request("GET", URL, ok=(304,)).status_code == 304


def test_one_token_is_spaced_out_and_other_tokens_are_not_held_up(rec):
    for _ in range(4):
        rec.add("GET", URL, 200, json_body={}, repeat=True)
    start = rec.now
    for _ in range(4):
        http.request("GET", URL, bucket="token-a", rate=2)
    assert rec.now - start == pytest.approx(1.5)        # 4 calls at 2/s: 3 waits of 0.5 s
    start = rec.now
    http.request("GET", URL, bucket="token-b", rate=2)  # its own bucket: no wait
    assert rec.now == start


def test_a_429_pauses_the_whole_bucket(rec):
    bucket = http.get_bucket("token-c", 100)
    rec.add("GET", URL, 429, headers={"Retry-After": "5"})
    rec.add("GET", URL, 200, json_body={}, repeat=True)
    http.request("GET", URL, bucket="token-c", rate=100)
    assert bucket.paused_until >= rec.now - 0.001


def test_the_guard_runs_before_every_call_and_redirects_are_not_followed(rec, monkeypatch):
    rec.hosts["internal.example.com"] = "10.1.2.3"
    with pytest.raises(base.ConfigError, match="private network"):
        http.request("GET", "https://internal.example.com/x", guard=http.guard_public)
    with pytest.raises(base.ConfigError, match="https"):
        http.request("GET", "http://docs.example.com/x", guard=http.guard_public)
    assert rec.calls == []
    rec.add("GET", "https://docs.example.com/x", 302, headers={"Location": "http://10.0.0.1/"})
    r = http.request("GET", "https://docs.example.com/x", guard=http.guard_public)
    assert r.status_code == 302 and rec.calls[-1]["url"] == "https://docs.example.com/x"


def test_a_local_test_site_can_be_allowed_for_connectors_only(rec, monkeypatch):
    import url_guard
    rec.hosts["localhost"] = "127.0.0.1"
    monkeypatch.setenv("CONNECTOR_ALLOW_PRIVATE_HOSTS", "localhost:8765")
    rec.add("GET", "http://localhost:8765/", 200, text="hi")
    assert http.request("GET", "http://localhost:8765/", guard=http.guard_public).text == "hi"
    with pytest.raises(url_guard.UnsafeURL):            # sign-in keeps its own list
        url_guard.check("http://localhost:8765/")
    with pytest.raises(base.ConfigError):               # other ports aren't opened
        http.request("GET", "http://localhost:9999/", guard=http.guard_public)
