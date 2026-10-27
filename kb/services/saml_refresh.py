"""
The IdP's metadata, fetched again from its URL.

An IdP rotates its signing key by publishing the new certificate in its metadata
(often beside the old one for a while). A provider set up from a metadata URL
fetches it again: on a schedule (daily, or sooner if the metadata says), and
when an answer's signature fails (at most every SAML_REFRESH_GAP). Only changes
from the SAME IdP are applied: a new entity ID, or a sign-in URL on another
host, is held back for the admin to look at ("needs review").
The schedule is a periodic job (kb/jobs.py); each fetch is also claimed in the
database first (members_store.claim_metadata_fetch), so a provider is fetched by
one worker even when an answer's failed signature asks for it at the same time.
"""

import logging
import sqlite3
import time
import urllib.parse

import members_store
import saml_sp
import sso_setup
import url_guard
from kb import jobs
from kb.services import webhooks
from kb.services.sign_in import saml_conn

log = logging.getLogger("uvicorn.error")

SAML_REFRESH_EVERY = 24 * 60 * 60        # unless its validUntil / cacheDuration say sooner
SAML_REFRESH_CHECK_EVERY = 10 * 60       # how often the schedule looks; also the shortest wait
SAML_REFRESH_RETRY = 60 * 60             # after a failed fetch
SAML_REFRESH_GAP = 5 * 60                # at most one fetch per provider this often
CERT_UPDATED = "Certificate updated automatically"


def read_idp(xml):
    """saml_sp.read_idp_metadata, and the browser is only sent to https."""
    idp = saml_sp.read_idp_metadata(xml)
    url_guard.require_https(idp["sso_url"])
    return idp


def next_refresh(idp):
    """When metadata just read (saml_sp.read_idp_metadata) should be fetched again."""
    wait = min(SAML_REFRESH_EVERY, idp["refresh_within"] or SAML_REFRESH_EVERY)
    return time.time() + max(SAML_REFRESH_CHECK_EVERY, wait)


def _metadata_attention(conn, column, problem, reason, **status):
    """Record a metadata problem; tell the admin when it's a new one."""
    members_store.set_metadata_status(conn["id"], **{column: problem}, **status)
    if conn.get(column) != problem:
        members_store.log_event(conn["workspace"], conn["id"], None, "attention", reason)
        _tell_webhooks(conn, "attention", reason)
        # TODO: email the workspace's admins as well, once there's a way to send mail.


def _tell_webhooks(conn, outcome, reason):
    """sso.metadata_changed (kb/services/webhooks.py): the same event the admin sees."""
    webhooks.emit(conn["workspace"], "sso.metadata_changed",
                  {"connection_id": conn["id"], "outcome": outcome, "reason": reason})


def refresh_saml_metadata(conn, due_by=None):
    """Fetch a SAML provider's metadata from its URL again (the same checks as
    when it was added), and apply it if it's from the same IdP. Returns the
    connection as it is now, or None if this didn't fetch: it has no URL, it
    isn't due by `due_by`, or it was fetched less than SAML_REFRESH_GAP ago (by
    any worker)."""
    settings, cid = conn["metadata"], conn["id"]
    url = settings.get("metadata_url")
    if not url or not members_store.claim_metadata_fetch(cid, SAML_REFRESH_GAP, due_by):
        return None
    try:
        xml = sso_setup.fetch_saml_metadata(url).decode("utf-8", "replace")
        idp = read_idp(xml)
    except (sso_setup.SetupError, saml_sp.SamlError, url_guard.UnsafeURL) as e:
        log.warning("SAML %s: couldn't refresh its metadata: %s", cid, e)
        # Only our own checks' words reach the admin, never what the provider sent.
        problem = ("couldn't fetch it from the metadata URL." if isinstance(e, sso_setup.SetupError)
                   else str(e))
        _metadata_attention(conn, "metadata_error", problem,
                            f"Metadata refresh failed: {problem}",
                            metadata_due_at=time.time() + SAML_REFRESH_RETRY)
        return members_store.get_connection(cid)
    old_host, new_host = (urllib.parse.urlsplit(u or "").hostname
                          for u in (settings.get("sso_url"), idp["sso_url"]))
    if idp["entity_id"] != conn["issuer"]:
        review = "the identity provider's entity ID changed"
    elif new_host != old_host:
        review = "its sign-in URL moved to another host"
    else:
        review = None
    if review:
        log.warning("SAML %s: fetched metadata not applied: %s", cid, review)
        _metadata_attention(conn, "needs_review", review,
                            f"Metadata change needs review: {review}. Nothing was changed: "
                            "if it's right, load it again (Edit → Metadata URL).",
                            metadata_error=None, metadata_due_at=next_refresh(idp))
        return members_store.get_connection(cid)
    status = {"metadata_refreshed_at": time.time(), "metadata_due_at": next_refresh(idp),
              "needs_review": None, "metadata_error": None}
    if xml == settings.get("idp_metadata"):
        members_store.set_metadata_status(cid, **status)
        return members_store.get_connection(cid)
    try:
        before = saml_sp.read_idp_metadata(settings["idp_metadata"])["fingerprints"]
    except (KeyError, saml_sp.SamlError):
        before = None
    new = {**settings, "idp_metadata": xml, "sso_url": idp["sso_url"],
           "want_authn_requests_signed": idp["want_authn_requests_signed"]}
    if not members_store.replace_metadata(cid, conn["updated_at"], new, **status):
        return members_store.get_connection(cid)    # an admin changed it meanwhile: theirs stands
    if before != idp["fingerprints"]:
        log.warning("SAML %s: its IdP's signing certificates changed; updated", cid)
        members_store.log_event(conn["workspace"], cid, None, "updated", CERT_UPDATED)
        _tell_webhooks(conn, "updated", CERT_UPDATED)
    elif (idp["sso_url"], idp["want_authn_requests_signed"]) != (
            settings.get("sso_url"), bool(settings.get("want_authn_requests_signed"))):
        members_store.log_event(conn["workspace"], cid, None, "updated",
                                "Sign-in settings updated automatically from the metadata")
        _tell_webhooks(conn, "updated", "Sign-in settings updated automatically from the "
                                        "metadata")
    return members_store.get_connection(cid)


def saml_newer_metadata(conn, trial):
    """After an answer's signature failed: (connection, keys) with IdP metadata
    newer than `conn`'s, fetched now or by another worker just before; or None."""
    if not conn["metadata"].get("metadata_url"):
        return None
    try:
        refresh_saml_metadata(conn)
    except Exception:              # e.g. the database is busy: the answer is just refused
        log.exception("SAML %s: couldn't refresh its metadata", conn["id"])
    newer, keys = saml_conn(conn["id"], trial)
    if newer and newer["metadata"].get("idp_metadata") != conn["metadata"].get("idp_metadata"):
        return newer, keys
    return None


def refresh_due_saml_metadata(now=None):
    """Fetch the metadata of every provider set up from a URL that's due by `now`."""
    now = now or time.time()
    try:
        connections = members_store.saml_connections_from_urls()
    except sqlite3.Error:
        log.exception("Couldn't list the SAML providers to refresh")
        return
    for c in connections:
        if (c["metadata_due_at"] or 0) <= now:
            try:
                refresh_saml_metadata(c, due_by=now)
            except Exception:
                log.exception("SAML %s: couldn't refresh its metadata", c["id"])


@jobs.periodic("saml_refresh", lambda: SAML_REFRESH_CHECK_EVERY)
def run_refresh(job=None):
    """The schedule: a job every SAML_REFRESH_CHECK_EVERY seconds (kb/jobs.py)."""
    refresh_due_saml_metadata()
