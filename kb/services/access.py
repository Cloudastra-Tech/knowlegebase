"""
Whether a member may read a document, for what is sent or written OUTSIDE a
question: digests (PL-30), summaries and drafts (PL-31). One place, so a change
to who may see what (group ACLs, PL-09) reaches them all.

    access_role(member)   the access role a member reads as now: "hr" or "public"
                          (rag.ROLE_ACCESS), from their account as it is NOW
    may_read(who, doc)    whether `who` (a role, or a member dict) may read a
                          document described by `doc` ({"access", "doc_key",
                          "provider", "acl"}); fails closed: an unknown level is
                          restricted, and an unknown role is public
    doc_access_now(ws, doc_key)  the access tag the document's chunks have NOW
                          (ingest.doc_meta's rule): an upload by its folder, a
                          synced file by its connection's level; a connection
                          that's gone counts as restricted

NOTE (group ACLs): today only the access tag decides. When documents carry
principals (source_items.acl, chunk metadata), may_read() is where they're
checked; its callers already pass the member and the document's acl.
"""

import integrations_store
import members_store
from rag import ROLE_ACCESS

UPLOADS = "uploads"             # the provider of uploaded files (assistant.UPLOADS too)


def access_role(member):
    """'hr' for an HR or admin account, else 'public'; None for nobody (no
    member, or one that's turned off)."""
    if not member or member.get("disabled"):
        return None
    return "hr" if member.get("role") in members_store.STAFF_ROLES else "public"


def allowed_levels(role):
    return ROLE_ACCESS.get(role, ["public"])


def may_read(who, doc):
    """True if `who` (an access role, or a member dict) may read `doc`."""
    role = who if isinstance(who, str) or who is None else access_role(who)
    if role is None:
        return False
    return ((doc or {}).get("access") or "restricted") in allowed_levels(role)


def provider_of(doc_key):
    parts = (doc_key or "").split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "connectors" else UPLOADS


def doc_access_now(ws, doc_key, levels=None):
    """The access tag `doc_key` has in the index now (see the top of this file).
    levels: integrations_store.chunk_access_levels(ws), if the caller has it."""
    provider = provider_of(doc_key)
    if provider == UPLOADS:
        return "restricted" if doc_key.startswith("restricted/") else "public"
    if levels is None:
        levels = integrations_store.chunk_access_levels(ws)
    return levels.get(provider, "restricted")


def strictest(*levels):
    """The most restrictive of some access levels (anything not public is restricted)."""
    return "public" if all(level == "public" for level in levels) else "restricted"
