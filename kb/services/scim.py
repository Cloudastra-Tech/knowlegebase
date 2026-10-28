"""
SCIM 2.0 (RFC 7643 / 7644) as Okta and Entra ID speak it: their JSON in and
out, filters, and PATCH in both dialects; plus the job that anonymises deleted
users. The data and its rules are in scim_store.py; the endpoints in
kb/routers/scim.py.

PATCH. The two providers send the same changes differently, and both are taken:
  * Okta:  {"op": "replace", "value": {"active": false}}   (no path: a value object)
           {"op": "remove", "path": "members[value eq \"<id>\"]"}
  * Entra: {"op": "Replace", "path": "active", "value": "False"}   (capitalised op,
           boolean as a string), "emails[type eq \"work\"].value", path-less
           values with dotted keys ("name.givenName"), and
           {"op": "Remove", "path": "members", "value": [{"value": "<id>"}]}
Attributes we don't keep (title, phoneNumbers, the enterprise extension, ...)
are accepted and ignored, as RFC 7644 lets a service provider do, so a
provider's sync never fails over them.
"""

import datetime
import logging
import re

import scim_store
from kb import jobs

log = logging.getLogger("uvicorn.error")

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
MAX_COUNT = 200                    # most results per page (ServiceProviderConfig says so)
DEFAULT_COUNT = 100


class ScimError(Exception):
    """Answered as a SCIM error (RFC 7644 §3.12)."""

    def __init__(self, status, detail, scim_type=None):
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type

    def body(self):
        out = {"schemas": [ERROR_SCHEMA], "status": str(self.status), "detail": self.detail}
        if self.scim_type:
            out["scimType"] = self.scim_type
        return out


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- Out: our users and groups as SCIM resources --------------------------------------
def user_resource(u, base):
    name = {k: v for k, v in (("givenName", u.get("given_name")),
                              ("familyName", u.get("family_name")),
                              ("middleName", u.get("middle_name")),
                              ("formatted", u.get("formatted"))) if v}
    out = {"schemas": [USER_SCHEMA], "id": u["id"], "userName": u["user_name"],
           "displayName": u["display_name"], "active": u["active"],
           "emails": [{"value": u["email"], "type": "work", "primary": True}],
           "groups": [{"value": g["id"], "display": g["display_name"],
                       "$ref": f"{base}/Groups/{g['id']}"} for g in u["groups"]],
           "meta": {"resourceType": "User", "created": _iso(u["created_at"]),
                    "lastModified": _iso(u["updated_at"]),
                    "location": f"{base}/Users/{u['id']}"}}
    if name:
        out["name"] = name
    if u.get("external_id"):
        out["externalId"] = u["external_id"]
    return out


def group_resource(g, base):
    out = {"schemas": [GROUP_SCHEMA], "id": g["id"], "displayName": g["display_name"],
           "meta": {"resourceType": "Group", "created": _iso(g["created_at"]),
                    "lastModified": _iso(g["updated_at"]),
                    "location": f"{base}/Groups/{g['id']}"}}
    if "members" in g:
        out["members"] = [{"value": m["id"], "display": m["display"],
                           "$ref": f"{base}/Users/{m['id']}"} for m in g["members"]]
    if g.get("external_id"):
        out["externalId"] = g["external_id"]
    return out


def list_response(resources, total, start):
    return {"schemas": [LIST_SCHEMA], "totalResults": total, "startIndex": start,
            "itemsPerPage": len(resources), "Resources": resources}


def pick_attributes(resource, attributes=None, excluded=None):
    """?attributes= / ?excludedAttributes= (top-level names; id and schemas are
    always returned)."""
    if attributes:
        keep = {a.strip().split(".")[0].lower() for a in attributes.split(",")} | {
            "id", "schemas", "meta"}
        resource = {k: v for k, v in resource.items() if k.lower() in keep}
    if excluded:
        drop = {a.strip().split(".")[0].lower() for a in excluded.split(",")} - {"id", "schemas"}
        resource = {k: v for k, v in resource.items() if k.lower() not in drop}
    return resource


# ---- In: query parameters ---------------------------------------------------------------
_FILTER = re.compile(r'^\s*([A-Za-z][\w.:$\[\]" ]*?)\s+eq\s+(?:"((?:[^"\\]|\\.)*)"|(\S+))\s*$',
                     re.I)


def parse_filter(text, allowed):
    """(attribute, value) from `attr eq "value"`; attr lower-cased and checked
    against `allowed`. Only eq, which is all Okta and Entra send."""
    if not text:
        return None, None
    m = _FILTER.match(text)
    attr = m.group(1).strip().lower() if m else None
    attr = attr and re.sub(r'^emails\[type eq "?work"?\]\.value$', "emails.value", attr)
    if attr and attr.startswith("urn:ietf:params:scim:schemas:core:2.0:"):
        attr = attr.split(":")[-1]
    if not m or attr not in allowed:
        raise ScimError(400, f"Unsupported filter: {text[:200]}. Filter with "
                             f"{' or '.join(sorted(allowed))} eq \"value\".", "invalidFilter")
    value = m.group(2) if m.group(2) is not None else m.group(3)
    return attr, value.replace('\\"', '"').replace("\\\\", "\\")


def page(start_index, count):
    """(start, count) from startIndex (1-based) and count, clamped as RFC 7644
    §3.4.2.4 says: below 1 is 1, a negative count is 0."""
    try:
        start = max(1, int(start_index)) if start_index not in (None, "") else 1
        count = DEFAULT_COUNT if count in (None, "") else max(0, min(int(count), MAX_COUNT))
    except ValueError:
        raise ScimError(400, "startIndex and count must be whole numbers.", "invalidValue")
    return start, count


# ---- In: a user ---------------------------------------------------------------------------
def as_bool(value):
    """SCIM booleans, and Entra's "True" / "False" strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ScimError(400, f"Expected true or false, not {str(value)[:50]!r}.", "invalidValue")


def _text(value, what, limit=300):
    if value is None:
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ScimError(400, f"{what} must be a string.", "invalidValue")
    return str(value).strip()[:limit] or None


def _email_from(emails):
    """The primary email, else the work one, else the first."""
    if emails is None:
        return None
    if not isinstance(emails, list):
        raise ScimError(400, "emails must be a list.", "invalidValue")
    emails = [e for e in emails if isinstance(e, dict) and e.get("value")]
    for test in (lambda e: e.get("primary") in (True, "true", "True"),
                 lambda e: str(e.get("type", "")).lower() == "work", lambda e: True):
        for e in emails:
            if test(e):
                return _text(e["value"], "emails.value")
    return None


def _lower_keys(d):
    return {str(k).lower(): v for k, v in d.items()} if isinstance(d, dict) else {}


def user_from_resource(body, current=None):
    """A POST or PUT body as scim_store's user dict. PUT (`current` given) keeps
    what the body leaves out of name, externalId and active."""
    if not isinstance(body, dict):
        raise ScimError(400, "Send a User object.", "invalidSyntax")
    b = _lower_keys(body)
    user = dict(current or {"external_id": None, "active": True})
    user_name = _text(b.get("username"), "userName")
    if not user_name:
        raise ScimError(400, "userName is required.", "invalidValue")
    user["user_name"] = user_name
    email = _email_from(b.get("emails"))
    if "externalid" in b:
        user["external_id"] = _text(b["externalid"], "externalId")
    if "active" in b and b["active"] is not None:
        user["active"] = as_bool(b["active"])
    if "name" in b and isinstance(b["name"], dict):
        n = _lower_keys(b["name"])
        for key, attr in (("givenname", "given_name"), ("familyname", "family_name"),
                          ("middlename", "middle_name"), ("formatted", "formatted")):
            user[attr] = _text(n.get(key), f"name.{key}")
    user["display_name"] = _text(b.get("displayname"), "displayName")
    return _finish(user, email)


def _finish(user, email):
    """Fill in the email (from userName if it's one) and a display name."""
    email = (email or user.get("email") or (user["user_name"] if "@" in user["user_name"]
                                            else "")).strip().lower()
    user["email"] = email
    if not user.get("display_name"):
        parts = [user.get("given_name"), user.get("family_name")]
        user["display_name"] = (user.get("formatted") or " ".join(p for p in parts if p)
                                or email.split("@")[0] or user["user_name"])
    return user


# ---- PATCH ------------------------------------------------------------------------------------
_ATTR_PREFIX = re.compile(r"^urn:ietf:params:scim:schemas:core:2\.0:(user|group):", re.I)
_EMAIL_PATH = re.compile(r'^emails\[\s*(type\s+eq\s+"?work"?|primary\s+eq\s+"?true"?)\s*\]'
                         r'(\.value)?$', re.I)
_MEMBER_PATH = re.compile(r'^members\[\s*value\s+eq\s+"([^"]+)"\s*\]$', re.I)
_NAME_PARTS = {"givenname": "given_name", "familyname": "family_name",
               "middlename": "middle_name", "formatted": "formatted"}


def operations(body):
    """The PatchOp's operations as (op, path or None, value), op lower-cased."""
    if not isinstance(body, dict):
        raise ScimError(400, "Send a PatchOp object.", "invalidSyntax")
    ops = _lower_keys(body).get("operations")
    if not isinstance(ops, list) or not ops:
        raise ScimError(400, "A PatchOp needs Operations.", "invalidSyntax")
    out = []
    for o in ops:
        o = _lower_keys(o)
        op = str(o.get("op", "")).lower()
        if op not in ("add", "replace", "remove"):
            raise ScimError(400, f"Unknown PATCH op {str(o.get('op'))[:20]!r}.", "invalidSyntax")
        path = o.get("path")
        path = _ATTR_PREFIX.sub("", path.strip()) if isinstance(path, str) and path.strip() else None
        out.append((op, path, o.get("value")))
    return out


def patch_user(user, body):
    """Apply a PatchOp to scim_store's user dict (a copy). Returns the new one."""
    user = dict(user)
    email = None
    for op, path, value in operations(body):
        if path is None:
            if op == "remove" or not isinstance(value, dict):
                raise ScimError(400, "A PATCH op without a path needs an object value.",
                                "noTarget" if op == "remove" else "invalidValue")
            for key, v in value.items():
                email = _set_user_attr(user, str(key), v, op) or email
        else:
            email = _set_user_attr(user, path, None if op == "remove" else value, op) or email
    return _finish(user, email or user["email"])


def _set_user_attr(user, path, value, op):
    """Set one attribute. Returns a new email, if this set one."""
    path = _ATTR_PREFIX.sub("", path.strip())
    key = path.lower()
    if key == "active":
        if value is not None:
            user["active"] = as_bool(value)
    elif key == "username":
        if not _text(value, "userName"):
            raise ScimError(400, "userName can't be removed.", "mutability")
        user["user_name"] = _text(value, "userName")
    elif key == "displayname":
        user["display_name"] = _text(value, "displayName")
    elif key == "externalid":
        user["external_id"] = _text(value, "externalId")
    elif key == "name":
        n = _lower_keys(value) if isinstance(value, dict) else {}
        for k, attr in _NAME_PARTS.items():
            if op == "replace" or op == "remove" or k in n:
                user[attr] = _text(n.get(k), f"name.{k}")
    elif key.startswith("name.") and key[5:] in _NAME_PARTS:
        user[_NAME_PARTS[key[5:]]] = _text(value, path)
    elif key == "emails":
        if value is not None:
            return _email_from(value if isinstance(value, list) else [value])
    elif _EMAIL_PATH.match(path):
        if isinstance(value, dict):
            value = value.get("value")
        if value is not None:
            return _text(value, "email")
    elif key == "groups" or key.startswith("groups"):
        raise ScimError(400, "A user's groups are read-only: change the group's members.",
                        "mutability")
    elif key in ("id", "meta", "schemas"):
        pass                                    # read-only; Okta repeats the id
    # Anything else (title, phoneNumbers, the enterprise extension, ...) we don't keep.
    return None


def patch_group(group, body):
    """Apply a PatchOp to a group: returns (display name, external id, member ids)."""
    name, external_id = group["display_name"], group.get("external_id")
    members = [m["id"] for m in group.get("members", [])]

    def ids(value):
        items = value if isinstance(value, list) else [value] if value is not None else []
        out = []
        for item in items:
            v = item.get("value") if isinstance(item, dict) else item
            if not isinstance(v, str) or not v:
                raise ScimError(400, "Each member needs a value (the user's id).", "invalidValue")
            out.append(v)
        return out

    for op, path, value in operations(body):
        key = (path or "").lower()
        m = _MEMBER_PATH.match(path or "")
        if path is None:
            if op == "remove" or not isinstance(value, dict):
                raise ScimError(400, "A PATCH op without a path needs an object value.",
                                "noTarget" if op == "remove" else "invalidValue")
            v = _lower_keys(value)
            if "displayname" in v:
                name = _text(v["displayname"], "displayName")
            if "externalid" in v:
                external_id = _text(v["externalid"], "externalId")
            if "members" in v:
                given = ids(v["members"])
                members = given if op == "replace" else members + given
        elif key == "displayname":
            if op == "remove":
                raise ScimError(400, "displayName can't be removed.", "mutability")
            name = _text(value, "displayName")
        elif key == "externalid":
            external_id = None if op == "remove" else _text(value, "externalId")
        elif key == "members":
            if op == "add":
                members += ids(value)
            elif op == "replace":
                members = ids(value)
            elif value is None:                  # remove with no filter: all of them
                members = []
            else:                                # Entra: remove these
                gone = set(ids(value))
                members = [x for x in members if x not in gone]
        elif m:
            if op != "remove":
                raise ScimError(400, f"Only remove works on {path}.", "invalidPath")
            members = [x for x in members if x != m.group(1)]
        elif key in ("id", "meta", "schemas"):
            pass
        else:
            raise ScimError(400, f"Groups don't have {path}.", "invalidPath")
    return name, external_id, list(dict.fromkeys(members))


# ---- Discovery (RFC 7643 §5-7) ------------------------------------------------------------
def _attr(name, type_="string", required=False, mutability="readWrite", unique="none",
          multi=False, sub=None, returned="default", case_exact=False):
    a = {"name": name, "type": type_, "multiValued": multi, "required": required,
         "caseExact": case_exact, "mutability": mutability, "returned": returned,
         "uniqueness": unique, "description": name}
    if sub:
        a["subAttributes"] = sub
    return a


SCHEMAS = [
    {"id": USER_SCHEMA, "name": "User", "description": "A member of the workspace",
     "attributes": [
         _attr("userName", required=True, unique="server"),
         _attr("name", "complex", sub=[_attr(n) for n in
                                       ("formatted", "familyName", "givenName", "middleName")]),
         _attr("displayName"),
         _attr("active", "boolean"),
         _attr("emails", "complex", multi=True, sub=[
             _attr("value"), _attr("type"), _attr("primary", "boolean")]),
         _attr("groups", "complex", multi=True, mutability="readOnly", sub=[
             _attr("value", mutability="readOnly"), _attr("display", mutability="readOnly"),
             _attr("$ref", "reference", mutability="readOnly")]),
         _attr("externalId", case_exact=True),
     ]},
    {"id": GROUP_SCHEMA, "name": "Group", "description": "A group from the identity provider",
     "attributes": [
         _attr("displayName", required=True, unique="server"),
         _attr("members", "complex", multi=True, sub=[
             _attr("value", mutability="immutable"), _attr("display", mutability="readOnly"),
             _attr("$ref", "reference", mutability="immutable")]),
         _attr("externalId", case_exact=True),
     ]},
]


def schemas(base):
    return [{**s, "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
             "meta": {"resourceType": "Schema", "location": f"{base}/Schemas/{s['id']}"}}
            for s in SCHEMAS]


def resource_types(base):
    return [{"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
             "id": name, "name": name, "endpoint": f"/{name}s", "schema": schema,
             "description": name,
             "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/{name}"}}
            for name, schema in (("User", USER_SCHEMA), ("Group", GROUP_SCHEMA))]


def service_provider_config(base):
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": MAX_COUNT},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [{
                "type": "oauthbearertoken", "name": "Bearer token", "primary": True,
                "description": "The token made in Access > Sign-in > Provisioning (SCIM)."}],
            "meta": {"resourceType": "ServiceProviderConfig",
                     "location": f"{base}/ServiceProviderConfig"}}


# ---- Anonymising deleted users ----------------------------------------------------------
@jobs.periodic("scim_anonymise", lambda: 60 * 60)
def anonymise(job=None):
    """Anonymise users SCIM deleted longer ago than the retention window."""
    import audit_store
    import members_store
    done = scim_store.anonymise_due()
    for workspace, username, scim_id, revoked in done:
        audit_store.record(workspace, "scim.user.anonymise", actor="system",
                           actor_type="system", target_type="member", target_id=username,
                           details={"scim_id": scim_id, "invites_revoked": revoked,
                                    "retention_days": scim_store.retention_days()})
        members_store.log_event(workspace, members_store.SCIM_SOURCE, username, "scim",
                                "scim_user_anonymised")
    if done:
        log.info("SCIM: anonymised %d deleted users", len(done))
    return {"anonymised": len(done)}
