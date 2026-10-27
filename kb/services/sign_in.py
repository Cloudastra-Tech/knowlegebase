"""
The sign-in steps the OIDC (routers/sso.py), SAML (routers/saml.py) and sign-up
(services/signup.py) routes share: going back to the login page with an error,
reading a provider's answer, the one-time code a sign-in ends with, the admin's
"Test sign-in", and logging failures for the admin.

Errors go back to the login page as /#login?error=<code>&ref=<reference>; it
explains each code in general terms. The reference is on the admin's log entry
(and in the server log), which says exactly why (sso_errors.py).
"""

import logging
import secrets
import sqlite3
import time
import urllib.parse

from fastapi.responses import RedirectResponse

import members_store
import saml_sp
import sso
import sso_errors
from kb import state

log = logging.getLogger("uvicorn.error")


def login_error(error, ref=None, status_code=307, tab="login"):
    """Back to the sign-in page (or its Create a workspace tab) with what went wrong.
    A sign-in that ended in a join request (kb/services/joining.py) isn't an error:
    it goes to its own "request sent" page, with no session."""
    if error == "join_requested":
        return RedirectResponse("/#join-requested", status_code=status_code)
    query = {"error": error, **({"ref": ref} if ref else {})}
    return RedirectResponse(f"/#{tab}?" + urllib.parse.urlencode(query), status_code=status_code)


class ProviderFailed(Exception):
    """The provider's answer can't be used. `error` is for the login page, `code`
    (sso_errors.py) for the admin, and `detail` (partly the provider's own
    words) only for the server log and the admin's Test sign-in."""

    def __init__(self, error, code, detail):
        super().__init__(detail)
        self.error, self.code, self.detail = error, code, detail


# The one-time code a sign-in ends with only works in the browser that started it:
# /start puts an ID in this browser's (Lax, signed) sign-in cookie, the code is
# tied to it, and /auth/sso/exchange (same-site POST, so the cookie comes along)
# checks it. Otherwise someone could finish a sign-in as themselves and send
# you the link, signing you in to their account.
BROWSER_KEY = "sso_browser"


def mark_browser(request):
    request.session.setdefault(BROWSER_KEY, secrets.token_urlsafe(16))


def code_for(request, user):
    return sso.issue_code(user, request.session.get(BROWSER_KEY))


async def provider_answer(name, c, request):
    """The provider sent the browser back: (claims, id token) if its answer checks
    out, else ProviderFailed."""
    error = request.query_params.get("error")
    if error:                      # the provider refused; log why (e.g. admin consent needed)
        detail = f"{error}: {request.query_params.get('error_description', '')}".rstrip(": ")
        log.warning("SSO %s: provider returned %s", name, detail)
        raise ProviderFailed("cancelled" if error == "access_denied" else "sso_failed",
                             sso_errors.from_provider_error(error), detail)
    try:
        token = await c.authorize_access_token(request)    # checks state + the ID token
    except Exception as e:
        log.exception("SSO %s: sign-in callback failed", name)
        raise ProviderFailed("sso_failed", sso_errors.from_oidc_exception(e),
                             f"{type(e).__name__}: {e}") from None
    return token.get("userinfo") or {}, token.get("id_token")


# ---- "Test sign-in": an admin tries a provider in a popup --------------------------
# The admin gets a ticket (POST /api/admin/sign-in/connections/{id}/test) and opens
# the provider's start URL with it. The answer is checked exactly as for a real
# sign-in, but nobody is signed in or added: the result (what the provider sent,
# and what would happen) waits under the ticket (state.sso_tests) for the admin's
# screen to fetch. It works on a provider that's turned off, so it can be tried
# before going live.
SSO_TEST_SECONDS = 10 * 60
# A test's OIDC state starts with this, and maps to its ticket in the sign-in
# cookie: a test answer is never taken for a real sign-in, even once the ticket
# has expired.
TEST_STATE_PREFIX = "t."
SSO_TESTS_SESSION_KEY = "sso_tests"


def new_sso_test(workspace, cid):
    ticket, now = secrets.token_urlsafe(24), time.time()
    with state.sso_tests_lock:
        for t in [t for t, v in state.sso_tests.items() if v["expires"] < now]:
            del state.sso_tests[t]
        state.sso_tests[ticket] = {"workspace": workspace, "cid": cid, "result": None,
                                   "expires": now + SSO_TEST_SECONDS}
    return ticket


def sso_test(ticket, cid):
    """The test a ticket started for this connection, if it's still running."""
    with state.sso_tests_lock:
        test = state.sso_tests.get(ticket or "")
    if test and test["cid"] == cid and test["expires"] > time.time() and not test["result"]:
        return test
    return None


def _shown(values):
    """What the provider sent, for the admin to read (short, no token internals)."""
    skip = {"nonce", "at_hash", "c_hash", "auth_time", "iat", "exp", "nbf"}
    return {k: (", ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v))[:300]
            for k, v in list((values or {}).items())[:60] if k not in skip}


def explained(code):
    """The admin's reason and fix for a code (sso_errors.py), if it's a known one."""
    found = sso_errors.describe(code) or {"reason": None, "fix": None}
    return {"reason_text": found["reason"], "fix": found["fix"]}


def finish_sso_test(test, conn, result, sent=None):
    result = {**result, **({} if result["ok"] else explained(result["reason"])),
              "sent": _shown(sent)}
    with state.sso_tests_lock:
        test["result"] = result
    members_store.log_event(conn["workspace"], conn["id"], result.get("email"), "test",
                            None if result["ok"] else result.get("reason"))
    return RedirectResponse("/#sso-test", status_code=303)


def test_failure(code, detail):
    return {"ok": False, "reason": code, "detail": (detail or "")[:300] or None}


def connection_or_none(cid):
    try:
        return members_store.get_connection(cid)
    except sqlite3.Error:
        log.exception("Couldn't read sign-in connection %s", cid)
        return None


def usable_connection(cid, protocol):
    try:
        conn = members_store.get_connection(cid)
    except sqlite3.Error:
        log.exception("Couldn't read sign-in connection %s", cid)
        return None
    return conn if conn and conn["enabled"] and conn["protocol"] == protocol else None


def saml_conn(cid, trial=None):
    """(connection, its keys), or (None, None) if it can't sign anyone in now.
    A test may use a connection that's turned off."""
    conn = connection_or_none(cid) if trial else usable_connection(cid, "saml")
    if not conn or conn["protocol"] != "saml" or not saml_sp.xmlsec_binary():
        return None, None
    try:
        return conn, members_store.connection_keys(cid)
    except Exception:              # e.g. INTEGRATIONS_KEY changed: can't decrypt its key
        log.exception("SAML %s: can't read its signing key", cid)
        return None, None


# Failed sign-ins are logged for the admin, but anyone can call a callback with
# an error: only so many per provider per minute, so the log can't be flooded.
FAILURES_LOGGED_PER_MINUTE = 20


def log_failure(conn, code):
    """Log a failed sign-in (a code from sso_errors.py) for the admin. Returns the
    reference to show the person; it's in the server log even when the admin's
    log is full for the minute."""
    ref, now = sso_errors.new_ref(), time.time()
    log.warning("SSO connection %s: sign-in failed (%s), reference %s", conn["id"], code, ref)
    with state.hits_lock:
        recent = [t for t in state.failures_logged.get(conn["id"], []) if t > now - 60]
        if len(recent) >= FAILURES_LOGGED_PER_MINUTE:
            return ref
        state.failures_logged[conn["id"]] = recent + [now]
    try:
        members_store.log_event(conn["workspace"], conn["id"], None, "failed", code, ref)
    except sqlite3.Error:
        log.exception("Couldn't log a failed sign-in")
    return ref
