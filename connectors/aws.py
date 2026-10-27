"""
connectors/aws.py  —  the little of AWS the S3 connector needs, without an SDK:
Signature Version 4 request signing and STS AssumeRole.

    sign(method, url, region=, service=, creds=, headers=, body=)
        the headers that make one request signed (Authorization, x-amz-date,
        x-amz-content-sha256, and x-amz-security-token for temporary
        credentials). `url` is sent exactly as given: build it with query()
        so the query string is the canonical one.
    query(params)       a canonical query string (sorted, RFC 3986 encoded)
    assume_role(...)    temporary credentials for a customer's role, with the
                        ExternalId its trust policy requires (connectors/s3.py)

boto3 isn't a dependency of the app, and the connector needs only two calls
(ListObjectsV2, GetObject) plus AssumeRole, so the signing is done here and
checked against AWS's own worked examples (tests/test_connector_s3.py).

References (checked 2026-10-27):
  SigV4 steps        https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv-create-signed-request.html
  S3 header signing  https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
                     (worked examples: GET object with a Range, ListObjects)
  AssumeRole         https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html
  STS query API      https://docs.aws.amazon.com/STS/latest/APIReference/CommonParameters.html
"""

import dataclasses
import datetime
import hashlib
import hmac
import urllib.parse

from defusedxml import ElementTree

ALGORITHM = "AWS4-HMAC-SHA256"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
STS_NS = "{https://sts.amazonaws.com/doc/2011-06-15/}"


@dataclasses.dataclass
class Credentials:
    access_key: str
    secret_key: str
    session_token: str | None = None


def _quote(value, safe="-_.~"):
    """RFC 3986 percent-encoding, as SigV4 wants it (spaces as %20, never +)."""
    return urllib.parse.quote(str(value), safe=safe)


def query(params):
    """The canonical query string: every name and value encoded, sorted by name."""
    pairs = sorted((_quote(k), _quote(v)) for k, v in (params or {}).items() if v is not None)
    return "&".join(f"{k}={v}" for k, v in pairs)


def _canonical_query(raw):
    pairs = urllib.parse.parse_qsl(raw, keep_blank_values=True)
    return "&".join(f"{k}={v}" for k, v in sorted((_quote(k), _quote(v)) for k, v in pairs))


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key, date, region, service):
    k = _hmac(("AWS4" + secret_key).encode("utf-8"), date)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def canonical_request(method, url, headers, payload_hash):
    """(canonical request, signed header names) for a request whose headers
    (lower-cased names) are final."""
    parts = urllib.parse.urlsplit(url)
    # S3 signs the path as it is sent (each segment encoded once); "/" when empty.
    path = parts.path or "/"
    names = sorted(headers)
    canonical_headers = "".join(f"{n}:{' '.join(str(headers[n]).split())}\n" for n in names)
    signed = ";".join(names)
    return ("\n".join([method.upper(), path, _canonical_query(parts.query), canonical_headers,
                       signed, payload_hash]), signed)


def sign(method, url, *, region, service, creds, headers=None, body=b"", now=None,
         payload_hash=None):
    """The request's headers with the SigV4 ones added (see the top of this file)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    if isinstance(body, str):
        body = body.encode("utf-8")
    payload_hash = payload_hash or hashlib.sha256(body or b"").hexdigest()
    out = {k.lower(): v for k, v in (headers or {}).items()}
    out["host"] = urllib.parse.urlsplit(url).netloc
    out["x-amz-date"] = amz_date
    if service == "s3":
        out["x-amz-content-sha256"] = payload_hash
    if creds.session_token:
        out["x-amz-security-token"] = creds.session_token
    request, signed = canonical_request(method, url, out, payload_hash)
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join([ALGORITHM, amz_date, scope,
                         hashlib.sha256(request.encode("utf-8")).hexdigest()])
    signature = hmac.new(signing_key(creds.secret_key, date, region, service),
                         to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out["authorization"] = (f"{ALGORITHM} Credential={creds.access_key}/{scope}, "
                            f"SignedHeaders={signed}, Signature={signature}")
    out.pop("host")                    # requests sets it from the URL (the same value)
    return out


def xml(body):
    """An XML answer's root element (defusedxml: no entity tricks)."""
    return ElementTree.fromstring(body)


def error_code(body):
    """The <Code> of an S3 or STS error answer, or ""."""
    try:
        root = xml(body)
    except Exception:
        return ""
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "Code":
            return (el.text or "").strip()
    return ""


def assume_role_request(region, role_arn, external_id, session_name, duration=3600):
    """(url, form body) of an STS AssumeRole call (the Query API, POSTed)."""
    host = f"sts.{region}.amazonaws.com"
    body = query({"Action": "AssumeRole", "Version": "2011-06-15", "RoleArn": role_arn,
                  "RoleSessionName": session_name, "ExternalId": external_id,
                  "DurationSeconds": str(duration)})
    return f"https://{host}/", body


def parse_assume_role(body):
    """(Credentials, expiration as ISO 8601) from an AssumeRole answer."""
    root = xml(body)
    c = root.find(f"{STS_NS}AssumeRoleResult/{STS_NS}Credentials")
    if c is None:
        raise ValueError("no credentials in the AssumeRole answer")

    def text(name):
        el = c.find(f"{STS_NS}{name}")
        return (el.text or "").strip() if el is not None else ""
    return (Credentials(text("AccessKeyId"), text("SecretAccessKey"), text("SessionToken")),
            text("Expiration"))
