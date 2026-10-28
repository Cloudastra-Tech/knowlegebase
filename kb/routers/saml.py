"""
SAML sign-in through a workspace's own provider (saml_sp.py).

The IdP posts its answer from its own site, so our Lax cookie doesn't come with
it. /acs checks the answer and parks it for a minute (state.saml_parked); /done,
a same-site GET, checks the cookie set at /start, so an answer only signs in the
browser that asked for it (no logging someone in to an attacker's account).
"""

import calendar
import logging
import secrets
import time
import urllib.parse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from saml2 import time_util as saml2_time

import members_store
import saml_sp
import sso
import sso_errors
from kb import state
from kb.deps import public_url
from kb.rate_limit import rate_limit
from kb.services.saml_refresh import saml_newer_metadata
from kb.services.sign_in import (code_for, connection_or_none, finish_sso_test, log_failure,
                                 login_error, mark_browser, saml_conn, sso_test, test_failure)

router = APIRouter()
log = logging.getLogger("uvicorn.error")

SAML_PARK_SECONDS = 60
SAML_SESSION_KEY = "saml_requests"
SAML_TESTS_SESSION_KEY = "saml_tests"     # request id -> test ticket
TEST_RELAY_PREFIX = "t:"


@router.get("/api/auth/saml/{cid}/metadata")
def saml_metadata(cid: str, request: Request):
    """Our SP's metadata, for the customer's IdP (public, like any SP's). Built
    once per version of the connection: it needs the decrypted key."""
    conn = connection_or_none(cid)
    if not conn or conn["protocol"] != "saml" or not saml_sp.xmlsec_binary():
        raise HTTPException(404, "No such SAML sign-in.")
    base = public_url(request, "")
    key = (cid, conn["updated_at"], base)
    cache = state.sp_metadata_cache
    if key not in cache:
        _, keys = saml_conn(cid, trial=True)
        if not keys:
            raise HTTPException(503, "This sign-in's settings can't be read. Tell the admin.")
        if len(cache) > 1000:
            cache.clear()
        cache[key] = saml_sp.sp_metadata(conn, keys, base)
    return Response(cache[key], media_type="application/samlmetadata+xml")


@router.get("/api/auth/saml/{cid}/start")
def saml_start(cid: str, request: Request, test: str = ""):
    rate_limit(request, "sso_start")
    trial = sso_test(test, cid)
    conn, keys = saml_conn(cid, trial)
    if not conn:
        return RedirectResponse("/#login?error=sso_unavailable")
    try:
        request_id, url = saml_sp.start(conn, keys, public_url(request, ""),
                                        TEST_RELAY_PREFIX + test if trial else "kb",
                                        force_authn=bool(trial))
    except Exception:
        log.exception("SAML %s: couldn't start sign-in", cid)
        return login_error("sso_failed", log_failure(conn, "saml_invalid"))
    members_store.remember_saml_request(cid, request_id, saml_sp.REQUEST_SECONDS)
    mark_browser(request)
    # This browser started it. Keep the last few, in case of several tabs.
    request.session[SAML_SESSION_KEY] = (request.session.get(SAML_SESSION_KEY, []) +
                                         [request_id])[-5:]
    if trial:
        tests = dict(list(request.session.get(SAML_TESTS_SESSION_KEY, {}).items())[-4:])
        request.session[SAML_TESTS_SESSION_KEY] = {**tests, request_id: test}
    return RedirectResponse(url)


@router.post("/api/auth/saml/{cid}/acs")
def saml_acs(cid: str, request: Request, SAMLResponse: str = Form(""),
             RelayState: str = Form("")):
    rate_limit(request, "saml_acs")
    # Only to report a failed TEST to the admin; it never lets anything through.
    trial = (sso_test(RelayState[len(TEST_RELAY_PREFIX):], cid)
             if RelayState.startswith(TEST_RELAY_PREFIX) else None)
    conn, keys = saml_conn(cid, trial)
    if not conn:
        return RedirectResponse("/#login?error=sso_unavailable", status_code=303)

    def refuse(code, detail):
        if trial:
            return finish_sso_test(trial, conn, test_failure(code, detail))
        return login_error("sso_failed", log_failure(conn, code), status_code=303)

    def check(conn, keys):
        return saml_sp.finish(conn, keys, public_url(request, ""), SAMLResponse,
                              members_store.open_saml_requests(cid))

    try:
        try:
            answer = check(conn, keys)
        except saml_sp.SamlError as e:
            # Signed with a key we don't know: the IdP may have a new one. Check
            # the SAME answer against its metadata as it is now. Nothing has been
            # used up by a check (that's below, once), so every rule still holds.
            newer = saml_newer_metadata(conn, trial) if e.bad_signature else None
            if not newer:
                raise
            log.warning("SAML %s: a signature failed; checking again with new metadata", cid)
            conn, keys = newer
            answer = check(conn, keys)
    except saml_sp.SamlError as e:
        log.warning("SAML %s: refused an answer: %s", cid, e)
        return refuse(e.code, f"SAML: {e}")
    expires = time.time() + saml_sp.REQUEST_SECONDS
    if answer["expires"]:
        expires = calendar.timegm(saml2_time.str_to_time(answer["expires"]))
    if not (members_store.close_saml_request(answer["request_id"]) and
            members_store.first_use_of_assertion(answer["assertion_id"], expires)):
        log.warning("SAML %s: refused a replayed answer", cid)
        return refuse("saml_replayed", "SAML: this answer was already used")
    key, now = secrets.token_urlsafe(24), time.time()
    with state.saml_lock:
        for k in [k for k, v in state.saml_parked.items() if v["expires"] < now]:
            del state.saml_parked[k]
        state.saml_parked[key] = {"cid": cid, "request_id": answer["request_id"],
                                  "email": answer["email"], "name": answer["name"],
                                  "sent": answer["sent"], "expires": now + SAML_PARK_SECONDS}
    return RedirectResponse(f"/api/auth/saml/{cid}/done?" + urllib.parse.urlencode({"k": key}),
                            status_code=303)


@router.get("/api/auth/saml/{cid}/done")
def saml_done(cid: str, request: Request, k: str = ""):
    with state.saml_lock:
        parked = state.saml_parked.pop(k, None)
    if not parked or parked["cid"] != cid or parked["expires"] < time.time():
        return RedirectResponse("/#login?error=expired")
    started_here = request.session.get(SAML_SESSION_KEY, [])
    request_id = parked["request_id"]
    if request_id not in started_here:
        log.warning("SAML %s: an answer arrived in a browser that didn't start it", cid)
        return RedirectResponse("/#login?error=sso_failed")
    request.session[SAML_SESSION_KEY] = [r for r in started_here if r != request_id]
    tests = request.session.get(SAML_TESTS_SESSION_KEY, {})
    trial = sso_test(tests.get(request_id), cid)
    if request_id in tests:
        request.session[SAML_TESTS_SESSION_KEY] = {r: t for r, t in tests.items()
                                                   if r != request_id}
        if not trial:                      # expired: a test, never a sign-in
            return RedirectResponse("/#sso-test")
    conn, _ = saml_conn(cid, trial)
    if not conn:
        return RedirectResponse("/#login?error=sso_unavailable")
    # The attributes too, for the provider's group rules.
    claims = {**(parked["sent"] or {}), "email": parked["email"], "name": parked["name"]}
    if trial:
        try:
            result = sso.try_connection(conn, claims)
        except Exception as e:
            log.exception("SAML %s: test failed", cid)
            result = test_failure("sso_failed", f"{type(e).__name__}: {e}")
        return finish_sso_test(trial, conn, result, parked["sent"])
    try:
        user = sso.user_for_connection(conn, claims)
    except sso.NotAllowed as e:
        ref = sso_errors.new_ref()
        members_store.log_event(conn["workspace"], cid, parked["email"], "refused", e.reason,
                                ref)
        return login_error(e.reason, ref)
    except Exception:
        log.exception("SAML %s: couldn't decide who gets in", cid)
        return login_error("sso_failed", log_failure(conn, "sso_failed"))
    members_store.log_event(conn["workspace"], cid, user["username"], "signed_in")
    return RedirectResponse("/#sso?code=" + code_for(request, user))
