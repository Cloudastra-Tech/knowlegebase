"""Sign-in settings (admin): a workspace's domains, its own providers (OIDC and
SAML), the roles their groups give, "Test sign-in", and the SSO policy."""

import re
import time
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import auth
import members_store
import saml_sp
import sso
import sso_errors
import sso_setup
import url_guard
from kb import state
from kb.audit import audited
from kb.deps import public_url, require_admin, ws_of
from kb.rate_limit import rate_limit
from kb.services import billing
from kb.services.saml_refresh import next_refresh, read_idp
from kb.services.sign_in import explained, new_sso_test

router = APIRouter()


class DomainIn(BaseModel):
    domain: str = Field(max_length=260)


CONNECTION_ID = r"^[0-9a-f]{12}$"


class ConnectionIn(BaseModel):
    id: str | None = Field(default=None, pattern=CONNECTION_ID)   # from /new-provider
    label: str = Field(min_length=1, max_length=60)
    issuer: str = Field(max_length=500)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str = Field(min_length=1, max_length=2000)
    enabled: bool = True           # the admin screen adds providers off, to test first


class ConnectionPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=60)
    issuer: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, min_length=1, max_length=300)
    client_secret: str | None = Field(default=None, min_length=1, max_length=2000)
    enabled: bool | None = None
    logout_at_idp: bool | None = None       # also sign out at the provider


def _domain_out(row):
    name, value = members_store.txt_record(row) if row["verify_token"] else (None, None)
    return {"domain": row["domain"], "verified": row["verified_at"] is not None,
            "verified_at": row["verified_at"], "txt_name": name, "txt_value": value}


class SamlConnectionIn(BaseModel):
    """The IdP's metadata from ONE of: a URL, the XML, or its three values by hand."""
    id: str | None = Field(default=None, pattern=CONNECTION_ID)   # from /new-provider
    label: str | None = Field(default=None, min_length=1, max_length=60)
    metadata_url: str | None = Field(default=None, max_length=1000)
    metadata_xml: str | None = Field(default=None, max_length=500_000)
    entity_id: str | None = Field(default=None, max_length=500)
    sso_url: str | None = Field(default=None, max_length=1000)
    certificate: str | None = Field(default=None, max_length=20_000)
    email_attribute: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


def _connection_out(request, c):
    """Everything the admin screen shows; never a secret."""
    out = {"id": c["id"], "protocol": c["protocol"], "label": c["label"],
           "issuer": c["issuer"], "enabled": c["enabled"],
           "created_at": c["created_at"], "last_login_at": c["last_login_at"],
           "group_claim": c.get("group_claim"),
           "group_rules": [{"group": r["group_name"], "role": r["role"]}
                           for r in members_store.group_rules(c["id"])],
           # failing: its latest sign-ins all failed (sso_errors.health)
           "health": sso_errors.health(members_store.connection_events(c["id"]))}
    if c["protocol"] == "saml":
        entity_id, acs, metadata_url = saml_sp.sp_urls(public_url(request, ""), c["id"])
        settings = c["metadata"]
        try:
            cert_expires_at = saml_sp.read_idp_metadata(settings["idp_metadata"])[
                "cert_expires_at"]
        except (KeyError, saml_sp.SamlError):
            cert_expires_at = None
        return {**out, "sp_entity_id": entity_id, "acs_url": acs,
                "sp_metadata_url": metadata_url, "sso_url": settings.get("sso_url"),
                "metadata_url": settings.get("metadata_url"),
                "email_attribute": settings.get("email_attribute"),
                "signs_requests": bool(settings.get("want_authn_requests_signed")),
                "metadata_refreshed_at": c.get("metadata_refreshed_at"),
                "cert_expires_at": cert_expires_at,
                "needs_review": bool(c.get("needs_review")),
                "review_reason": c.get("needs_review")}
    return {**out, "client_id": c["client_id"],
            "redirect_uri": public_url(request, f"/api/auth/sso/c/{c['id']}/callback"),
            "logout_at_idp": c["logout_at_idp"],
            "supports_logout": bool(c["metadata"].get("end_session_endpoint")),
            "post_logout_redirect_uri": public_url(request, "/")}


def _saml_settings(body, old=None):
    """(IdP entity ID, settings, metadata status) from the admin's input, checked.
    `old` settings are kept for anything not given. The status (see
    members_store.METADATA_STATUS) is empty if the IdP's metadata wasn't given
    again. Raises HTTPException(400)."""
    if not saml_sp.xmlsec_binary():
        raise HTTPException(400, "SAML isn't available on this server: xmlsec1 isn't "
                                 "installed. Ask whoever runs it.")
    settings = dict(old or {})
    sources = [bool(body.metadata_url), bool(body.metadata_xml),
               bool(body.entity_id or body.sso_url or body.certificate)]
    if sum(sources) > 1:
        raise HTTPException(400, "Give the identity provider's metadata URL, its metadata "
                                 "XML, or its three values by hand: only one of them.")
    try:
        if body.metadata_url:
            url = body.metadata_url.strip()
            xml = sso_setup.fetch_saml_metadata(url).decode("utf-8", "replace")
            settings.update(metadata_url=url)
        elif body.metadata_xml:
            xml = body.metadata_xml.strip()
            settings.pop("metadata_url", None)
        elif sources[2]:
            if not (body.entity_id and body.sso_url and body.certificate):
                raise HTTPException(400, "By hand, give all three: the identity provider's "
                                         "entity ID, sign-in URL and certificate.")
            xml = saml_sp.manual_idp_metadata(body.entity_id.strip(), body.sso_url.strip(),
                                              body.certificate)
            settings.pop("metadata_url", None)
        elif old is None:
            raise HTTPException(400, "Give the identity provider's metadata: a URL, the XML, "
                                     "or its entity ID, sign-in URL and certificate.")
        else:
            xml = settings["idp_metadata"]
        idp = read_idp(xml)
    except sso_setup.SetupError as e:
        raise _setup_error(e)
    except (saml_sp.SamlError, url_guard.UnsafeURL) as e:
        raise HTTPException(400, f"SAML metadata: {e}")
    settings.update(idp_metadata=xml, sso_url=idp["sso_url"],
                    want_authn_requests_signed=idp["want_authn_requests_signed"])
    if body.email_attribute is not None:
        settings["email_attribute"] = body.email_attribute.strip() or None
    status = {}
    if any(sources):           # new metadata, checked by the admin: nothing to review
        fetched = bool(body.metadata_url)
        status = {"metadata_refreshed_at": time.time() if fetched else None,
                  "metadata_due_at": next_refresh(idp) if fetched else None,
                  "needs_review": None, "metadata_error": None}
    return idp["entity_id"], settings, status


def _setup_error(e):
    return HTTPException(400, str(e))


def _connection_id(wanted):
    """The ID a new provider gets: the one /new-provider showed the admin, if free."""
    if not wanted:
        return members_store.new_connection_id()
    if members_store.get_connection(wanted):
        raise HTTPException(409, "That provider was already added. Reload the page.")
    return wanted


@router.get("/api/admin/sign-in/new-provider")
def new_provider(request: Request, user=Depends(require_admin)):
    """An ID for a provider about to be added, with the addresses its IdP needs:
    an IdP admin usually has to enter these BEFORE they get the client ID or
    metadata to give us. Nothing is saved until the provider is added."""
    cid = members_store.new_connection_id()
    entity_id, acs, metadata_url = saml_sp.sp_urls(public_url(request, ""), cid)
    return {"id": cid,
            "redirect_uri": public_url(request, f"/api/auth/sso/c/{cid}/callback"),
            "sp_entity_id": entity_id, "acs_url": acs, "sp_metadata_url": metadata_url}


@router.get("/api/admin/sign-in")
def sign_in_settings(request: Request, user=Depends(require_admin)):
    ws = ws_of(user)
    connections = members_store.list_connections(ws)
    labels = {c["id"]: c["label"] for c in connections}
    return {"domains": [_domain_out(d) for d in members_store.list_domains(ws)],
            "connections": [_connection_out(request, c) for c in connections],
            "require_sso": members_store.requires_sso(ws),
            "role_source": members_store.role_source(ws),
            "group_admin_days": members_store.group_admin_days(ws),
            "password_login": auth.password_login_on(),
            "events": [{**e, "connection": _event_method(labels, e["connection_id"]),
                        **explained(e["reason"])}
                       for e in members_store.recent_events(ws)]}


def _event_method(labels, method):
    """What someone signed in with: a workspace provider's name, "password", or a
    built-in provider (Google, ...). None for a provider deleted since."""
    if method in labels:
        return labels[method]
    if method == "password":
        return "password"
    if method and not re.fullmatch(CONNECTION_ID, method):
        return sso.label(method)
    return None


class PolicyIn(BaseModel):
    require_sso: bool


@router.put("/api/admin/sign-in/policy")
def set_policy(body: PolicyIn, user=Depends(require_admin), audit=audited("sign_in.policy")):
    """Require single sign-on: nobody but the workspace's admins may use a password."""
    ws = ws_of(user)
    if body.require_sso:
        if not any(d["verified_at"] for d in members_store.list_domains(ws)):
            raise HTTPException(400, "Verify a domain first, or nobody could sign in.")
        if not (members_store.list_connections(ws, enabled_only=True) or sso.configured()):
            raise HTTPException(400, "Turn on a sign-in provider first, or nobody could sign in.")
    members_store.set_requires_sso(ws, body.require_sso)
    audit.record("workspace", ws, {"require_sso": body.require_sso}, user=user)
    return {"require_sso": body.require_sso}


@router.post("/api/admin/sign-in/domains")
def add_domain(body: DomainIn, user=Depends(require_admin), audit=audited("domain.add")):
    try:
        domain = sso_setup.normalize_domain(body.domain)
    except sso_setup.SetupError as e:
        raise _setup_error(e)
    row = members_store.claim_domain(ws_of(user), domain)
    audit.record("domain", domain, user=user)
    return _domain_out(row)


@router.post("/api/admin/sign-in/domains/{domain}/verify")
def verify_domain(domain: str, request: Request, user=Depends(require_admin),
                  audit=audited("domain.verify")):
    rate_limit(request, "dns_check")
    ws = ws_of(user)
    row = members_store.get_domain(ws, domain)
    if not row:
        raise HTTPException(404, "Add the domain first.")
    if row["verified_at"] is None:
        name, value = members_store.txt_record(row)
        if value not in sso_setup.txt_values(name):
            raise HTTPException(400, f"No TXT record {name} with the value {value} yet. "
                                     "DNS changes can take a few minutes to appear.")
        try:
            members_store.mark_verified(ws, row["domain"])
        except ValueError as e:
            raise HTTPException(409, str(e))
        audit.record("domain", row["domain"], user=user)
    return _domain_out(members_store.get_domain(ws, domain))


@router.delete("/api/admin/sign-in/domains/{domain}")
def delete_domain(domain: str, user=Depends(require_admin), audit=audited("domain.remove")):
    domain = domain.strip().lower()
    row = members_store.get_domain(ws_of(user), domain)
    if row and row["verified_at"]:
        _keep_a_way_in(user, ws_of(user), domain=domain)
    if not members_store.remove_domain(domain, workspace=ws_of(user)):
        raise HTTPException(404, "This workspace doesn't have that domain.")
    audit.record("domain", domain, {"was_verified": bool(row and row["verified_at"])}, user=user)
    return {"ok": True}


def _changed(fields):
    """What an admin changed on a provider, for the audit log: never a secret's value."""
    return {k: ("(changed)" if k in ("keys", "secret") else v) for k, v in fields.items()
            if k not in ("metadata", *members_store.METADATA_STATUS)}


@router.post("/api/admin/sign-in/connections")
def add_connection(body: ConnectionIn, request: Request, user=Depends(require_admin),
                   audit=audited("connection.create")):
    billing.require(ws_of(user), "sso_oidc_saml")
    try:
        issuer = sso_setup.normalize_issuer(body.issuer)
        metadata = sso_setup.oidc_metadata(issuer)
    except sso_setup.SetupError as e:
        raise _setup_error(e)
    conn = members_store.create_connection(ws_of(user), "oidc", body.label.strip(), issuer,
                                           body.client_id.strip(),
                                           {"client_secret": body.client_secret},
                                           metadata, user["username"],
                                           cid=_connection_id(body.id))
    if not body.enabled:
        conn = members_store.update_connection(conn["workspace"], conn["id"], enabled=False)
    audit.record("connection", conn["id"], {"protocol": "oidc", "label": conn["label"],
                                            "issuer": issuer, "enabled": body.enabled},
                 user=user)
    return _connection_out(request, conn)


@router.patch("/api/admin/sign-in/connections/{cid}")
def edit_connection(cid: str, body: ConnectionPatch, request: Request,
                    user=Depends(require_admin), audit=audited("connection.update")):
    ws = ws_of(user)
    conn = members_store.get_connection(cid)
    if not conn or conn["workspace"] != ws:
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    if conn["protocol"] != "oidc":
        raise HTTPException(400, "That's a SAML provider: change it under saml-connections.")
    fields = body.model_dump(exclude_none=True)
    if "client_secret" in fields:
        fields["keys"] = {"client_secret": fields.pop("client_secret")}
    for key in ("label", "client_id"):
        if key in fields:
            fields[key] = fields[key].strip()
    if "issuer" in fields:
        if "keys" not in fields:
            # Else the saved secret would go to whatever the new issuer's token
            # endpoint is.
            raise HTTPException(400, "Enter the client secret again when you change the issuer.")
        try:
            fields["issuer"] = sso_setup.normalize_issuer(fields["issuer"])
            fields["metadata"] = sso_setup.oidc_metadata(fields["issuer"])
        except sso_setup.SetupError as e:
            raise _setup_error(e)
    if fields.get("enabled") is False:
        _keep_a_way_in(user, ws, without=cid)
    if fields:
        conn = members_store.update_connection(ws, cid, **fields)
        audit.record("connection", cid, _changed(fields), user=user)
    return _connection_out(request, conn)


@router.post("/api/admin/sign-in/saml-connections")
def add_saml_connection(body: SamlConnectionIn, request: Request, user=Depends(require_admin),
                        audit=audited("connection.create")):
    billing.require(ws_of(user), "sso_oidc_saml")
    if not (body.label or "").strip():
        raise HTTPException(400, "Give the sign-in button a name.")
    entity_id, settings, status = _saml_settings(body)
    cid = _connection_id(body.id)
    sp_key, sp_cert = saml_sp.new_keypair(f"Knowledge assistant {cid}")
    conn = members_store.create_connection(ws_of(user), "saml", body.label.strip(), entity_id,
                                           None, {"sp_key": sp_key, "sp_cert": sp_cert},
                                           settings, user["username"], cid=cid)
    conn = members_store.update_connection(conn["workspace"], cid, **status,
                                           **({"enabled": False} if body.enabled is False
                                              else {}))
    audit.record("connection", cid, {"protocol": "saml", "label": conn["label"],
                                     "issuer": entity_id, "enabled": conn["enabled"]},
                 user=user)
    return _connection_out(request, conn)


@router.patch("/api/admin/sign-in/saml-connections/{cid}")
def edit_saml_connection(cid: str, body: SamlConnectionIn, request: Request,
                         user=Depends(require_admin), audit=audited("connection.update")):
    ws = ws_of(user)
    conn = members_store.get_connection(cid)
    if not conn or conn["workspace"] != ws or conn["protocol"] != "saml":
        raise HTTPException(404, "This workspace has no such SAML sign-in provider.")
    fields = {}
    if body.label is not None:
        fields["label"] = body.label.strip()
    if body.enabled is not None:
        fields["enabled"] = body.enabled
        if body.enabled is False:
            _keep_a_way_in(user, ws, without=cid)
    if any(v is not None for v in (body.metadata_url, body.metadata_xml, body.entity_id,
                                   body.sso_url, body.certificate, body.email_attribute)):
        fields["issuer"], fields["metadata"], status = _saml_settings(body, old=conn["metadata"])
        fields.update(status)
    if fields:
        conn = members_store.update_connection(ws, cid, **fields)
        audit.record("connection", cid, _changed(fields), user=user)
    return _connection_out(request, conn)


@router.post("/api/admin/sign-in/connections/{cid}/test")
def test_connection(cid: str, user=Depends(require_admin), audit=audited("connection.test")):
    """Start a "Test sign-in": open `url` in a popup, then poll /tests/{ticket}."""
    ws = ws_of(user)
    conn = members_store.get_connection(cid)
    if not conn or conn["workspace"] != ws:
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    if conn["protocol"] == "saml" and not saml_sp.xmlsec_binary():
        raise HTTPException(400, "SAML isn't available on this server: xmlsec1 isn't installed.")
    ticket = new_sso_test(ws, cid)
    audit.record("connection", cid, user=user)
    path =(f"/api/auth/saml/{cid}/start" if conn["protocol"] == "saml"
            else f"/api/auth/sso/c/{cid}/start")
    return {"ticket": ticket, "url": f"{path}?" + urllib.parse.urlencode({"test": ticket})}


@router.get("/api/admin/sign-in/tests/{ticket}")
def test_result(ticket: str, user=Depends(require_admin)):
    with state.sso_tests_lock:
        test = state.sso_tests.get(ticket)
    if not test or test["workspace"] != ws_of(user) or test["expires"] < time.time():
        raise HTTPException(404, "That test has expired. Start it again.")
    return {"done": test["result"] is not None, "result": test["result"]}


class GroupRule(BaseModel):
    group: str = Field(min_length=1, max_length=256)
    role: str = Field(pattern=r"^(public|hr|admin)$")


class GroupRulesIn(BaseModel):
    group_claim: str | None = Field(default=None, max_length=300)
    rules: list[GroupRule] = Field(default_factory=list, max_length=200)
    make_role_source: bool = False   # take over setting roles from another provider


def _no_admin_left(e):
    who = ", ".join(e.admins)
    return HTTPException(400, f"This would leave the workspace with no admin ({who} "
                              f"{'is' if len(e.admins) == 1 else 'are'} admin through "
                              "groups). Make someone else admin on the Members tab first.")


@router.put("/api/admin/sign-in/connections/{cid}/roles")
def set_group_roles(cid: str, body: GroupRulesIn, request: Request,
                    user=Depends(require_admin), audit=audited("connection.roles")):
    """Roles from the groups a provider sends: they apply at each person's next
    sign-in. Rules make it the workspace's one role source (members_store.py)."""
    ws = ws_of(user)
    claim = (body.group_claim or "").strip() or None
    try:
        found = members_store.set_group_rules(ws, cid, claim,
                                              [(r.group, r.role) for r in body.rules],
                                              make_role_source=body.make_role_source)
    except members_store.RoleSourceTaken as e:
        source = members_store.get_connection(e.source)
        name = (source["label"] if source else
                "SCIM provisioning" if e.source == members_store.SCIM_SOURCE else
                "another provider")
        raise HTTPException(409, f"Roles come from {name}. A workspace takes roles from one "
                                 "provider: switch it to this one to save these rules.")
    except members_store.NoAdminLeft as e:
        raise _no_admin_left(e)
    if not found:
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    audit.record("connection", cid, {"group_claim": claim,
                                     "rules": [r.model_dump() for r in body.rules],
                                     "role_source": members_store.role_source(ws)}, user=user)
    return _connection_out(request, members_store.get_connection(cid))


class GroupAdminDaysIn(BaseModel):
    days: int = Field(ge=0, le=3650)


@router.put("/api/admin/sign-in/group-admin-days")
def set_group_admin_days(body: GroupAdminDaysIn, user=Depends(require_admin),
                         audit=audited("sign_in.group_admin_days")):
    """How long admin from groups lasts without a sign-in through the role source (0: forever)."""
    members_store.set_group_admin_days(ws_of(user), body.days)
    audit.record("workspace", ws_of(user), {"days": body.days}, user=user)
    return {"group_admin_days": body.days}


def _keep_a_way_in(user, ws, without=None, domain=None):
    """Refuse a change that would lock out the admin making it (they signed in
    through this provider, or with an email on this domain and have no password),
    or leave "Require single sign-on" on with no way to sign in."""
    if without and user.get("connection") == without:
        raise HTTPException(400, "You're signed in through this provider, so this would sign "
                                 "you out for good. Sign in another way first (for example, "
                                 "an admin password).")
    if domain and user.get("provider") and user["username"].endswith("@" + domain):
        raise HTTPException(400, f"You signed in with an @{domain} address, so this would sign "
                                 "you out for good. Sign in another way first.")
    if not members_store.requires_sso(ws):
        return
    providers = [c for c in members_store.list_connections(ws, enabled_only=True)
                 if c["id"] != without]
    domains = [d for d in members_store.list_domains(ws)
               if d["verified_at"] and d["domain"] != domain]
    if not domains or not (providers or sso.configured()):
        raise HTTPException(400, "Single sign-on is required, and this would leave no way to "
                                 "sign in with it. Turn that off first (Sign-in rules).")


@router.delete("/api/admin/sign-in/connections/{cid}")
def delete_connection(cid: str, user=Depends(require_admin),
                      audit=audited("connection.delete")):
    _keep_a_way_in(user, ws_of(user), without=cid)
    conn = members_store.get_connection(cid)
    try:
        found = members_store.delete_connection(ws_of(user), cid)
    except members_store.NoAdminLeft as e:
        raise _no_admin_left(e)
    if not found:
        raise HTTPException(404, "This workspace has no such sign-in provider.")
    audit.record("connection", cid, {"label": conn["label"], "protocol": conn["protocol"]},
                 user=user)
    return {"ok": True}
