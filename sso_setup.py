"""
sso_setup.py  —  checks run when a workspace admin changes its sign-in settings.

  * A DOMAIN only lets people in once the workspace proves it owns it, with a
    DNS TXT record (members_store.txt_record). Anyone can claim a domain;
    only its owner can add the record.
  * An OPENID CONNECT provider is checked before it's saved: its settings
    document (/.well-known/openid-configuration) must load, name the issuer the
    admin typed, and list endpoints the server may call (url_guard.py). The
    endpoints are stored with the connection, so signing in doesn't depend on
    fetching that document again.
"""

import json
import re

import dns.exception
import dns.resolver
import httpx

import url_guard

DISCOVERY_MAX_BYTES = 200_000
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
# What's kept from the settings document; Authlib reads these names.
METADATA_KEYS = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri",
                 "userinfo_endpoint", "end_session_endpoint",
                 "id_token_signing_alg_values_supported",
                 "token_endpoint_auth_methods_supported", "code_challenge_methods_supported")
URL_KEYS = ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint",
            "end_session_endpoint")


class SetupError(ValueError):
    """The settings can't be saved. The message tells the admin what to fix."""


# ---- Domains -------------------------------------------------------------------
def normalize_domain(text):
    domain = (text or "").strip().lower().rstrip(".")
    if domain.startswith("@"):
        domain = domain[1:]
    if not _DOMAIN.match(domain):
        raise SetupError("Enter a domain like example.com.")
    return domain


def txt_values(name):
    """The TXT strings published at `name` (empty if none, or DNS doesn't answer)."""
    try:
        answer = dns.resolver.resolve(name, "TXT", lifetime=5)
    except (dns.exception.DNSException, OSError):
        return []
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


# ---- OpenID Connect providers --------------------------------------------------
def normalize_issuer(text):
    issuer = (text or "").strip()
    try:
        url_guard.check(issuer)
    except url_guard.UnsafeURL as e:
        raise SetupError(f"Issuer: {e}") from None
    if "?" in issuer or "#" in issuer:
        raise SetupError("The issuer is a plain address, with no ? or # part.")
    return issuer


def _get(url):
    with httpx.Client(timeout=10, follow_redirects=False,
                      event_hooks={"request": [url_guard.httpx_hook_sync]}) as client:
        with client.stream("GET", url, headers={"Accept": "application/json"}) as r:
            if r.status_code != 200:
                raise SetupError(f"The provider answered {r.status_code} at {url}. "
                                 "Check the issuer address.")
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > DISCOVERY_MAX_BYTES:
                    raise SetupError("The provider's settings document is too large.")
    return body


def oidc_metadata(issuer):
    """The provider's endpoints, checked. Raises SetupError saying what's wrong."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        doc = json.loads(_get(url))
    except SetupError:
        raise
    except url_guard.UnsafeURL as e:
        raise SetupError(f"Issuer: {e}") from None
    except httpx.HTTPError:
        raise SetupError(f"Couldn't reach {url}. Check the issuer address.") from None
    except ValueError:
        raise SetupError(f"{url} didn't return the provider's settings (JSON).") from None
    if not isinstance(doc, dict):
        raise SetupError(f"{url} didn't return the provider's settings (JSON).")
    # The spec requires the document to name exactly this issuer; ID tokens are
    # later checked against it.
    if str(doc.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
        raise SetupError(f"The provider calls itself {doc.get('issuer')!r}, not {issuer!r}. "
                         "Use its issuer address exactly.")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(key):
            raise SetupError(f"The provider's settings have no {key}.")
    for key in URL_KEYS:
        if doc.get(key):
            try:
                url_guard.check(str(doc[key]))
            except url_guard.UnsafeURL as e:
                raise SetupError(f"The provider's {key}: {e}") from None
    return {k: doc[k] for k in METADATA_KEYS if k in doc}
