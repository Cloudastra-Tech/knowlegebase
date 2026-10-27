"""
connectors/s3.py  —  sync documents from an Amazon S3 bucket (or an
S3-compatible store) into a workspace's index.

Read-only: two S3 calls, ListObjectsV2 and GetObject, signed with Signature
Version 4 by connectors/aws.py (boto3 isn't a dependency).

Connecting, two ways (the card's form):
  ROLE (preferred)   the customer creates an IAM role in their account whose
                     trust policy lets this server's AWS principal assume it
                     only with this workspace's EXTERNAL ID (the confused-deputy
                     defence: we make the id, unique per workspace, and the
                     customer can't pick another workspace's). Each sync asks
                     STS AssumeRole for one-hour credentials, renewed at 80 %
                     of their life. Needs the server's own IAM user
                     (S3_ROLE_ACCESS_KEY_ID / S3_ROLE_SECRET_ACCESS_KEY, allowed
                     sts:AssumeRole) and S3_EXTERNAL_ID_SECRET, from which each
                     workspace's id is derived (HMAC); the id is kept with the
                     connection, so changing that secret only changes ids shown
                     for new connections. S3_ROLE_PRINCIPAL_ARN (the server's
                     IAM ARN) is shown in the trust policy the form suggests.
  ACCESS KEYS        an access key id and secret of an IAM user that may read
                     the bucket; also the only way for an S3-compatible
                     endpoint (MinIO, R2, ...), which must be a public https
                     address (url_guard, as Outline's).
Either way the keys and temporary credentials are sealed with the workspace's
key (integrations_store -> secret_box) and never shown again.

What is synced: the bucket (or the prefix given at connect), narrowed to the
"folders" (prefixes) an admin picks on the card; objects whose type
connectors/extract.py reads (PDF, Word, PowerPoint, Excel, HTML, Markdown, text,
CSV), each up to MAX_BYTES. Folder markers (keys ending in "/") are skipped.

How a sync finds what changed: S3 has no change feed we can read without the
customer wiring up Event Notifications, so every run LISTS the picked prefixes
(ListObjectsV2, 1,000 keys a page, continuation tokens) and compares each
object's ETag with the one synced: an unchanged ETag costs nothing, a changed
one is fetched again, and a key no longer listed is deleted (behind the
mass-delete guard). The ETag isn't always an MD5 (multipart uploads,
SSE-KMS), but it changes whenever the object does, which is all this needs.

Permissions: S3 objects carry no sharing a person could be matched on (bucket
policies and IAM grant AWS principals, not people), so there is NO source
acl: who may ask is the connection's policy (workspace or HR and admins), and
the "as shared at the source" policy isn't offered for it (ACL = False; an
S3 acl that reached kb/services/permissions.py would be for no one).

Errors: a wrong region (301 / AuthorizationHeaderMalformed) or a bucket that's
gone is `config`; AccessDenied is `forbidden`; keys or a role that stopped
working (InvalidAccessKeyId, SignatureDoesNotMatch, STS AccessDenied) are
`auth_expired`; 503 SlowDown and 5xx are retried with backoff by
connectors/http.py.

API references (checked 2026-10-27):
  ListObjectsV2   https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html
  GetObject       https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html
  errors          https://docs.aws.amazon.com/AmazonS3/latest/API/ErrorResponses.html
  ETag            https://docs.aws.amazon.com/AmazonS3/latest/API/API_Object.html
  addressing      https://docs.aws.amazon.com/AmazonS3/latest/userguide/VirtualHosting.html
  bucket names    https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html
  AssumeRole      https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html
  external id     https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html
                  https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for_user_externalid.html
  throttling      https://docs.aws.amazon.com/AmazonS3/latest/userguide/optimizing-performance.html
"""

import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse

import integrations_store as store
from connectors import aws, extract, http
from connectors.base import (AuthExpired, ConfigError, Connector, Cursor, Delete, Fetched,
                             Forbidden, FormError, Item, LimitReached, NotFound, Skipped, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "s3"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
RATE = 20                              # calls a second per set of credentials
MAX_BYTES = 20 * 1024 * 1024           # objects bigger than this are skipped
PAGE = 1000                            # ListObjectsV2's most keys a page
ROLE_SECONDS = 3600                    # AssumeRole session length
RENEW_AT = 0.8                         # renew temporary credentials at 80 % of their life
SESSION_NAME = "knowledge-assistant-sync"
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$")
_ROLE_ARN = re.compile(r"^arn:aws(-[a-z]+)*:iam::\d{12}:role/[\w+=,.@/-]{1,512}$")
_ACCESS_KEY = re.compile(r"^[A-Za-z0-9]{16,128}$")
# Answers that mean the credentials themselves no longer work (ErrorResponses.html).
DEAD_CREDENTIALS = ("InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidToken",
                    "AccountProblem", "InvalidSecurity")
EXPIRED = ("ExpiredToken", "TokenRefreshRequired")
WRONG_REGION = ("AuthorizationHeaderMalformed", "PermanentRedirect", "IllegalLocationConstraintException")


def external_id(workspace):
    """This workspace's ExternalId for the role's trust policy, or None when the
    server can't assume roles."""
    secret = os.getenv("S3_EXTERNAL_ID_SECRET", "")
    if not secret:
        return None
    digest = hmac.new(secret.encode(), f"s3-external-id:{workspace}".encode(),
                      hashlib.sha256).hexdigest()
    return f"kb-{digest[:40]}"


def _server_creds():
    key, secret = os.getenv("S3_ROLE_ACCESS_KEY_ID", ""), os.getenv("S3_ROLE_SECRET_ACCESS_KEY", "")
    return aws.Credentials(key, secret) if key and secret else None


def roles_available():
    return bool(_server_creds() and os.getenv("S3_EXTERNAL_ID_SECRET"))


def trust_policy(workspace):
    """The trust policy the form suggests for the customer's role."""
    principal = os.getenv("S3_ROLE_PRINCIPAL_ARN") or "<this server's IAM user ARN>"
    return {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"AWS": principal}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"sts:ExternalId": external_id(workspace) or ""}}}]}


def read_policy(bucket, prefix=""):
    """The least the role (or user) needs: list the bucket, read its objects."""
    bucket = bucket or "<bucket>"
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{bucket}"},
        {"Effect": "Allow", "Action": "s3:GetObject",
         "Resource": f"arn:aws:s3:::{bucket}/{prefix}*"}]}


def _prefix(value):
    p = (value or "").strip().lstrip("/")
    if p and not p.endswith("/"):
        p += "/"
    if len(p) > 1024 or any(ord(ch) < 32 for ch in p):
        raise FormError("That prefix can't be used.")
    return p


def _endpoint(value):
    v = (value or "").strip().rstrip("/")
    if not v:
        return ""
    parts = urllib.parse.urlsplit(v)
    if not parts.scheme or not parts.hostname or parts.path not in ("", "/") or parts.query:
        raise FormError("Enter the endpoint as an address, like https://s3.example.com.")
    return f"{parts.scheme}://{parts.netloc}"


def _sid(key):
    return f"s3:{key}"


def _key(source_id):
    return source_id.removeprefix("s3:")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_listing(body):
    """(objects [{key, etag, size, modified}], prefixes, next continuation token or
    None) from a ListObjectsV2 answer."""
    root = aws.xml(body)
    objects, prefixes, token, truncated = [], [], None, False
    for el in root:
        name = _local(el.tag)
        if name == "Contents":
            o = {_local(c.tag): (c.text or "") for c in el}
            objects.append({"key": o.get("Key", ""), "etag": o.get("ETag", "").strip('"'),
                            "size": int(o.get("Size") or 0), "modified": o.get("LastModified")})
        elif name == "CommonPrefixes":
            prefixes += [(c.text or "") for c in el if _local(c.tag) == "Prefix"]
        elif name == "NextContinuationToken":
            token = el.text
        elif name == "IsTruncated":
            truncated = (el.text or "").strip().lower() == "true"
    return objects, prefixes, token if truncated else None


class S3(Connector):
    PROVIDER = PROVIDER
    LABEL = "Amazon S3"
    DESCRIPTION = "PDFs, Office and text files from a bucket (or an S3-compatible store)"
    AUTH = "token"
    PICKER = "tree"
    PICKER_REQUIRED = False
    SETTINGS_KEY = "prefixes"
    UNIT = "files"
    ACL = False
    NOTE = ""
    SOURCE_POLICY_NOTE = ("S3 objects carry no per-person sharing to follow, so this choice "
                          "isn't available for S3.")

    # ---- connecting ---------------------------------------------------------------
    def connect_form(self, workspace, form, user):
        bucket = (form.get("bucket") or "").strip().lower()
        if bucket.startswith("s3://"):
            bucket = bucket[5:].split("/")[0]
        if not _BUCKET.match(bucket) or ".." in bucket:
            raise FormError("Enter the bucket's name, like acme-handbook.")
        region = (form.get("region") or "").strip().lower()
        endpoint = _endpoint(form.get("endpoint"))
        if not _REGION.match(region) and not (endpoint and region):
            raise FormError("Enter the bucket's region, like eu-west-1.")
        prefix = _prefix(form.get("prefix"))
        settings = {"bucket": bucket, "region": region, "prefix": prefix}
        if endpoint:
            settings["endpoint"] = endpoint
        role_arn = (form.get("role_arn") or "").strip()
        if role_arn:
            if endpoint:
                raise FormError("An S3-compatible endpoint connects with access keys, not a role.")
            if not _ROLE_ARN.match(role_arn):
                raise FormError("Enter the role's ARN, like arn:aws:iam::123456789012:role/kb-read.")
            if not roles_available():
                raise FormError("This server isn't set up to assume roles. Use access keys, or "
                                "ask your admin to set S3_ROLE_ACCESS_KEY_ID, "
                                "S3_ROLE_SECRET_ACCESS_KEY and S3_EXTERNAL_ID_SECRET.")
            settings["external_id"] = external_id(workspace)
            try:
                secret = {"mode": "role", "role_arn": role_arn,
                          **self._assume(role_arn, settings["external_id"], region)}
            except AuthExpired:
                raise FormError("AWS didn't let this server assume that role. Check its trust "
                                "policy names this server and the external ID shown.") from None
            account = f"{bucket} (role {role_arn.rsplit('/', 1)[-1]})"
        else:
            key = (form.get("access_key_id") or "").strip()
            secret_key = (form.get("secret_access_key") or "").strip()
            if not _ACCESS_KEY.match(key) or not secret_key:
                raise FormError("Enter an access key ID and its secret access key, or a role ARN.")
            secret = {"mode": "keys", "access_key": key, "secret_key": secret_key}
            account = f"{bucket} (key {key[:4]}…{key[-4:]})"
        try:
            next(self._list(settings, aws.Credentials(secret["access_key"], secret["secret_key"],
                                                      secret.get("session_token")),
                            prefix, max_keys=1))
        except AuthExpired:
            raise FormError("AWS didn't accept those credentials.") from None
        except Forbidden:
            raise FormError("Those credentials can't list that bucket: they need s3:ListBucket "
                            "and s3:GetObject.") from None
        except NotFound:
            raise FormError("There's no bucket by that name.") from None
        except ConfigError as e:
            raise FormError(f"Couldn't use that bucket: {e}") from None
        except Exception:
            raise FormError("Couldn't reach the bucket.") from None
        row = store.get(workspace, PROVIDER)
        old = (row or {}).get("settings") or {}
        store.connect(workspace, PROVIDER, account, secret, user)
        same = all(old.get(k) == settings.get(k) for k in ("bucket", "prefix", "endpoint"))
        merged = dict(old, **settings)
        if not endpoint:
            merged.pop("endpoint", None)
        if not same:
            merged["prefixes"] = []            # another bucket: its folders differ
        store.update(workspace, PROVIDER, settings=merged)
        return account

    def card(self, workspace, settings):
        """What the card and the form show besides the generic fields."""
        return {"bucket": settings.get("bucket"), "region": settings.get("region"),
                "prefix": settings.get("prefix") or "", "endpoint": settings.get("endpoint"),
                "roles": roles_available(),
                "external_id": settings.get("external_id") or external_id(workspace),
                "trust_policy": trust_policy(workspace) if roles_available() else None,
                "read_policy": read_policy(settings.get("bucket"), settings.get("prefix") or "")}

    # ---- credentials --------------------------------------------------------------
    def _assume(self, role_arn, ext_id, region):
        url, body = aws.assume_role_request(region, role_arn, ext_id, SESSION_NAME, ROLE_SECONDS)
        headers = aws.sign("POST", url, region=region, service="sts", creds=_server_creds(),
                           body=body, headers={"Content-Type":
                                               "application/x-www-form-urlencoded; charset=utf-8"})
        r = http.request("POST", url, retries=2, ok=(400, 403), data=body, headers=headers)
        if r.status_code in (400, 403):
            raise AuthExpired(f"STS refused AssumeRole ({aws.error_code(r.content) or r.status_code})")
        creds, expiration = aws.parse_assume_role(r.content)
        try:
            ends = datetime.datetime.fromisoformat(expiration.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ends = time.time() + ROLE_SECONDS
        now = time.time()
        return {"access_key": creds.access_key, "secret_key": creds.secret_key,
                "session_token": creds.session_token, "expires_at": ends,
                "renew_at": now + (ends - now) * RENEW_AT}

    def _creds(self, workspace, force=False):
        """The connection's credentials: its keys, or temporary ones for its role
        (assumed again under a lock when they're past RENEW_AT, or `force`)."""
        with http.refresh_lock(f"{PROVIDER}:{workspace}"):
            sec = store.secret(workspace, PROVIDER)
            if not sec or not (sec.get("access_key") or sec.get("mode") == "role"):
                raise AuthExpired("S3 is not connected")
            if sec.get("mode") == "role" and (force or sec.get("renew_at", 0) <= time.time()):
                row = store.get(workspace, PROVIDER) or {}
                settings = row.get("settings") or {}
                sec.update(self._assume(sec["role_arn"], settings.get("external_id") or "",
                                        settings.get("region") or "us-east-1"))
                store.update(workspace, PROVIDER, secret=sec)   # saved before it's used
            return aws.Credentials(sec["access_key"], sec["secret_key"], sec.get("session_token"))

    # ---- calls --------------------------------------------------------------------
    def _url(self, settings, key="", params=None):
        bucket, region = settings["bucket"], settings.get("region") or "us-east-1"
        path = urllib.parse.quote(key, safe="/-_.~")
        q = ("?" + aws.query(params)) if params else ""
        if settings.get("endpoint"):                   # S3-compatible: path-style
            return f"{settings['endpoint']}/{bucket}/{path}{q}"
        if "." in bucket:                              # dots break the wildcard certificate
            return f"https://s3.{region}.amazonaws.com/{bucket}/{path}{q}"
        return f"https://{bucket}.s3.{region}.amazonaws.com/{path}{q}"

    def _call(self, settings, creds, url, workspace=None, **kwargs):
        """One signed S3 GET. S3's own error codes are mapped here (see the top)."""
        guard = http.guard_public if settings.get("endpoint") else None
        for attempt in (0, 1):
            headers = aws.sign("GET", url, region=settings.get("region") or "us-east-1",
                               service="s3", creds=creds)
            r = http.request("GET", url, bucket=http.bucket_key(PROVIDER, creds.access_key),
                             rate=RATE, burst=RATE, guard=guard, ok=(301, 307, 400, 403),
                             headers=headers, allow_redirects=False, **kwargs)
            if r.status_code < 300:
                return r
            code = aws.error_code(r.content) if r.content else ""
            if r.status_code in (301, 307) or code in WRONG_REGION:
                raise ConfigError(f"the bucket isn't in {settings.get('region')} ({code or r.status_code})")
            if code in EXPIRED and workspace and attempt == 0:
                creds = self._creds(workspace, force=True)
                continue
            if code in DEAD_CREDENTIALS or code in EXPIRED:
                raise AuthExpired(f"S3 refused the credentials ({code})")
            if r.status_code == 403:
                raise Forbidden(f"S3 answered 403 ({code or 'AccessDenied'})")
            raise ConfigError(f"S3 answered {r.status_code} ({code or 'no code'})")
        raise AuthExpired("S3 refused the renewed credentials")

    def _list(self, settings, creds, prefix, delimiter=None, max_keys=PAGE, workspace=None):
        """Every page of a ListObjectsV2 listing: yields (objects, prefixes)."""
        token = None
        while True:
            params = {"list-type": "2", "prefix": prefix, "max-keys": str(max_keys)}
            if delimiter:
                params["delimiter"] = delimiter
            if token:
                params["continuation-token"] = token
            r = self._call(settings, creds, self._url(settings, params=params), workspace)
            objects, prefixes, token = parse_listing(r.content)
            yield objects, prefixes
            if not token or max_keys < PAGE:
                return

    def _conn(self, workspace):
        row = store.get(workspace, PROVIDER)
        if not row:
            raise AuthExpired("S3 is not connected")
        settings = row.get("settings") or {}
        if not settings.get("bucket"):
            raise ConfigError("no bucket")
        return settings, self._creds(workspace)

    # ---- picking ------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        """The "folders" (common prefixes) at the top of the bucket's prefix, or
        inside `parent`."""
        settings, creds = self._conn(ctx.workspace)
        base = settings.get("prefix") or ""
        under = parent if parent and parent.startswith(base) else base
        out = []
        try:
            for _, prefixes in self._list(settings, creds, under, delimiter="/",
                                          workspace=ctx.workspace):
                out += [{"id": p, "name": p[len(under):].rstrip("/") or p, "kind": "folder",
                         "has_children": True} for p in prefixes]
        except NotFound:
            raise ConfigError("the bucket is gone") from None
        return sorted(out, key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = super().check_containers(picked)
        for p in out:
            p["kind"] = "folder"
            if not p["id"].endswith("/"):
                raise FormError("Pick folders of the bucket.")
        return out

    # ---- syncing ------------------------------------------------------------------
    def changes(self, ctx, cursor):
        settings, creds = self._conn(ctx.workspace)
        base = settings.get("prefix") or ""
        picks = [p["id"] for p in (ctx.settings or {}).get("prefixes") or []
                 if str(p.get("id", "")).startswith(base)]
        roots = sorted(set(picks)) or [base]
        # A pick inside another pick is listed once, with the outer one.
        roots = [r for r in roots if not any(o != r and r.startswith(o) for o in roots)]
        seen, n = set(), 0
        try:
            for root in roots:
                for objects, _ in self._list(settings, creds, root, workspace=ctx.workspace):
                    for o in objects:
                        key = o["key"]
                        if not key or key.endswith("/"):
                            continue                         # a "folder" marker
                        sid = _sid(key)
                        name = key.rsplit("/", 1)[-1]
                        if extract.kind_of("", name) is None:
                            if sid in ctx.known:             # renamed to a type we don't read
                                yield Delete(sid)
                            continue
                        if ctx.max_items and n >= ctx.max_items:
                            yield LimitReached(ctx.max_items)
                            yield Cursor(cursor)             # deletes nothing; tries again
                            return
                        seen.add(sid)
                        n += 1
                        if o["size"] > MAX_BYTES:
                            yield Skipped(sid, "too_large", name)
                            continue
                        folder = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
                        yield Upsert(Item(
                            source_id=sid, title=name, url=self._console(settings, key),
                            version=o["etag"] or o["modified"] or "", kind="file", mime="",
                            size=o["size"], modified=o["modified"], parent_id=folder or None,
                            acl={"via": "s3"}, attrs={"key": key}))
        except NotFound:
            raise ConfigError("the bucket is gone") from None
        yield from ctx.missing(seen)
        sig = hashlib.sha256(json.dumps(roots).encode()).hexdigest()[:16]
        yield Cursor(json.dumps({"roots": sig, "full_at": time.time()}), full=True)

    def _console(self, settings, key):
        """Where a citation links: the object in the AWS console (people sign in
        there; the bucket's objects aren't public)."""
        if settings.get("endpoint"):
            return ""
        return ("https://s3.console.aws.amazon.com/s3/object/" + settings["bucket"] + "?"
                + urllib.parse.urlencode({"region": settings.get("region") or "us-east-1",
                                          "prefix": key}))

    def fetch(self, ctx, item):
        if item.size and item.size > MAX_BYTES:
            return None
        settings, creds = self._conn(ctx.workspace)
        key = (item.attrs or {}).get("key") or _key(item.source_id)
        r = self._call(settings, creds, self._url(settings, key), ctx.workspace, timeout=60)
        mime = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        if extract.kind_of(mime) is None:                # S3's binary/octet-stream: go by the name
            mime = ""
        return Fetched(r.content, mime)

    def disconnect(self, workspace):
        """Nothing to revoke from here: the keys or the role's temporary
        credentials are deleted with the connection. The customer deletes the
        role (or deactivates the access key) in IAM to remove our access there."""
        log.info("S3: workspace %s disconnected; its credentials are deleted here", workspace)


CONNECTOR = S3()
