"""
Who may read a document, for what's sent or written OUTSIDE a question:
digests (PL-30), summaries and drafts (PL-31). It's access.py's model (PL-09):
a reader is a Viewer (their principals, groups_store.principals_for) and a
document has an ACL; this only puts the two together for these callers.

    viewer_of(member)       a member's Viewer NOW, from their account as it is
                            (None: no member, or turned off). HR / Admin read as
                            hr; the "view as public" switch is the browser's, and a
                            digest has no browser
    Acls(ws).now(doc_key)   the ACL a document has NOW, as the index tags it: an
                            upload's picked one or its folder's level; a synced
                            file's source ACL (permissions.connector_acls) or its
                            connection's level; a connection that's gone, HR only
    may_read(who, *acls)    may `who` (a Viewer, a member dict, a role) read
                            something with ALL of these ACLs (access.can_see)?
"""

import access
import groups_store
import integrations_store
import members_store
from kb.services import permissions

UPLOADS = "uploads"             # the provider of uploaded files (assistant.UPLOADS too)


def access_role(member):
    """'hr' for an HR or admin account, else 'public'; None for nobody."""
    if not member or member.get("disabled"):
        return None
    return "hr" if member.get("role") in members_store.STAFF_ROLES else "public"


def viewer_of(member):
    role = access_role(member)
    if role is None:
        return None
    return groups_store.principals_for(member["workspace"], member["username"], role)


def viewer(who):
    """A Viewer for a Viewer, a member (or session user) dict, or a role."""
    if isinstance(who, access.Viewer):
        return who
    if isinstance(who, dict):
        return viewer_of(who)
    return access.of(who)


def may_read(who, *acls):
    held = viewer(who)
    return held is not None and all(access.can_see(held, acl) for acl in acls)


def provider_of(doc_key):
    parts = (doc_key or "").split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "connectors" else UPLOADS


class Acls:
    """The documents' ACLs now, for one workspace (read once, used for many)."""

    def __init__(self, ws):
        self.ws = ws
        self.picked = groups_store.all_doc_acls(ws)
        self.levels = integrations_store.chunk_access_levels(ws)
        self._source = None

    def source(self):
        if self._source is None:
            self._source = permissions.connector_acls(self.ws)
        return self._source

    def now(self, doc_key):
        provider = provider_of(doc_key)
        if provider == UPLOADS:
            return self.picked.get(doc_key) or access.level_acl(
                "restricted" if doc_key.startswith("restricted/") else "public")
        if provider not in self.levels:
            return [access.HR]
        return permissions.connection_acl(self.ws, provider, doc_key, self.source(),
                                          self.levels)[0]


def doc_acl_now(ws, doc_key):
    return Acls(ws).now(doc_key)


def strictest_level(*acls):
    """The access level (public / restricted) of something read from these ACLs."""
    return "public" if all(access.EVERYONE in (a or ()) for a in acls) else "restricted"
