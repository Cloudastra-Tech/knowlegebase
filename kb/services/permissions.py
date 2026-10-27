"""
Permission sync (platform.md PL-10 step 2, connectors.md §2.5): who may see each
synced file, from its connection's PERMISSION POLICY (integrations_store.policy_of):

    workspace   everyone                     [everyone]
    restricted  HR and admins                [group:hr]
    source      who may read it at the source: the item's source_items.acl, as
                the connector captured it, mapped to this workspace's principals
                (access.py) by Resolver

MAPPING (Resolver.resolve). A grant becomes a principal only when it matches
something here; anything else is DROPPED, never widened (fail closed). A file
whose every grant is dropped is for no one ([nobody]).
    users      an email of a member -> user:<email>. Ids that aren't emails
               (Atlassian account ids, GitHub logins, Entra object ids) can't be
               matched to a member: dropped.
    groups     an identity-provider group (access_groups with source idp or
               scim, groups_store.py) whose email, name or external id is the
               source group's: google:<group email>, entra:<object id>,
               atlassian:group:<name or id>, github:team:<org>/<slug> (or the
               slug). SharePoint site groups, Jira and Confluence roles and
               Jira's issue-relative grants (reporter, assignee) have no
               counterpart: dropped.
    domains    a domain the workspace has VERIFIED -> domain:<domain> (members
               whose email is there); any other domain is dropped. SharePoint's
               organisation-wide links -> every verified domain.
    anyone     public at the source (Drive / SharePoint "anyone", a public
               GitHub repo, Jira "anyone", a Zendesk article for everyone, a
               public web page) -> everyone.
Per source:
    Jira         an issue with a security level is for no one (its members
                 aren't read); otherwise the project's browse grants.
    Confluence   a page's read restrictions, and those of every restricted
                 ancestor, must ALL allow (their principals intersected); with
                 none, the space's read grants. Restrictions not read yet: no one.
    Zendesk      "workspace" segments (none, or signed-in users) -> everyone;
                 anything narrower -> the hr group (staff). Tickets lock their
                 connection to restricted (settings.access_lock).
    Notion       no permission API: the source policy can't be picked.
    Partial ACLs (a Drive file in a shared drive, a repo whose teams couldn't
    be read) keep only the grants that were read: never more people.

WHEN. The ACLs are worked out whenever the index is brought up to date
(kb/services/indexing.py: doc_acls), and after every sync of a source-policy
connection, a RE-TAG compares every chunk's ACL with this and updates the
ones that differ (no embedding). So a change at the source, or in the groups
here, reaches the index within one sync interval (sync.SYNC_EVERY, 30
minutes); "Sync permissions now" does it at once. Between syncs the index has
the permissions of the last one: that's the ACL DRIFT the Integrations screen
states.
"""

import access
import groups_store
import integrations_store
import sync_store
from connectors import registry

GROUP_PREFIXES = ("google:", "entra:", "atlassian:group:", "github:team:")


class Resolver:
    """A workspace's members, identity-provider groups and verified domains, to
    map source grants onto (see the top of this file)."""

    def __init__(self, ws):
        self.ws = ws
        self.members = set(groups_store.workspace_members(ws))
        groups_store.refresh_external(ws)
        self.groups = {}                  # lowercased email / name / external id -> principals
        for g in groups_store.list_groups(ws, refresh=False):
            if g["source"] not in ("idp", "scim"):
                continue
            for key in (g.get("email"), g.get("name"), g.get("external_id")):
                if key:
                    self.groups.setdefault(key.strip().lower(), set()).add(access.group(g["id"]))
        self.domains = groups_store.verified_domains(ws)

    def user(self, who):
        who = str(who or "").strip().lower()
        return access.user(who) if "@" in who and who in self.members else None

    def group(self, who):
        who = str(who or "").strip().lower()
        for prefix in GROUP_PREFIXES:
            if who.startswith(prefix):
                ident = who[len(prefix):]
                keys = [ident] + ([ident.split("/", 1)[1]] if prefix == "github:team:"
                                  and "/" in ident else [])
                return set().union(*(self.groups.get(k, set()) for k in keys))
        return set()

    def domain(self, name):
        name = str(name or "").strip().lower()
        return access.domain(name) if name in self.domains else None

    def grants(self, acl):
        """(principals, how many grants were dropped) of the generic shape:
        users, groups, domains, anyone, organization."""
        if acl.get("anyone") or acl.get("everyone") is True or acl.get("public") is True \
                or acl.get("public_web"):
            return {access.EVERYONE}, 0
        found, dropped = set(), 0
        for u in acl.get("users") or ():
            p = self.user(u)
            found |= {p} if p else set()
            dropped += not p
        for g in list(acl.get("groups") or ()) + list(acl.get("teams") or ()):
            got = self.group(g)
            found |= got
            dropped += not got
        for d in acl.get("domains") or ():
            p = self.domain(d)
            found |= {p} if p else set()
            dropped += not p
        if acl.get("organization"):
            found |= {access.domain(d) for d in self.domains}
            dropped += not self.domains
        dropped += len(acl.get("roles") or ()) + len(acl.get("dynamic") or ())
        return found, dropped

    def resolve(self, acl, items=None):
        """(ACL, dropped): who may see an item, from its source acl. items:
        {source_id: its acl} of the same connection (Confluence ancestors)."""
        if not isinstance(acl, dict) or not acl:
            return [access.NOBODY], 1
        mode = acl.get("mode")
        if mode in ("workspace", "everyone"):
            return [access.EVERYONE], 0
        if mode == "staff":
            return [access.HR], 0
        if "project_read" in acl:                                       # Jira
            if acl.get("restricted") or acl.get("security_level") or not acl["project_read"]:
                return [access.NOBODY], 1
            found, dropped = self.grants(acl["project_read"])
            return access.clean_acl(found), dropped
        if "space_read" in acl:                                         # Confluence
            return self._confluence(acl, items or {})
        if "collection_permission" in acl:                              # Outline
            if acl.get("collection_permission") in ("read", "read_write"):
                return [access.EVERYONE], 0
            return [access.NOBODY], 1
        found, dropped = self.grants(acl)
        return access.clean_acl(found), dropped

    def _confluence(self, acl, items):
        restricted = [acl] if acl.get("restricted") else []
        for ancestor in acl.get("inherits_from") or ():
            parent = items.get(ancestor)
            if not isinstance(parent, dict):
                return [access.NOBODY], 1              # an ancestor we know nothing of
            if parent.get("partial"):
                return [access.NOBODY], 1
            if parent.get("restricted"):
                restricted.append(parent)
        if acl.get("partial"):
            return [access.NOBODY], 1
        if not restricted:
            space = acl.get("space_read")
            if not isinstance(space, dict):
                return [access.NOBODY], 1
            found, dropped = self.grants(space)
            return access.clean_acl(found), dropped
        found, dropped = None, 0
        for r in restricted:                 # every restriction on the way down must allow
            got, lost = self.grants({"users": r.get("users"), "groups": r.get("groups")})
            found = got if found is None else found & got
            dropped += lost
        return access.clean_acl(found), dropped


def policies(provider):
    """The policies a connector's connection may have: source only when it
    captures permissions (connectors/base.py ACL)."""
    c = registry.get(provider)
    return integrations_store.POLICIES if c and c.ACL else ("workspace", "restricted")


def connector_acls(ws, provider=None, details=False):
    """{doc key: ACL} of the synced files of every source-policy connection (or
    only `provider`'s). The others' files take their policy's level (ingest.py).
    details=True: {doc key: (ACL, dropped grants)}."""
    out, resolver = {}, None
    for row in integrations_store.list_for(ws):
        if provider and row["provider"] != provider:
            continue
        if integrations_store.policy_of(row) != "source":
            continue
        resolver = resolver or Resolver(ws)
        items = sync_store.items(ws, row["provider"])
        acls = {sid: item.get("acl") for sid, item in items.items()}
        for item in items.values():
            if not item.get("file_name"):
                continue
            acl, dropped = resolver.resolve(item.get("acl"), acls)
            key = f"connectors/{row['provider']}/{item['file_name']}"
            out[key] = (acl, dropped) if details else acl
    return out


def doc_acls(ws):
    """{doc key: ACL} of every document with its own: uploads given one on the
    Documents screen (groups_store.doc_acl) and source-policy synced files."""
    return {**groups_store.all_doc_acls(ws), **connector_acls(ws)}


def upload_acl(ws, key):
    """An uploaded document's ACL: the one picked for it, else its folder's level's."""
    return groups_store.doc_acl(ws, key) or access.level_acl(
        "restricted" if key.startswith("restricted/") else "public")


def check_upload_acl(ws, principals):
    """The ACL picked for an upload, checked: everyone, the built-in hr group, a
    group of this workspace, or a member. Raises groups_store.Invalid."""
    picked = sorted({str(p).strip() for p in principals or () if str(p).strip()})
    if not picked:
        raise groups_store.Invalid("Pick who may see it.")
    if access.EVERYONE in picked:
        return [access.EVERYONE]
    known_groups = {g["id"] for g in groups_store.list_groups(ws, refresh=False)}
    members = groups_store.workspace_members(ws)
    for p in picked:
        kind, _, value = p.partition(":")
        if kind == "group" and value in known_groups:
            continue
        if kind == "user" and value.lower() in members:
            continue
        raise groups_store.Invalid("Pick groups and members of this workspace, or everyone.")
    return access.clean_acl(picked)


def labels(ws, acl, names=None):
    """How the screens show an ACL: [{principal, kind, label}]."""
    names = names if names is not None else _names(ws)
    out = []
    for p in acl or ():
        kind, _, value = p.partition(":")
        if p == access.EVERYONE:
            out.append({"principal": p, "kind": "everyone", "label": "Everyone"})
        elif p == access.NOBODY:
            out.append({"principal": p, "kind": "nobody", "label": "No one (nothing matched)"})
        elif kind == "group":
            out.append({"principal": p, "kind": "group",
                        "label": names["groups"].get(value, "A deleted group")})
        elif kind == "user":
            out.append({"principal": p, "kind": "user",
                        "label": names["members"].get(value) or value})
        elif kind == "domain":
            out.append({"principal": p, "kind": "domain", "label": f"Everyone at {value}"})
    return out


def _names(ws):
    return {"groups": {g["id"]: g["name"] for g in groups_store.list_groups(ws, refresh=False)},
            "members": groups_store.workspace_members(ws)}


def names(ws):
    """What labels() needs, read once for many calls."""
    return _names(ws)


def connection_acl(ws, provider, key, source_acls, levels=None):
    """(ACL, dropped) of a synced file as the index tags it: its source-policy
    ACL (source_acls: connector_acls(ws, details=True)), else its connection's
    level's (levels: integrations_store.chunk_access_levels, read once)."""
    if key in source_acls:
        return source_acls[key]
    if levels is None:
        levels = integrations_store.chunk_access_levels(ws)
    return access.level_acl(levels.get(provider, "restricted")), 0
