"""
groups_store.py  —  access groups and document ACLs (config/app.db, tables made
by migrations/versions/0013_access_groups.py; what the principals mean: access.py).

    access_groups   groups that gate content, by source:
                      manual   made and filled by an admin (Access → Groups)
                      idp      the groups sign-in claims carry (members.groups,
                               from the workspace's role source)
                      scim     the groups the identity provider pushes (scim_groups)
                    idp and scim rows are COPIES, read-only here: refresh_external
                    rebuilds them from where they come from. The built-in group
                    "hr" has no row: its members are everyone whose role is HR
                    or Admin, so it is the old "restricted" level.
    group_members   who is in each group
    doc_acl         who may see an uploaded document, when someone picked it

Who someone is to the search (principals_for) is read from the sources directly,
so a SCIM push or a sign-in counts at once, before any refresh.
"""

import contextlib
import hashlib
import json
import secrets
import time

import access
import db

SOURCES = ("manual", "idp", "scim")
NAME_CHARS = 80
DESCRIPTION_CHARS = 300
MAX_GROUPS = 500
SCIM_SOURCE = "scim"              # members.group_source of SCIM's groups (members_store)
HR_NAME = "HR & Admin"


@contextlib.contextmanager
def _db():
    conn = db.connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def idp_id(name):
    return "idp_" + hashlib.sha256(name.strip().lower().encode()).hexdigest()[:16]


def scim_id(group_id):
    return f"scim_{group_id}"


def builtin():
    """The built-in group, as a row."""
    return {"id": access.HR_GROUP, "name": HR_NAME, "source": "builtin", "email": None,
            "external_id": None, "builtin": True,
            "description": "Everyone whose role is HR or Admin: what restricted documents "
                           "were before groups. Change it by changing roles in Members."}


def _row(r):
    return {**dict(r), "builtin": False} if r else None


# ---- Groups -------------------------------------------------------------------------
def list_groups(workspace, refresh=True):
    """Every group of the workspace, the built-in one first, with member counts."""
    if refresh:
        refresh_external(workspace)
    with _db() as conn:
        rows = conn.execute(
            """SELECT g.*, (SELECT COUNT(*) FROM group_members m WHERE m.workspace = g.workspace
                            AND m.group_id = g.id) AS member_count
               FROM access_groups g WHERE g.workspace = ?
               ORDER BY g.source, g.name COLLATE NOCASE""", (workspace,)).fetchall()
    hr = {**builtin(), "member_count": len(staff(workspace))}
    return [hr] + [_row(r) for r in rows]


def get(workspace, group_id):
    if group_id == access.HR_GROUP:
        return builtin()
    with _db() as conn:
        return _row(conn.execute("SELECT * FROM access_groups WHERE workspace = ? AND id = ?",
                                 (workspace, group_id)).fetchone())


class Invalid(ValueError):
    """A group can't be saved: the message says why."""


def _check_name(conn, workspace, name, group_id=None):
    name = " ".join(str(name or "").split())
    if not name or len(name) > NAME_CHARS:
        raise Invalid(f"Give the group a name of up to {NAME_CHARS} characters.")
    if name.casefold() == HR_NAME.casefold() or name.casefold() == access.HR_GROUP:
        raise Invalid("That name is the built-in group's.")
    taken = conn.execute("SELECT id FROM access_groups WHERE workspace = ? AND source = 'manual' "
                         "AND name = ? COLLATE NOCASE", (workspace, name)).fetchone()
    if taken and taken["id"] != group_id:
        raise Invalid("A group with that name already exists.")
    return name


def create(workspace, name, description, created_by):
    now = time.time()
    with _db() as conn:
        if conn.execute("SELECT COUNT(*) FROM access_groups WHERE workspace = ?",
                        (workspace,)).fetchone()[0] >= MAX_GROUPS:
            raise Invalid(f"A workspace has {MAX_GROUPS} groups at most.")
        name = _check_name(conn, workspace, name)
        gid = "g_" + secrets.token_hex(6)
        conn.execute("""INSERT INTO access_groups (workspace, id, name, source, description,
                            created_by, created_at, updated_at)
                        VALUES (?, ?, ?, 'manual', ?, ?, ?, ?)""",
                     (workspace, gid, name, str(description or "")[:DESCRIPTION_CHARS],
                      created_by, now, now))
    return get(workspace, gid)


def update(workspace, group_id, name=None, description=None):
    """A manual group's name or description. None when it isn't one."""
    group = get(workspace, group_id)
    if not group or group["source"] != "manual":
        return None
    with _db() as conn:
        fields = {"updated_at": time.time()}
        if name is not None:
            fields["name"] = _check_name(conn, workspace, name, group_id)
        if description is not None:
            fields["description"] = str(description)[:DESCRIPTION_CHARS]
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE access_groups SET {cols} WHERE workspace = ? AND id = ?",
                     (*fields.values(), workspace, group_id))
    return get(workspace, group_id)


def delete(workspace, group_id):
    """A manual group and its members. False when it isn't one. Documents that
    named it keep the grant, which no one holds any more (fails closed)."""
    with _db() as conn:
        return conn.execute("DELETE FROM access_groups WHERE workspace = ? AND id = ? AND "
                            "source = 'manual'", (workspace, group_id)).rowcount > 0


# ---- Members ------------------------------------------------------------------------
def staff(workspace):
    """The built-in group's members: HR and Admin, not turned off."""
    with _db() as conn:
        return [r["username"] for r in conn.execute(
            "SELECT username FROM members WHERE workspace = ? AND role IN ('hr', 'admin') "
            "AND NOT disabled ORDER BY username", (workspace,))]


def members(workspace, group_id):
    """The usernames in a group."""
    if group_id == access.HR_GROUP:
        return staff(workspace)
    with _db() as conn:
        return [r["username"] for r in conn.execute(
            "SELECT username FROM group_members WHERE workspace = ? AND group_id = ? "
            "ORDER BY username", (workspace, group_id))]


def workspace_members(workspace):
    """{username: name} of the workspace's members."""
    with _db() as conn:
        return {r["username"]: r["name"] for r in conn.execute(
            "SELECT username, name FROM members WHERE workspace = ?", (workspace,))}


def set_members(workspace, group_id, usernames, added_by):
    """Replace a manual group's members. Raises Invalid for someone who isn't a
    member of the workspace; None when the group isn't a manual one."""
    group = get(workspace, group_id)
    if not group or group["source"] != "manual":
        return None
    wanted = sorted({str(u).strip().lower() for u in usernames or () if str(u).strip()})
    known = workspace_members(workspace)
    strangers = [u for u in wanted if u not in known]
    if strangers:
        raise Invalid(f"Not members of this workspace: {', '.join(strangers[:5])}.")
    now = time.time()
    with _db() as conn:
        conn.execute("DELETE FROM group_members WHERE workspace = ? AND group_id = ?",
                     (workspace, group_id))
        conn.executemany("INSERT INTO group_members (workspace, group_id, username, added_by, "
                         "added_at) VALUES (?, ?, ?, ?, ?)",
                         [(workspace, group_id, u, added_by, now) for u in wanted])
        conn.execute("UPDATE access_groups SET updated_at = ? WHERE workspace = ? AND id = ?",
                     (now, workspace, group_id))
    return wanted


# ---- The identity provider's groups -----------------------------------------------------
def _external(conn, workspace):
    """{group id: (name, source, external_id, [usernames])} from members.groups
    (sign-in claims) and scim_groups, as they are now."""
    found = {}
    for r in conn.execute("SELECT id, display_name, external_id FROM scim_groups "
                          "WHERE workspace = ?", (workspace,)):
        found[scim_id(r["id"])] = (r["display_name"], "scim", r["external_id"], [])
    for r in conn.execute("""SELECT m.group_id, m.username FROM scim_group_members m
                             JOIN scim_groups g ON g.id = m.group_id WHERE g.workspace = ?""",
                          (workspace,)):
        found[scim_id(r["group_id"])][3].append(r["username"])
    for r in conn.execute("SELECT username, groups FROM members WHERE workspace = ? AND "
                          "groups IS NOT NULL AND COALESCE(group_source, '') != ?",
                          (workspace, SCIM_SOURCE)):
        try:
            names = json.loads(r["groups"] or "[]")
        except ValueError:
            names = []
        for name in names if isinstance(names, list) else []:
            name = " ".join(str(name).split())[:NAME_CHARS]
            if not name:
                continue
            gid = idp_id(name)
            found.setdefault(gid, (name, "idp", None, []))[3].append(r["username"])
    return found


def refresh_external(workspace):
    """Bring the idp and scim copies in line with where they come from: new groups
    added, gone ones removed, members replaced. Returns how many groups there are."""
    now = time.time()
    with _db() as conn:
        found = _external(conn, workspace)
        have = {r["id"] for r in conn.execute(
            "SELECT id FROM access_groups WHERE workspace = ? AND source != 'manual'",
            (workspace,))}
        for gid in have - set(found):
            conn.execute("DELETE FROM access_groups WHERE workspace = ? AND id = ?",
                         (workspace, gid))
        for gid, (name, source, external_id, usernames) in found.items():
            email = name.lower() if "@" in name else None
            # Two groups can't share a name within a source: the newer one is renamed.
            clash = conn.execute("SELECT id FROM access_groups WHERE workspace = ? AND source = ? "
                                 "AND name = ? COLLATE NOCASE AND id != ?",
                                 (workspace, source, name, gid)).fetchone()
            shown = f"{name} ({gid[-6:]})" if clash else name
            conn.execute("""INSERT INTO access_groups (workspace, id, name, source, email,
                                external_id, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT (workspace, id) DO UPDATE SET name = excluded.name,
                                email = excluded.email, external_id = excluded.external_id,
                                updated_at = excluded.updated_at""",
                         (workspace, gid, shown, source, email, external_id, now, now))
            conn.execute("DELETE FROM group_members WHERE workspace = ? AND group_id = ?",
                         (workspace, gid))
            conn.executemany("INSERT OR IGNORE INTO group_members (workspace, group_id, "
                             "username, added_at) VALUES (?, ?, ?, ?)",
                             [(workspace, gid, u, now) for u in usernames])
    return len(found)


# ---- Who someone is to the search ---------------------------------------------------------
def groups_of(workspace, username):
    """The ids of the groups someone is in (not the built-in one)."""
    username = (username or "").strip().lower()
    with _db() as conn:
        found = {r["group_id"] for r in conn.execute(
            """SELECT m.group_id FROM group_members m JOIN access_groups g
                   ON g.workspace = m.workspace AND g.id = m.group_id
               WHERE m.workspace = ? AND m.username = ? AND g.source = 'manual'""",
            (workspace, username))}
        found |= {scim_id(r["group_id"]) for r in conn.execute(
            """SELECT m.group_id FROM scim_group_members m JOIN scim_groups g ON g.id = m.group_id
               WHERE g.workspace = ? AND m.username = ?""", (workspace, username))}
        row = conn.execute("SELECT groups, group_source FROM members WHERE workspace = ? AND "
                           "username = ?", (workspace, username)).fetchone()
    if row and row["groups"] and (row["group_source"] or "") != SCIM_SOURCE:
        try:
            names = json.loads(row["groups"])
        except ValueError:
            names = []
        found |= {idp_id(n) for n in names if isinstance(n, str) and n.strip()}
    return found


def verified_domains(workspace):
    with _db() as conn:
        return {r["domain"] for r in conn.execute(
            "SELECT domain FROM workspace_domains WHERE workspace = ? AND verified_at IS NOT NULL",
            (workspace,))}


def principals_for(workspace, username, role):
    """The Viewer someone is (access.py): everyone, themselves, the built-in hr
    group when viewing the app as HR / Admin, their groups, and their email's
    domain when the workspace has verified it."""
    username = (username or "").strip().lower()
    held = {access.user(username)} if username else set()
    if role == "hr":
        held.add(access.HR)
    held |= {access.group(g) for g in groups_of(workspace, username)}
    if "@" in username:
        at = username.rsplit("@", 1)[1]
        if at in verified_domains(workspace):
            held.add(access.domain(at))
    return access.Viewer(held, role)


# ---- Uploaded documents' ACLs -------------------------------------------------------------
def doc_acl(workspace, doc_key):
    """The principals picked for a document, or None (its folder decides)."""
    return all_doc_acls(workspace, [doc_key]).get(doc_key)


def all_doc_acls(workspace, keys=None):
    """{doc key: [principals]} of the documents that have one picked."""
    sql, args = "SELECT * FROM doc_acl WHERE workspace = ?", [workspace]
    if keys is not None:
        keys = list(keys)
        if not keys:
            return {}
        sql += f" AND doc_key IN ({','.join('?' * len(keys))})"
        args += keys
    out = {}
    with _db() as conn:
        for r in conn.execute(sql, args):
            kind, value = r["principal_type"], r["principal"]
            out.setdefault(r["doc_key"], []).append(
                access.EVERYONE if kind == "everyone" else f"{kind}:{value}")
    return {k: access.clean_acl(v) for k, v in out.items()}


def _split(principal):
    if principal == access.EVERYONE:
        return "everyone", ""
    kind, _, value = principal.partition(":")
    if kind not in ("group", "user") or not value:
        raise Invalid(f"Unknown principal {principal!r}.")
    return kind, value


def set_doc_acl(workspace, doc_key, principals, created_by):
    """Replace a document's ACL; None or [] removes it (its folder decides again)."""
    rows = sorted({_split(p) for p in principals or ()})
    now = time.time()
    with _db() as conn:
        conn.execute("DELETE FROM doc_acl WHERE workspace = ? AND doc_key = ?",
                     (workspace, doc_key))
        conn.executemany("INSERT INTO doc_acl (workspace, doc_key, principal_type, principal, "
                         "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         [(workspace, doc_key, kind, value, created_by, now)
                          for kind, value in rows])


def move_doc_acl(workspace, old_key, new_key):
    with _db() as conn:
        conn.execute("UPDATE OR REPLACE doc_acl SET doc_key = ? WHERE workspace = ? AND "
                     "doc_key = ?", (new_key, workspace, old_key))
