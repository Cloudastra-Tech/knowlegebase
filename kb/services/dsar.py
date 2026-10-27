"""
Data requests about one member (DSAR, platform.md PL-12): everything the
workspace holds about them, and erasing it.

EXPORT: their member row (never the password hash), the questions they asked
(the question log, text as the workspace keeps it), their chats with every
message, the feedback they gave, their API keys and invites, and the audit
events where they are the actor.

ERASE: deletes their questions, chats (messages, feedback on them, share
links), folders, feedback, sign-in events, invites to them, API keys, sessions,
their place in access groups, and the member itself. Documents shared with them
by name keep that grant under their pseudonym (no one holds it: fails closed),
and the search index's ACLs are re-tagged without them. Where they appear as who made or changed something
(a provider they added, an invite they sent, a verified answer they own) the
username is replaced by a pseudonym. The audit log isn't edited (it's a hash
chain, and keeps what happened): from now on it shows the pseudonym in their
place (audit_store.erase_actor), and its chain still verifies.
"""

import json

import audit_store
import db
import members_store

# Columns that name the member who did something, in tables with a workspace column.
PERSON_COLUMNS = ("created_by", "updated_by", "installed_by", "connected_by", "invited_by",
                  "requested_by", "revoked_by", "verified_by", "owner", "delete_requested_by",
                  "suspended_by", "support_granted_by")


def _dicts(conn, sql, *args, drop=()):
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = {k: r[k] for k in r.keys() if k not in drop}
        for k in ("groups", "sources", "tools", "reasons", "scopes", "snapshot", "steps"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        out.append(d)
    return out


def export_member(ws, username):
    """Everything about one member of `ws`, as a dict (None if there's no such member)."""
    member = members_store.get(username)
    if not member or member["workspace"] != ws:
        return None
    u = member["username"]
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM members WHERE username = ?", (u,)).fetchone()
        out = {"member": {k: row[k] for k in row.keys() if k != "password_hash"},
               "questions": _dicts(conn, "SELECT * FROM question_log WHERE workspace = ? AND "
                                         "user = ? ORDER BY at", ws, u, drop=("embedding",)),
               "conversations": [],
               "folders": _dicts(conn, "SELECT id, name, created_at FROM conversation_folders "
                                       "WHERE workspace = ? AND owner = ?", ws, u),
               "feedback": _dicts(conn, "SELECT message_id, rating, reasons, comment, "
                                        "created_at, updated_at FROM message_feedback WHERE "
                                        "workspace = ? AND user = ?", ws, u),
               "api_keys": _dicts(conn, "SELECT name, kind, scopes, created_at, last_used_at, "
                                        "expires_at, revoked_at FROM api_keys WHERE workspace = ? "
                                        "AND created_by = ?", ws, u),
               "invites": _dicts(conn, "SELECT email, role, invited_by, created_at, accepted_at, "
                                       "revoked_at FROM invites WHERE workspace = ? AND email = ?",
                                 ws, u),
               "access_groups": _dicts(conn, "SELECT g.name, g.source, m.added_at FROM "
                                             "group_members m JOIN access_groups g ON "
                                             "g.workspace = m.workspace AND g.id = m.group_id "
                                             "WHERE m.workspace = ? AND m.username = ?", ws, u),
               "shared_documents": _dicts(conn, "SELECT doc_key, created_at FROM doc_acl WHERE "
                                                "workspace = ? AND principal_type = 'user' AND "
                                                "lower(principal) = ?", ws, u)}
        out["member"]["groups"] = json.loads(out["member"]["groups"] or "[]")
        for c in _dicts(conn, "SELECT id, title, role_used, created_at, updated_at, deleted_at "
                              "FROM conversations WHERE workspace = ? AND owner = ? "
                              "ORDER BY created_at", ws, u):
            c["messages"] = _dicts(conn, "SELECT id, parent_id, author, content, state, sources, "
                                         "created_at FROM messages WHERE conversation_id = ? "
                                         "ORDER BY created_at", c["id"])
            out["conversations"].append(c)
    finally:
        conn.close()
    out["audit_events"] = audit_store.by_actor(ws, u)
    return out


def erase_member(ws, username):
    """Erase one member of `ws` (see the top of this file). Returns (pseudonym,
    {what: rows}). The caller has checked they may be erased."""
    u = members_store.get(username)["username"]
    pseudonym = audit_store.erase_actor(ws, u)
    counts = {}
    conn = db.connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")

        def run(what, sql, *args):
            n = conn.execute(sql, args).rowcount
            if n:
                counts[what] = counts.get(what, 0) + n

        for (cid,) in conn.execute("SELECT id FROM conversations WHERE workspace = ? AND "
                                   "owner = ?", (ws, u)).fetchall():
            conn.execute("DELETE FROM conversation_search WHERE conversation_id = ?", (cid,))
        run("conversations", "DELETE FROM conversations WHERE workspace = ? AND owner = ?", ws, u)
        run("folders", "DELETE FROM conversation_folders WHERE workspace = ? AND owner = ?", ws, u)
        run("questions", "DELETE FROM question_log WHERE workspace = ? AND user = ?", ws, u)
        run("cached_answers", "DELETE FROM answer_cache WHERE workspace = ? AND username = ?",
            ws, u)
        run("alerts", "DELETE FROM abuse_alerts WHERE workspace = ? AND subject = ?", ws, u)
        run("feedback", "DELETE FROM message_feedback WHERE workspace = ? AND user = ?", ws, u)
        run("sign_in_events", "DELETE FROM sso_events WHERE workspace = ? AND lower(email) = ?",
            ws, u)
        run("scim_groups", "DELETE FROM scim_group_members WHERE username = ? AND group_id IN "
                           "(SELECT id FROM scim_groups WHERE workspace = ?)", u, ws)
        # ACCESS (access.py): out of every group; a document shared with them by name
        # keeps the grant under the pseudonym, which no one holds, so it fails closed
        # (dropping it would hand the document to its folder's default).
        run("groups", "DELETE FROM group_members WHERE workspace = ? AND username = ?", ws, u)
        shared = [r[0] for r in conn.execute(
            "SELECT DISTINCT doc_key FROM doc_acl WHERE workspace = ? AND principal_type = 'user' "
            "AND lower(principal) = ?", (ws, u)).fetchall()]
        run("document_access", "UPDATE OR REPLACE doc_acl SET principal = ? WHERE workspace = ? "
                               "AND principal_type = 'user' AND lower(principal) = ?",
            pseudonym, ws, u)
        run("invites", "DELETE FROM invites WHERE workspace = ? AND lower(email) = ?", ws, u)
        run("api_keys", "DELETE FROM api_keys WHERE workspace = ? AND created_by = ?", ws, u)
        run("sessions", "DELETE FROM sessions WHERE username = ?", u)
        run("member", "DELETE FROM members WHERE username = ? AND workspace = ?", u, ws)
        for table in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE "
                                                  "type = 'table'").fetchall()]:
            if table.startswith("sqlite_") or table.startswith("conversation_search"):
                continue
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            where = "workspace = ?" if "workspace" in columns else "slug = ?" \
                if table == "workspaces" else None
            if where is None:
                continue
            for column in PERSON_COLUMNS:
                if column in columns:
                    run("pseudonymised", f'UPDATE "{table}" SET {column} = ? WHERE {where} AND '
                                         f"lower({column}) = ?", pseudonym, ws, u)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    _retag(ws, shared)
    return pseudonym, counts


def _retag(ws, docs):
    """Their username out of the search index's ACLs too: the uploads shared with
    them by name, and every file of a connection following its source's sharing
    (mapped to members: they aren't one any more). Re-tags only, no embedding."""
    import integrations_store
    from kb.services import indexing
    if docs:
        indexing.schedule_index(ws, docs=docs)
    for row in integrations_store.list_for(ws):
        if integrations_store.policy_of(row) == "source":
            indexing.schedule_retag(ws, row["provider"])
