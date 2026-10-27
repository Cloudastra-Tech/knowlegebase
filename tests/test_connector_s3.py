"""
Amazon S3 against recorded HTTP (tests/fixtures/connectors/s3/, written from
the shapes in the S3 and STS API references; an unrecorded request fails the
test), through the real framework (kb/services/sync.py): the SigV4 signer
checked against AWS's worked examples, connecting with access keys or an
assumed role (ExternalId, temporary credentials renewed), the prefix picker,
the list-and-diff sync by ETag, deletes behind the mass-delete guard, no
source acl (the source policy isn't offered), throttling and typed errors.
"""

import datetime
import json
import time
import urllib.parse

import pytest

from connector_fixtures import Recorded, make_pdf
from connectors import aws, base
from test_connector_providers import env  # noqa: F401  (the shared fixture)

B = "https://acme-handbook.s3.eu-west-1.amazonaws.com/"
STS = "https://sts.eu-west-1.amazonaws.com/"
NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
ROLE = "arn:aws:iam::123456789012:role/kb-read"


def empty_listing():
    return (f'<ListBucketResult {NS}><Name>acme-handbook</Name><KeyCount>0</KeyCount>'
            "<IsTruncated>false</IsTruncated></ListBucketResult>")


def error(code):
    return f"<Error><Code>{code}</Code><Message>no</Message><RequestId>1</RequestId></Error>"


def assumed(n=1, expires="2099-01-01T00:00:00Z"):
    return ('<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
            "<AssumeRoleResult><Credentials>"
            f"<AccessKeyId>ASIATEMP{n:012d}</AccessKeyId><SecretAccessKey>temp-secret-{n}"
            f"</SecretAccessKey><SessionToken>session-{n}</SessionToken>"
            f"<Expiration>{expires}</Expiration></Credentials>"
            f"<AssumedRoleUser><Arn>{ROLE}/s</Arn><AssumedRoleId>AROA:s</AssumedRoleId>"
            "</AssumedRoleUser></AssumeRoleResult></AssumeRoleResponse>")


def connect_keys(env, **settings):
    import integrations_store
    integrations_store.connect(env.ws, "s3", "acme-handbook (key AKIA…MPLE)",
                               {"mode": "keys", "access_key": "AKIAIOSFODNN7EXAMPLE",
                                "secret_key": "test-secret"}, "admin")
    integrations_store.update(env.ws, "s3", settings={
        "bucket": "acme-handbook", "region": "eu-west-1", "prefix": "handbook/", **settings})


def item(env, sid):
    import sync_store
    return sync_store.item(env.ws, "s3", sid)


def card(env):
    return next(i for i in env.client.get("/api/integrations", headers=env.headers())
                .json()["integrations"] if i["id"] == "s3")


@pytest.fixture
def roles(monkeypatch):
    monkeypatch.setenv("S3_ROLE_ACCESS_KEY_ID", "AKIASERVER0000000001")
    monkeypatch.setenv("S3_ROLE_SECRET_ACCESS_KEY", "server-secret")
    monkeypatch.setenv("S3_EXTERNAL_ID_SECRET", "external-id-secret")
    monkeypatch.setenv("S3_ROLE_PRINCIPAL_ARN", "arn:aws:iam::999999999999:user/kb-sync")


# ---- SigV4 -----------------------------------------------------------------------------
EXAMPLE = aws.Credentials("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
EXAMPLE_TIME = datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize("url, headers, signed, signature", [
    # https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html,
    # "Example: GET Object" and "Example: GET Bucket (List Objects)".
    ("https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"},
     "host;range;x-amz-content-sha256;x-amz-date",
     "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"),
    ("https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J", {},
     "host;x-amz-content-sha256;x-amz-date",
     "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"),
])
def test_sigv4_matches_the_worked_examples_in_the_s3_docs(url, headers, signed, signature):
    h = aws.sign("GET", url, region="us-east-1", service="s3", creds=EXAMPLE, headers=headers,
                 now=EXAMPLE_TIME)
    assert h["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        f"SignedHeaders={signed}, Signature={signature}")
    assert h["x-amz-content-sha256"] == aws.EMPTY_SHA256 and h["x-amz-date"] == "20130524T000000Z"


def test_sigv4_agrees_with_botocore_on_odd_keys_and_session_tokens():
    botocore = pytest.importorskip("botocore")   # not a dependency: only where it's installed
    from botocore.auth import S3SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials
    url = "https://b.s3.eu-west-1.amazonaws.com/?" + aws.query(
        {"list-type": "2", "prefix": "a b/ü+x", "continuation-token": "1/2=="})
    r = AWSRequest(method="GET", url=url, headers={})
    S3SigV4Auth(Credentials("AK", "SK", "TOK"), "s3", "eu-west-1").add_auth(r)
    when = datetime.datetime.strptime(r.headers["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(
        tzinfo=datetime.timezone.utc)
    ours = aws.sign("GET", url, region="eu-west-1", service="s3",
                    creds=aws.Credentials("AK", "SK", "TOK"), now=when)
    assert ours["authorization"] == r.headers["Authorization"], botocore.__version__
    assert ours["x-amz-security-token"] == "TOK"


def test_the_canonical_query_is_sorted_and_rfc3986_encoded():
    assert aws.query({"prefix": "a b+c/é", "list-type": "2", "x": None}) == (
        "list-type=2&prefix=a%20b%2Bc%2F%C3%A9")


# ---- connecting ------------------------------------------------------------------------
def test_access_keys_connect_after_listing_the_bucket_and_are_kept_sealed(env, monkeypatch):
    import db
    rec = Recorded(monkeypatch)
    rec.add("GET", B, params={"list-type": "2", "prefix": "handbook/", "max-keys": "1"},
            text=empty_listing())
    r = env.client.post("/api/integrations/s3/setup", headers=env.headers(), json={
        "bucket": "s3://Acme-Handbook", "region": "eu-west-1", "prefix": "/handbook",
        "access_key_id": "AKIAIOSFODNN7EXAMPLE", "secret_access_key": "the-secret-key"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "account": "acme-handbook (key AKIA…MPLE)", "choose": False}
    [call] = rec.calls
    auth = call["headers"]["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/")
    assert "/eu-west-1/s3/aws4_request" in auth
    with db.connect() as conn:
        blob = conn.execute("SELECT secret FROM integrations WHERE workspace = ? AND "
                            "provider = 's3'", (env.ws,)).fetchone()[0]
    assert b"the-secret-key" not in blob and b"AKIAIOSFODNN7EXAMPLE" not in blob
    c = card(env)
    assert (c["s3"]["bucket"], c["s3"]["prefix"], c["s3"]["roles"]) == (
        "acme-handbook", "handbook/", False)
    assert c["s3"]["read_policy"]["Statement"][1]["Resource"] == (
        "arn:aws:s3:::acme-handbook/handbook/*")


@pytest.mark.parametrize("form, message", [
    ({"bucket": "no", "region": "eu-west-1"}, "bucket's name"),
    ({"bucket": "acme-handbook", "region": "Europe"}, "region"),
    ({"bucket": "acme-handbook", "region": "eu-west-1"}, "access key ID"),
    ({"bucket": "acme-handbook", "region": "eu-west-1", "role_arn": "arn:aws:iam::1:role/x"},
     "role's ARN"),
    ({"bucket": "acme-handbook", "region": "eu-west-1", "role_arn": ROLE},
     "isn't set up to assume roles"),
    ({"bucket": "acme-handbook", "region": "us-east-1", "endpoint": "https://minio.example.com",
      "role_arn": ROLE}, "access keys, not a role"),
])
def test_a_form_that_cant_work_is_refused_before_any_call(env, monkeypatch, form, message):
    rec = Recorded(monkeypatch)
    r = env.client.post("/api/integrations/s3/setup", headers=env.headers(), json=form)
    assert r.status_code == 400 and message in r.json()["detail"]
    assert rec.calls == []


@pytest.mark.parametrize("status, code, message", [
    (403, "InvalidAccessKeyId", "didn't accept those credentials"),
    (403, "AccessDenied", "need s3:ListBucket"),
    (301, "PermanentRedirect", "isn't in eu-west-1"),
    (404, "NoSuchBucket", "no bucket by that name"),
])
def test_s3s_answers_are_explained_in_plain_words(env, monkeypatch, status, code, message):
    import integrations_store
    rec = Recorded(monkeypatch)
    rec.add("GET", B, status=status, text=error(code))
    r = env.client.post("/api/integrations/s3/setup", headers=env.headers(), json={
        "bucket": "acme-handbook", "region": "eu-west-1", "access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "secret_access_key": "k"})
    assert r.status_code == 400 and message in r.json()["detail"], r.text
    assert integrations_store.get(env.ws, "s3") is None


def test_a_role_is_assumed_with_this_workspaces_external_id(env, monkeypatch, roles):
    import integrations_store
    from connectors import s3
    rec = Recorded(monkeypatch)
    c = card(env)
    ext = c["s3"]["external_id"]
    assert c["s3"]["roles"] is True and ext.startswith("kb-") and ext == s3.external_id(env.ws)
    assert s3.external_id("another-workspace") != ext
    trust = c["s3"]["trust_policy"]["Statement"][0]
    assert trust["Principal"] == {"AWS": "arn:aws:iam::999999999999:user/kb-sync"}
    assert trust["Condition"] == {"StringEquals": {"sts:ExternalId": ext}}
    rec.add("POST", STS, text=assumed(1))
    rec.add("GET", B, params={"max-keys": "1"}, text=empty_listing(),
            req_headers={"x-amz-security-token": "session-1"})
    r = env.client.post("/api/integrations/s3/setup", headers=env.headers(), json={
        "bucket": "acme-handbook", "region": "eu-west-1", "role_arn": ROLE})
    assert r.status_code == 200, r.text
    sts = rec.calls[0]
    form = dict(urllib.parse.parse_qsl(sts["data"]))
    assert form == {"Action": "AssumeRole", "Version": "2011-06-15", "RoleArn": ROLE,
                    "RoleSessionName": "knowledge-assistant-sync", "ExternalId": ext,
                    "DurationSeconds": "3600"}
    assert "Credential=AKIASERVER0000000001/" in sts["headers"]["authorization"]
    assert "/eu-west-1/sts/aws4_request" in sts["headers"]["authorization"]
    sec = integrations_store.secret(env.ws, "s3")
    assert (sec["mode"], sec["access_key"], sec["session_token"]) == (
        "role", "ASIATEMP000000000001", "session-1")
    assert integrations_store.get(env.ws, "s3")["settings"]["external_id"] == ext


def test_a_role_that_doesnt_trust_us_is_refused(env, monkeypatch, roles):
    rec = Recorded(monkeypatch)
    rec.add("POST", STS, status=403, text=error("AccessDenied"))
    r = env.client.post("/api/integrations/s3/setup", headers=env.headers(), json={
        "bucket": "acme-handbook", "region": "eu-west-1", "role_arn": ROLE})
    assert r.status_code == 400 and "external ID" in r.json()["detail"]


def test_role_credentials_are_renewed_before_they_run_out_and_when_s3_says_expired(
        env, monkeypatch, roles):
    import integrations_store
    from connectors import s3
    rec = Recorded(monkeypatch)
    connect_keys(env, external_id=s3.external_id(env.ws))
    integrations_store.update(env.ws, "s3", secret={
        "mode": "role", "role_arn": ROLE, "access_key": "ASIAOLD", "secret_key": "old",
        "session_token": "session-0", "expires_at": time.time() + 600,
        "renew_at": time.time() - 1})                     # past 80 % of its life
    rec.add("POST", STS, text=assumed(1))
    creds = s3.CONNECTOR._creds(env.ws)
    assert creds.session_token == "session-1"
    assert integrations_store.secret(env.ws, "s3")["session_token"] == "session-1"
    assert s3.CONNECTOR._creds(env.ws).session_token == "session-1"   # no new call
    # S3 says the token expired anyway: assumed again once, and the call retried.
    rec.add("GET", B, status=400, text=error("ExpiredToken"))
    rec.add("POST", STS, text=assumed(2))
    rec.add("GET", B, text=empty_listing(), req_headers={"x-amz-security-token": "session-2"})
    ctx = base.SyncContext(workspace=env.ws, provider="s3", settings={}, known={})
    assert s3.CONNECTOR.list_containers(ctx) == []
    assert len(rec.requests_to("sts.")) == 2 and rec.unused() == []


# ---- picking ---------------------------------------------------------------------------
def test_the_picker_walks_the_prefixes_under_the_connections_prefix(env, monkeypatch):
    rec = Recorded(monkeypatch)
    connect_keys(env)
    rec.add("GET", B, params={"prefix": "handbook/", "delimiter": "/"}, text=(
        f"<ListBucketResult {NS}><IsTruncated>false</IsTruncated>"
        "<CommonPrefixes><Prefix>handbook/policies/</Prefix></CommonPrefixes>"
        "<CommonPrefixes><Prefix>handbook/Archive/</Prefix></CommonPrefixes></ListBucketResult>"))
    r = env.client.get("/api/integrations/s3/containers", headers=env.headers())
    assert r.json()["items"] == [
        {"id": "handbook/Archive/", "name": "Archive", "kind": "folder", "has_children": True},
        {"id": "handbook/policies/", "name": "policies", "kind": "folder", "has_children": True}]
    rec.add("GET", B, params={"prefix": "handbook/policies/", "delimiter": "/"},
            text=empty_listing())
    r = env.client.get("/api/integrations/s3/containers", headers=env.headers(),
                       params={"parent": "handbook/policies/"})
    assert r.json()["items"] == []
    r = env.client.put("/api/integrations/s3/containers", headers=env.headers(),
                       json={"containers": [{"id": "handbook/policies", "name": "x"}]})
    assert r.status_code == 400 and "folders" in r.json()["detail"]


# ---- syncing ---------------------------------------------------------------------------
def full(rec):
    rec.load("s3/full")
    rec.add("GET", B + "handbook/expenses.pdf", body=make_pdf("Expenses over 100 need a receipt."),
            headers={"Content-Type": "application/pdf"})


def test_a_sync_lists_every_page_reads_what_extract_can_and_skips_the_rest(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_keys(env)
    full(rec)
    summary = sync.sync(env.ws, "s3")
    assert rec.unused() == []
    assert (summary["upserted"], summary["skipped"], summary["mode"]) == (3, 1, "full")
    policy = item(env, "s3:handbook/leave-policy.md")
    assert "25 days" in env.text("s3", policy["file_name"])
    assert policy["version"] == "0b5a4c1e2b3a7f60a1e6b7c1f0e2d3a4"
    assert policy["acl"] == {"via": "s3"}
    assert policy["url"].startswith("https://s3.console.aws.amazon.com/s3/object/acme-handbook?")
    assert item(env, "s3:handbook/archive/scan.pdf")["skipped_reason"] == "too_large"
    assert item(env, "s3:handbook/logo.png") is None              # a type we don't read
    assert item(env, "s3:handbook/") is None                      # a folder marker
    assert item(env, "s3:handbook/expenses.pdf")["file_name"].endswith(".pdf")
    assert sync_store.state(env.ws, "s3")["cursor"]


def test_then_only_objects_whose_etag_changed_are_fetched_and_gone_ones_deleted(env,
                                                                               monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_keys(env)
    full(rec)
    sync.sync(env.ws, "s3")
    rec.load("s3/incremental")
    before = len(rec.calls)
    summary = sync.sync(env.ws, "s3")
    assert rec.unused() == []
    fetched = [c["url"] for c in rec.calls[before:] if not c["url"].endswith("/")]
    assert fetched == [B + "handbook/leave-policy.md"]           # the PDF's ETag is the same
    assert (summary["upserted"], summary["unchanged"], summary["deleted"]) == (1, 1, 1)
    assert "28 days" in env.text("s3", item(env, "s3:handbook/leave-policy.md")["file_name"])
    assert item(env, "s3:handbook/onboarding.txt") is None


def test_picked_prefixes_are_listed_instead_of_the_whole_prefix(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_keys(env, prefixes=[{"id": "handbook/a/", "name": "a", "kind": "folder"},
                                {"id": "handbook/a/b/", "name": "b", "kind": "folder"},
                                {"id": "other/", "name": "outside", "kind": "folder"}])
    rec.add("GET", B, params={"prefix": "handbook/a/"}, text=empty_listing())
    sync.sync(env.ws, "s3")
    assert [c["params"]["prefix"] for c in rec.calls] == ["handbook/a/"]


def test_a_listing_that_lost_everything_is_held_for_an_admin(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_keys(env)
    full(rec)
    sync.sync(env.ws, "s3")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    rec.add("GET", B, text=empty_listing())
    summary = sync.sync(env.ws, "s3")
    assert (summary["held_deletes"], summary["deleted"]) == (3, 0)
    assert card(env)["held_deletes"] == 3
    rec.add("GET", B, text=empty_listing())
    assert sync.sync(env.ws, "s3", confirm_deletes=True)["deleted"] == 3
    assert env.files("s3") == []


def test_slow_down_is_retried_and_dead_keys_need_a_reconnect(env, monkeypatch):
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_keys(env)
    rec.add("GET", B, status=503, text=error("SlowDown"))
    rec.add("GET", B, text=empty_listing())
    sync.sync(env.ws, "s3")
    assert len(rec.slept) >= 1
    rec.add("GET", B, status=403, text=error("InvalidAccessKeyId"))
    with pytest.raises(base.AuthExpired):
        sync.sync(env.ws, "s3")
    assert card(env)["error"]["code"] == "auth_expired"
    rec.add("GET", B, status=403, text=error("AccessDenied"))
    with pytest.raises(base.Forbidden):
        sync.sync(env.ws, "s3")
    rec.add("GET", B, status=404, text=error("NoSuchBucket"))
    with pytest.raises(base.ConfigError):
        sync.sync(env.ws, "s3")


def test_an_s3_compatible_endpoint_is_path_style_and_must_be_public(env, monkeypatch):
    rec = Recorded(monkeypatch)
    rec.hosts["minio.internal.example"] = "10.0.0.5"
    r = env.client.post("/api/integrations/s3/setup", headers=env.headers(), json={
        "bucket": "docs", "region": "us-east-1", "endpoint": "https://minio.internal.example",
        "access_key_id": "MINIOACCESSKEY01", "secret_access_key": "k"})
    assert r.status_code == 400 and rec.calls == []
    rec.add("GET", "https://files.example.com/docs/", params={"max-keys": "1"},
            text=empty_listing())
    r = env.client.post("/api/integrations/s3/setup", headers=env.headers(), json={
        "bucket": "docs", "region": "us-east-1", "endpoint": "https://files.example.com/",
        "access_key_id": "MINIOACCESSKEY01", "secret_access_key": "k"})
    assert r.status_code == 200, r.text
    assert card(env)["s3"]["endpoint"] == "https://files.example.com"


# ---- permissions and the registry ------------------------------------------------------
def test_there_is_no_source_policy_for_s3(env, monkeypatch):
    connect_keys(env)
    c = card(env)
    assert c["acl"] is False and c["policies"] == ["workspace", "restricted"]
    assert "no per-person sharing" in c["source_policy_note"]
    r = env.client.put("/api/integrations/s3/policy", headers=env.headers(),
                       json={"policy": "source"})
    assert r.status_code == 400
    r = env.client.put("/api/integrations/s3/policy", headers=env.headers(),
                       json={"policy": "workspace"})
    assert r.status_code == 200


def test_disconnecting_deletes_the_credentials_and_the_copies(env, monkeypatch):
    import integrations_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_keys(env)
    full(rec)
    sync.sync(env.ws, "s3")
    calls = len(rec.calls)
    r = env.client.delete("/api/integrations/s3", headers=env.headers())
    assert r.status_code == 200 and len(rec.calls) == calls     # nothing to revoke at AWS
    assert integrations_store.get(env.ws, "s3") is None and env.files("s3") == []


def test_s3_is_live_in_the_registry(env):
    by_id = {c["id"]: c["availability"]
             for c in env.client.get("/api/integrations/catalog").json()["connectors"]}
    assert by_id["s3"] == "live"


def test_the_cursor_is_json(env, monkeypatch):
    import sync_store
    from kb.services import sync
    rec = Recorded(monkeypatch)
    connect_keys(env)
    rec.add("GET", B, text=empty_listing())
    sync.sync(env.ws, "s3")
    assert set(json.loads(sync_store.state(env.ws, "s3")["cursor"])) == {"roots", "full_at"}


def test_the_workspace_purge_revokes_linear_dropbox_and_box_and_forgets_s3(env, monkeypatch):
    """kb/services/lifecycle.py calls every connection's disconnect when a deleted
    workspace is purged: the three OAuth connectors revoke their tokens; S3 has
    nothing to revoke (its keys go with the row)."""
    import integrations_store
    from kb.services import lifecycle
    for k, v in {"LINEAR_CLIENT_ID": "l", "LINEAR_CLIENT_SECRET": "ls", "DROPBOX_APP_KEY": "d",
                 "DROPBOX_APP_SECRET": "ds", "BOX_CLIENT_ID": "b",
                 "BOX_CLIENT_SECRET": "bs"}.items():
        monkeypatch.setenv(k, v)
    rec = Recorded(monkeypatch)
    far = time.time() + 3600
    for provider in ("linear", "dropbox", "box"):
        integrations_store.connect(env.ws, provider, "acct", {
            "access_token": f"{provider}-at", "refresh_token": f"{provider}-rt",
            "expires_at": far, "renew_at": far}, "admin")
    connect_keys(env)
    rec.add("POST", "https://api.linear.app/oauth/revoke")
    rec.add("POST", "https://api.dropboxapi.com/2/auth/token/revoke", text="null")
    rec.add("POST", "https://api.box.com/oauth2/revoke")
    done = lifecycle._revoke_outside(env.ws)
    assert {"linear", "dropbox", "box", "s3"} <= set(done)
    assert rec.unused() == []
    by_url = {c["url"]: c for c in rec.calls}
    assert by_url["https://api.linear.app/oauth/revoke"]["data"]["token"] == "linear-rt"
    assert by_url["https://api.dropboxapi.com/2/auth/token/revoke"]["headers"][
        "Authorization"] == "Bearer dropbox-at"
    assert by_url["https://api.box.com/oauth2/revoke"]["data"]["token"] == "box-rt"
    assert len(rec.calls) == 3                               # nothing called for S3
