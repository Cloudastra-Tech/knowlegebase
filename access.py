"""
access.py  —  who may see what (platform.md PL-09): PRINCIPALS and ACLs.

Roles (public, hr, admin) gate SCREENS. Access groups gate CONTENT. Every chunk
in the search index carries an ACL: the principals that may read it.

    everyone            every member of the workspace
    user:<username>     one member (their username, the email for SSO users)
    group:<id>          an access group (groups_store.py); the built-in one,
                        group:hr, is everyone whose role is HR or Admin, so the
                        old "restricted" level is exactly ACL [group:hr]
    domain:<domain>     members whose email is at a domain the workspace has
                        verified (a source shared "with everyone at acme.com")
    nobody              held by no one: an ACL whose every grant failed to map
                        (Chroma takes no empty lists)

A VIEWER is the set of principals someone holds, with the role (public / hr)
the app is viewed as. Anything that takes a viewer also takes a bare role, which
means that role's own principals only: "public" is [everyone], "hr" adds
[group:hr]. So code and tests from before groups keep their meaning, and a
Slack channel, answered "as public", is exactly [everyone].

An ANSWER's audience is a list of ACLs (CLAUSES): a viewer may read it only if
they hold a principal of EVERY one, since the answer may repeat any passage the
model read. Clauses that include everyone are dropped, so [] is "everyone".
Answers saved before this have only max_access: public is [], restricted is
[[group:hr]].
"""

EVERYONE = "everyone"
HR = "group:hr"
NOBODY = "nobody"
HR_GROUP = "hr"                      # the built-in group's id (groups_store.py)
LEVELS = ("public", "restricted")


def user(username):
    return f"user:{(username or '').strip().lower()}"


def group(group_id):
    return f"group:{group_id}"


def domain(name):
    return f"domain:{(name or '').strip().lower()}"


class Viewer(frozenset):
    """The principals someone holds, and the role the app is viewed as."""

    def __new__(cls, principals=(), role="public"):
        self = super().__new__(cls, {EVERYONE, *principals})
        self.role = role
        return self

    def __repr__(self):
        return f"Viewer({sorted(self)!r}, role={self.role!r})"


def for_role(role):
    """A role's own principals: everyone, and group:hr for HR / Admin."""
    return Viewer([HR] if role == "hr" else [], role)


def of(who):
    """A Viewer for a role ("public" / "hr"), a Viewer, or a set of principals."""
    if isinstance(who, Viewer):
        return who
    if who is None or isinstance(who, str):
        return for_role(who or "public")
    held = set(who)
    return Viewer(held, "hr" if HR in held else "public")


def role_of(who):
    """The role (public / hr) a viewer views the app as."""
    if isinstance(who, Viewer):
        return who.role
    if who is None or isinstance(who, str):
        return who or "public"
    return of(who).role


def may_see_hr(who):
    """May this viewer see restricted HR content (staff pay)? Holders of the
    built-in hr group may: by default, exactly HR and Admin (safety.py)."""
    return HR in of(who)


# ---- ACLs ----------------------------------------------------------------------------
def level_acl(level):
    """The ACL an access level stands for."""
    return [EVERYONE] if level == "public" else [HR]


def level_of_acl(acl):
    """The access level an ACL is tagged with too: public only if everyone may read it."""
    return "public" if EVERYONE in (acl or ()) else "restricted"


def clean_acl(principals):
    """An ACL as stored on chunks: sorted, without duplicates, never empty."""
    acl = sorted({str(p) for p in principals or () if p})
    if EVERYONE in acl:
        return [EVERYONE]
    return acl or [NOBODY]


def chunk_acl(meta):
    """A chunk's ACL: its `acl`, or (an index from before ACLs) its access level's."""
    acl = (meta or {}).get("acl")
    if isinstance(acl, (list, tuple)) and acl:
        return list(acl)
    return level_acl((meta or {}).get("access", "public"))


def can_see(who, acl):
    """May the viewer read something with this ACL?"""
    acl = acl or ()
    return EVERYONE in acl or bool(of(who) & set(acl))


def chroma_filter(who):
    """The Chroma `where` that keeps only chunks the viewer may read."""
    held = sorted(of(who))
    clauses = [{"acl": {"$contains": p}} for p in held]
    return clauses[0] if len(clauses) == 1 else {"$or": clauses}


# ---- An answer's audience (clauses) -----------------------------------------------------
def clauses(acls):
    """ACLs as an answer's audience: those including everyone dropped, duplicates
    and any ACL another one implies (a superset) dropped, sorted. [] = everyone."""
    sets = {frozenset(a) for a in acls if a and EVERYONE not in a}
    sets |= {frozenset([NOBODY]) for a in acls if a is not None and not a}
    kept = [s for s in sets if not any(o < s for o in sets)]
    return sorted(sorted(s) for s in kept)


def source_acl(source):
    """The ACL of an answer's source: its own, or its access level's."""
    acl = source.get("acl")
    if isinstance(acl, list) and acl:
        return acl
    return level_acl(source.get("access") or "public")


def message_clauses(message):
    """The clauses a saved answer's reader must meet: what the model read for it
    (its `acl`, or its max_access) and each of its sources."""
    saved = message.get("acl")
    if saved is None:
        level = message.get("max_access") or "restricted"
        saved = [] if level == "public" else [[HR]]
    found = [list(c) for c in saved]
    found += [source_acl(s) for s in message.get("sources") or ()
              if s.get("acl") or s.get("access")]
    return clauses(found)


def sees(who, message_or_clauses):
    """May the viewer (or EVERY viewer, given a tuple of them) read the answer?"""
    if isinstance(who, tuple):
        return all(sees(w, message_or_clauses) for w in who)
    found = (message_clauses(message_or_clauses) if isinstance(message_or_clauses, dict)
             else message_or_clauses)
    held = of(who)
    return all(held & set(c) for c in found)


def level_of(found):
    """The access level of an answer with these clauses: public only for everyone."""
    return "public" if not found else "restricted"
