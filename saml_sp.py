"""
saml_sp.py  —  SIGN IN WITH SAML: this app as the service provider (SP) for a
workspace's own identity provider (IdP: Okta, Entra ID, Google Workspace,
Keycloak, ADFS, ...).

pysaml2 does the SAML work: building the login request, and checking the
answer's signature (with the xmlsec1 program), audience, timing and that it
answers a request we made. This file holds the settings and the rules:

  * each connection is its own SP, with its own entity ID and its own signing
    keypair (members_store.py keeps the key encrypted)
  * login requests are signed when the IdP's metadata asks for it
  * the IdP must sign the assertion or the whole response
  * only answers to OUR requests count (no IdP-initiated sign-in), each request
    once, and each assertion once
  * the email comes from an attribute (or an email-shaped NameID); sso.py then
    applies the same workspace and domain rules as OpenID Connect

The IdP posts its answer from ITS site, so the browser doesn't send our
SameSite=Lax cookie with it. To tie the answer to the browser that started
the sign-in, the request ID is remembered in that cookie at /start, and the
assertion consumer hands over to a same-site GET (/done) that checks it.

The xmlsec1 program must be installed (Docker: apt-get install xmlsec1).
XMLSEC_BINARY in .env points at it if it isn't on the PATH.
"""

import base64
import contextlib
import datetime
import html
import os
import re
import shutil
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT
from saml2.attribute_converter import ac_factory
from saml2.client import Saml2Client
from saml2.config import SPConfig
from saml2.mdstore import InMemoryMetaData
from saml2.metadata import entity_descriptor
from saml2.saml import NAMEID_FORMAT_EMAILADDRESS, NAMEID_FORMAT_UNSPECIFIED
from saml2.xmldsig import DIGEST_SHA256, SIG_RSA_SHA256

CLOCK_SKEW_SECONDS = 120
REQUEST_SECONDS = 10 * 60          # how long the IdP has to answer
# Where IdPs usually put the email, tried in order after the connection's own choice.
EMAIL_ATTRIBUTES = ("email", "mail", "emailaddress", "emailAddress",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                    "urn:oid:0.9.2342.19200300.100.1.3", "Email", "user.email")
NAME_ATTRIBUTES = ("displayName", "name", "cn",
                   "http://schemas.microsoft.com/identity/claims/displayname",
                   "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,63}$", re.I)


class SamlError(ValueError):
    """The IdP's answer can't be used. The message is for the server log."""


# ---- The xmlsec1 program ---------------------------------------------------------
def xmlsec_binary():
    """Path to xmlsec1, or None if this server can't do SAML."""
    path = os.getenv("XMLSEC_BINARY") or shutil.which("xmlsec1")
    return path if path and os.access(path, os.X_OK) else None


# ---- This app's side: one SP per connection --------------------------------------
def new_keypair(common_name):
    """A fresh RSA key and self-signed certificate (PEM) for signing login requests.
    IdPs only use the certificate to check our signature, so self-signed is normal."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name[:64])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode(),
            cert.public_bytes(serialization.Encoding.PEM).decode())


def sp_urls(base, cid):
    """(entity ID, assertion consumer URL, metadata URL) of a connection's SP.
    The entity ID is the metadata URL, so IdPs that fetch it find everything."""
    root = f"{base.rstrip('/')}/api/auth/saml/{cid}"
    return f"{root}/metadata", f"{root}/acs", f"{root}/metadata"


@contextlib.contextmanager
def _client(conn, keys, base):
    """A pysaml2 client for one connection. pysaml2 reads keys from files, so
    they're written to a private temporary folder that's removed afterwards."""
    folder = tempfile.mkdtemp(prefix="kb-saml-")      # mode 0700
    try:
        key_file, cert_file = os.path.join(folder, "sp.key"), os.path.join(folder, "sp.crt")
        for path, text in ((key_file, keys["sp_key"]), (cert_file, keys["sp_cert"])):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(text)
        entity_id, acs, _ = sp_urls(base, conn["id"])
        settings = conn["metadata"]
        config = SPConfig().load({
            "entityid": entity_id,
            "xmlsec_binary": xmlsec_binary(),
            "key_file": key_file,
            "cert_file": cert_file,
            "accepted_time_diff": CLOCK_SKEW_SECONDS,
            "allow_unknown_attributes": True,
            "metadata": {"inline": [settings["idp_metadata"]]},
            "service": {"sp": {
                "name": conn["label"],
                "endpoints": {"assertion_consumer_service": [(acs, BINDING_HTTP_POST)]},
                "name_id_format": [NAMEID_FORMAT_EMAILADDRESS, NAMEID_FORMAT_UNSPECIFIED],
                "allow_unsolicited": False,               # no IdP-initiated sign-in
                "authn_requests_signed": bool(settings.get("want_authn_requests_signed")),
                "want_assertions_or_response_signed": True,
                "want_assertions_signed": False,
                "want_response_signed": False,
                "signing_algorithm": SIG_RSA_SHA256,
                "digest_algorithm": DIGEST_SHA256,
            }},
        })
        yield Saml2Client(config=config)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def sp_metadata(conn, keys, base):
    """Our SP's metadata XML, for the customer to load into their IdP."""
    with _client(conn, keys, base) as client:
        return str(entity_descriptor(client.config))


def start(conn, keys, base, relay_state="kb", force_authn=False):
    """(request ID, URL to send the browser to) for a new sign-in. force_authn
    asks the IdP for a fresh login even if it has a session (admin's test)."""
    with _client(conn, keys, base) as client:
        request_id, info = client.prepare_for_authenticate(
            entityid=conn["issuer"], relay_state=relay_state, binding=BINDING_HTTP_REDIRECT,
            force_authn="true" if force_authn else None,
            sigalg=SIG_RSA_SHA256 if conn["metadata"].get("want_authn_requests_signed") else None)
    return request_id, dict(info["headers"])["Location"]


def finish(conn, keys, base, saml_response, request_ids):
    """Check the IdP's answer. Returns {"request_id", "assertion_id", "expires",
    "email", "name", "sent" (the NameID and attributes)}; raises SamlError if it
    can't be used."""
    with _client(conn, keys, base) as client:
        try:
            response = client.parse_authn_request_response(
                saml_response, BINDING_HTTP_POST, outstanding={r: "/" for r in request_ids})
        except Exception as e:             # pysaml2 has many error types; all mean "no"
            raise SamlError(f"{type(e).__name__}: {e}") from None
    if response is None or response.assertion is None:
        raise SamlError("No assertion in the response")
    if response.issuer() != conn["issuer"]:
        raise SamlError(f"Issued by {response.issuer()!r}, not {conn['issuer']!r}")
    identity = response.get_identity() or {}
    email = _email(identity, response.name_id, conn["metadata"].get("email_attribute"))
    if not email:
        raise SamlError(f"No email in the assertion (attributes: {sorted(identity)})")
    expires = None
    subject = response.assertion.subject
    for confirmation in (subject.subject_confirmation if subject else []) or []:
        data = confirmation.subject_confirmation_data
        if data is not None and data.not_on_or_after:
            expires = data.not_on_or_after
    sent = {"NameID": response.name_id.text if response.name_id is not None else None,
            **identity}
    return {"request_id": response.in_response_to, "assertion_id": response.assertion.id,
            "expires": expires, "email": email, "name": _first(identity, NAME_ATTRIBUTES),
            "sent": sent}


def _first(identity, names):
    lowered = {k.lower(): v for k, v in identity.items()}
    for name in names:
        values = lowered.get(name.lower())
        if values:
            return str(values[0]).strip()
    return None


def _email(identity, name_id, attribute=None):
    value = _first(identity, ((attribute,) if attribute else ()) + EMAIL_ATTRIBUTES)
    if not value and name_id is not None and _EMAIL.match((name_id.text or "").strip()):
        value = name_id.text
    value = (value or "").strip().lower()
    return value if _EMAIL.match(value) else None


# ---- The IdP's side: its metadata ------------------------------------------------
def read_idp_metadata(xml):
    """What we need from an IdP's metadata XML: {"entity_id", "sso_url",
    "want_authn_requests_signed", "certificates"}. Raises SamlError saying what's
    missing."""
    store = InMemoryMetaData(ac_factory(), metadata=xml)
    try:
        store.load()
    except Exception as e:
        raise SamlError(f"That isn't valid SAML metadata ({type(e).__name__}).") from None
    idps = [(eid, entity) for eid, entity in store.items() if entity.get("idpsso_descriptor")]
    if len(idps) != 1:
        raise SamlError("The metadata must describe exactly one identity provider "
                        f"(found {len(idps)}).")
    entity_id, entity = idps[0]
    descriptor = entity["idpsso_descriptor"][0]
    sso_url = next((s["location"] for s in descriptor.get("single_sign_on_service", [])
                    if s.get("binding") == BINDING_HTTP_REDIRECT), None)
    if not sso_url:
        raise SamlError("The identity provider has no single sign-on address for the "
                        "HTTP-Redirect binding.")
    certificates = [kd for kd in descriptor.get("key_descriptor", [])
                    if kd.get("use") in (None, "signing")]
    if not certificates:
        raise SamlError("The metadata has no signing certificate.")
    return {"entity_id": entity_id, "sso_url": sso_url,
            "want_authn_requests_signed":
                str(descriptor.get("want_authn_requests_signed", "")).lower() in ("true", "1"),
            "certificates": len(certificates)}


def manual_idp_metadata(entity_id, sso_url, certificate):
    """Metadata XML built from the three values an IdP's setup page shows."""
    body = re.sub(r"-----(BEGIN|END) CERTIFICATE-----|\s", "", certificate or "")
    try:
        x509.load_der_x509_certificate(base64.b64decode(body, validate=True))
    except ValueError:
        raise SamlError("The certificate isn't a valid X.509 certificate (PEM or base64).") \
            from None
    e = lambda v: html.escape(v, quote=True)        # noqa: E731
    return (
        '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID="{e(entity_id)}"><md:IDPSSODescriptor '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        '<md:KeyDescriptor use="signing"><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
        f'<ds:X509Data><ds:X509Certificate>{body}</ds:X509Certificate></ds:X509Data>'
        '</ds:KeyInfo></md:KeyDescriptor>'
        f'<md:SingleSignOnService Binding="{BINDING_HTTP_REDIRECT}" Location="{e(sso_url)}"/>'
        '</md:IDPSSODescriptor></md:EntityDescriptor>')
