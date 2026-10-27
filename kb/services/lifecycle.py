"""
A workspace's life (platform.md PL-06): export it, delete it after a grace
period, purge it; and its audit log's retention (PL-12). The state is in
lifecycle_store.py; the screens are kb/routers/workspace.py (admins) and
kb/routers/operator.py (operators).

EXPORT (job "workspace_export"): one ZIP, kept EXPORT_HOURS, with
    manifest.json          what's in it, with each file's sha256
    members.csv            everyone in the workspace (never a password hash)
    audit.jsonl            the audit log, as hashed (erased people as pseudonyms)
    conversations.jsonl    chats still inside the workspace's chat retention
    verified_answers.jsonl the verified answers
    settings.json          the workspace's settings: sign-in, providers,
                           integrations, chat, API, Slack, assistant, glossary,
                           source rules. WITHOUT SECRETS: each part lists the
                           columns it takes, so a new secret column never slips in
    docs/<access>/...      the uploaded documents (synced copies come back from
                           their source by connecting it again)

DELETE: an admin asks (typing the workspace's name); it's purged GRACE_DAYS
later unless cancelled. Meanwhile only admins sign in, the admins are emailed,
and the request is in the workspace's audit log and the operators' (PLATFORM).

PURGE (job "workspace_purge", queued by the hourly "workspace_purge_due"):
    1. each connector's disconnect (revoking its tokens at the provider), and
       the Slack app uninstalled (its bot token revoked)
    2. API keys and SCIM tokens revoked
    3. its search index collections dropped, its documents and exports deleted
    4. every row of it deleted (FK cascades, and each table with a workspace
       column), sessions of its members included
    5. its data keys deleted: anything still sealed under them, in a backup say,
       can never be opened (crypto-shredding, secret_box.shred)
The operators' chain keeps a record that it was purged: when, who asked, how
many rows, never the content.
"""

import csv
import hashlib
import io
import json
import logging
import os
import shutil
import tempfile
import time
import zipfile

import audit_store
import conversations_store
import db
import docs_store
import ingest
import integrations_store
import lifecycle_store as store
import mailer
import members_store
import secret_box
import slack_store
import workspaces
from connectors import registry
from kb import jobs, state

log = logging.getLogger("uvicorn.error")

AUDIT_COLUMNS = ("id", "at", "workspace", "actor", "actor_type", "action", "target_type",
                 "target_id", "ip", "user_agent", "details", "prev_hash", "hash")
MEMBER_COLUMNS = ("username", "name", "role", "override_role", "group_role", "groups",
                  "disabled", "has_password", "created_at", "last_login_at")
# settings.json: (part, SQL, its columns). Only these columns are exported.
SETTINGS = (
    ("workspace", "SELECT {} FROM workspaces WHERE slug = ?",
     ("slug", "name", "created_at", "require_sso", "role_source", "group_admin_days",
      "question_log_mode", "question_log_days", "audit_days", "support_until")),
    ("domains", "SELECT {} FROM workspace_domains WHERE workspace = ?",
     ("domain", "verified_at")),
    ("sign_in_providers", "SELECT {} FROM sso_connections WHERE workspace = ?",
     ("id", "protocol", "label", "issuer", "client_id", "metadata", "enabled", "group_claim",
      "logout_at_idp", "created_by", "created_at", "updated_at")),
    ("group_role_rules", "SELECT {} FROM group_role_rules WHERE connection_id IN "
                         "(SELECT id FROM sso_connections WHERE workspace = ?)",
     ("connection_id", "group_name", "role")),
    ("scim_group_rules", "SELECT {} FROM scim_group_rules WHERE workspace = ?",
     ("group_name", "role")),
    ("integrations", "SELECT {} FROM integrations WHERE workspace = ?",
     ("provider", "status", "account", "settings", "connected_by", "connected_at",
      "last_sync_at", "doc_count")),
    ("chat_settings", "SELECT {} FROM chat_settings WHERE workspace = ?",
     ("retention_days", "sharing_enabled", "updated_by", "updated_at")),
    ("api_settings", "SELECT {} FROM api_settings WHERE workspace = ?",
     ("api_enabled", "mcp_enabled", "mcp_tools", "updated_by", "updated_at")),
    ("api_keys", "SELECT {} FROM api_keys WHERE workspace = ?",
     ("name", "kind", "scopes", "created_by", "created_at", "last_used_at", "expires_at",
      "revoked_at")),
    ("slack", "SELECT {} FROM slack_installs WHERE workspace = ?",
     ("mode", "client_id", "status", "team_id", "team_name", "unknown_users", "auto_answer",
      "auto_answer_channels", "installed_by", "installed_at")),
    ("assistant", "SELECT {} FROM assistant_settings WHERE workspace = ?",
     ("assistant_name", "tone", "instructions", "language", "allow_wikipedia",
      "allow_currency", "show_steps", "adapt_verified", "updated_by", "updated_at")),
    ("glossary", "SELECT {} FROM glossary WHERE workspace = ?",
     ("term", "definition", "synonyms", "created_by", "created_at")),
    ("source_rules", "SELECT {} FROM doc_rules WHERE workspace = ?",
     ("match_type", "match_value", "action", "weight", "created_by", "created_at")),
)
VERIFIED_COLUMNS = ("id", "title", "variants", "answer_md", "sources", "access", "owner",
                    "verified_by", "verified_at", "review_days", "status", "created_by",
                    "created_at", "updated_at")
JSON_COLUMNS = ("metadata", "settings", "scopes", "mcp_tools", "auto_answer_channels",
                "synonyms", "variants", "sources")


def _rows(conn, sql, columns, *args):
    """Rows as dicts of exactly `columns`; a table this database lacks gives none."""
    try:
        rows = conn.execute(sql.format(", ".join(columns)), args).fetchall()
    except Exception as e:
        if "no such" in str(e):
            return []
        raise
    out = []
    for r in rows:
        d = {c: r[c] for c in columns}
        for c in JSON_COLUMNS:
            if isinstance(d.get(c), str):
                try:
                    d[c] = json.loads(d[c])
                except ValueError:
                    pass
        out.append(d)
    return out


# ---- Export ------------------------------------------------------------------------------
def start_export(ws, by):
    export = store.new_export(ws, by)
    jobs.enqueue("workspace_export", ws, {"export_id": export["id"]}, lane=f"export:{ws}")
    return export


def _settings(conn, ws):
    out = {}
    for part, sql, columns in SETTINGS:
        rows = _rows(conn, sql, columns, ws)
        out[part] = rows[0] if part in ("workspace", "chat_settings", "api_settings", "slack",
                                        "assistant") and rows else rows
    out["scim"] = {"tokens": conn.execute("SELECT COUNT(*) FROM scim_tokens WHERE workspace = ? "
                                          "AND revoked_at IS NULL", (ws,)).fetchone()[0],
                   "groups": [r[0] for r in conn.execute(
                       "SELECT display_name FROM scim_groups WHERE workspace = ? ORDER BY 1",
                       (ws,))]}
    return out


def _members_csv(ws):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(MEMBER_COLUMNS)
    members = members_store.list_members(ws)
    for m in members:
        writer.writerow([json.dumps(m[c]) if c == "groups" else m[c] for c in MEMBER_COLUMNS])
    return out.getvalue(), len(members)


def _conversations(conn, ws, now):
    """Chats inside the workspace's chat retention (the nightly purge may not
    have run yet), each with its messages."""
    days = conversations_store.chat_settings(ws)["retention_days"]
    since = now - days * 86400 if days else 0
    for c in conn.execute("SELECT id, owner, title, role_used, created_at, updated_at "
                          "FROM conversations WHERE workspace = ? AND deleted_at IS NULL "
                          "AND updated_at >= ? ORDER BY created_at", (ws, since)).fetchall():
        msgs = _rows(conn, "SELECT {} FROM messages WHERE conversation_id = ? ORDER BY created_at",
                     ("id", "parent_id", "author", "content", "state", "sources", "created_at"),
                     c["id"])
        yield {**dict(c), "messages": msgs}


def build_export(ws, path, by, now=None):
    """Write the workspace's export to `path` (a ZIP). Returns its counts."""
    now = now or time.time()
    files, counts = {}, {}

    def put(z, name, data):
        data = data.encode() if isinstance(data, str) else data
        z.writestr(name, data)
        files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    conn = db.connect()
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            text, counts["members"] = _members_csv(ws)
            put(z, "members.csv", text)
            lines = [json.dumps({c: e[c] for c in AUDIT_COLUMNS})
                     for e in audit_store.iter_events(ws)]
            counts["audit_events"] = len(lines)
            put(z, "audit.jsonl", "".join(line + "\n" for line in lines))
            chats = [json.dumps(c) for c in _conversations(conn, ws, now)]
            counts["conversations"] = len(chats)
            put(z, "conversations.jsonl", "".join(line + "\n" for line in chats))
            verified = _rows(conn, "SELECT {} FROM verified_answers WHERE workspace = ? "
                                   "ORDER BY created_at", VERIFIED_COLUMNS, ws)
            counts["verified_answers"] = len(verified)
            put(z, "verified_answers.jsonl", "".join(json.dumps(v) + "\n" for v in verified))
            put(z, "settings.json", json.dumps(_settings(conn, ws), indent=2, sort_keys=True))
            root, counts["documents"] = workspaces.docs_root(ws), 0
            for access in docs_store.ACCESS_LEVELS:
                folder = os.path.join(root, access)
                if not os.path.isdir(folder):
                    continue
                for name in sorted(os.listdir(folder)):
                    full = os.path.join(folder, name)
                    if os.path.isfile(full) and not name.startswith("."):
                        with open(full, "rb") as f:
                            put(z, f"docs/{access}/{name}", f.read())
                        counts["documents"] += 1
            z.writestr("manifest.json", json.dumps({
                "workspace": ws, "name": members_store.workspace_name(ws), "generated_at": now,
                "requested_by": by, "counts": counts, "files": files}, indent=2))
    finally:
        conn.close()
    return counts


@jobs.handler("workspace_export", max_attempts=2)
def run_export(job):
    ws, eid = job.workspace, job.payload["export_id"]
    export = store.get_export(ws, eid)
    if not export:
        return {"skipped": "gone"}
    store.update_export(eid, status="running")
    folder = store.export_dir(ws)
    os.makedirs(folder, exist_ok=True)
    name = f"{eid}.zip"
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".part")
    os.close(fd)
    try:
        counts = build_export(ws, tmp, export["requested_by"])
        os.replace(tmp, os.path.join(folder, name))
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        store.update_export(eid, status="failed", error=type(e).__name__, finished_at=time.time())
        raise
    now = time.time()
    store.update_export(eid, status="done", file_name=name, counts=counts, finished_at=now,
                        size=os.path.getsize(os.path.join(folder, name)), error=None,
                        expires_at=now + store.EXPORT_HOURS * 3600)
    return {"export_id": eid, **counts}


def export_path(export):
    return os.path.join(store.export_dir(export["workspace"]), export["file_name"])


@jobs.periodic("workspace_exports_prune", lambda: 60 * 60)
def prune_exports(job=None, now=None):
    """Delete exports past their EXPORT_HOURS (and failed ones), files and rows."""
    n = 0
    for ws, eid, file_name in store.expired_exports(now):
        if file_name:
            try:
                os.remove(os.path.join(store.export_dir(ws), file_name))
            except FileNotFoundError:
                pass
        store.forget_export(eid)
        n += 1
    return {"deleted": n}


# ---- Delete, after a grace period -------------------------------------------------------
def _tell_admins(ws, title, body):
    """Email the workspace's admins (best effort: it's in the audit log either way)."""
    for admin in members_store.enabled_admins(ws):
        if "@" not in admin:
            continue
        try:
            mailer.send(admin, "notice", title=title, body=body)
        except mailer.MailError:
            log.warning("Couldn't email %s about workspace %s", admin, ws)


def _when(t):
    return time.strftime("%d %B %Y, %H:%M UTC", time.gmtime(t))


def schedule_deletion(ws, by):
    """Purge it after the grace period. Members are signed out now (admins stay,
    to export or cancel). Returns when it will be purged."""
    when = store.schedule_deletion(ws, by)
    keep = set(members_store.enabled_admins(ws))
    conn = db.connect()
    try:
        with conn:
            conn.execute(f"DELETE FROM sessions WHERE workspace = ? AND username NOT IN "
                         f"({', '.join('?' * len(keep)) or 'NULL'})", (ws, *keep))
    finally:
        conn.close()
    name = members_store.workspace_name(ws)
    audit_store.record(audit_store.PLATFORM, "workspace.delete_requested", actor="system",
                       actor_type="system", target_type="workspace", target_id=ws,
                       details={"requested_by": by, "purge_at": when})
    _tell_admins(ws, f"{name} will be deleted on {_when(when)}",
                 f"{by} asked for the {name} workspace of the Knowledge assistant to be deleted. "
                 f"On {_when(when)} its documents, chats, members, settings and connections "
                 "are deleted for good. Until then only admins can sign in: export the "
                 "workspace, or cancel the deletion, under Access > Data.")
    return when


def cancel_deletion(ws, by):
    if not store.cancel_deletion(ws):
        return False
    name = members_store.workspace_name(ws)
    audit_store.record(audit_store.PLATFORM, "workspace.delete_cancelled", actor="system",
                       actor_type="system", target_type="workspace", target_id=ws,
                       details={"cancelled_by": by})
    _tell_admins(ws, f"{name} won't be deleted",
                 f"{by} cancelled the deletion of the {name} workspace. Everyone can sign in "
                 "again.")
    return True


@jobs.periodic("workspace_purge_due", lambda: 60 * 60)
def purge_due(job=None, now=None):
    due = store.due_for_purge(now)
    for ws in due:
        jobs.enqueue("workspace_purge", payload={"workspace": ws}, lane=f"purge:{ws}",
                     dedupe_key=f"purge:{ws}")
    return {"queued": len(due)}


@jobs.handler("workspace_purge", max_attempts=5)
def run_purge(job):
    ws = job.payload["workspace"]
    deleting_at = store.state(ws)["deleting_at"]
    if not deleting_at or deleting_at > time.time():
        return {"skipped": "not due (cancelled?)"}
    return purge(ws)


def _revoke_outside(ws):
    """Revoke what the providers let us: each connector's tokens, the Slack bot."""
    done = []
    for row in integrations_store.list_for(ws):
        c = registry.CONNECTORS.get(row["provider"])
        if c is None:
            continue
        try:
            c.disconnect(ws)
            done.append(row["provider"])
        except Exception:
            log.exception("Purge: %s couldn't revoke its access for workspace %s",
                          row["provider"], ws)
    install = slack_store.get(ws)
    if install and install["status"] == "installed":
        from kb.services import slack
        try:
            slack.uninstall(ws)
            done.append("slack")
        except Exception:
            log.exception("Purge: couldn't uninstall Slack for workspace %s", ws)
    return done


def collections_of(ws):
    """The names of the workspace's search index collections (current and shadow)."""
    if ws == workspaces.DEFAULT or not os.path.isdir(ingest.DB_FOLDER):
        return []
    import chromadb
    with ingest.CHROMA_LOCK:
        found = chromadb.PersistentClient(path=ingest.DB_FOLDER).list_collections()
    base = workspaces.collection(ws)
    names = [getattr(c, "name", c) for c in found]
    return [n for n in names if n == base or n.startswith(base + "__g")]


def _drop_collections(ws):
    import chromadb
    names = collections_of(ws)
    with ingest.CHROMA_LOCK:
        client = chromadb.PersistentClient(path=ingest.DB_FOLDER)
        for name in names:
            client.delete_collection(name)
    return names


def workspace_dirs(ws):
    """Folders holding the workspace's files: its own folder (documents and synced
    copies) and its exports."""
    if ws == workspaces.DEFAULT:
        return []
    return [os.path.dirname(workspaces.docs_root(ws)), store.export_dir(ws)]


def _tables(conn):
    """{table: [columns]} of every table but SQLite's own and FTS shadows."""
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    out = {}
    for name in names:
        if name.startswith("sqlite_") or name.startswith("conversation_search_"):
            continue
        out[name] = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
    return out


def _delete_rows(ws):
    """Every row of the workspace, in one transaction. Returns {table: rows}."""
    counts = {}
    conn = db.connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        tables = _tables(conn)
        for (cid,) in conn.execute("SELECT id FROM conversations WHERE workspace = ?",
                                   (ws,)).fetchall():
            conn.execute("DELETE FROM conversation_search WHERE conversation_id = ?", (cid,))
        teams = [r[0] for r in conn.execute("SELECT team_id FROM slack_installs WHERE "
                                            "workspace = ? AND team_id IS NOT NULL", (ws,))]
        for team in teams:
            conn.execute("DELETE FROM slack_events WHERE team_id = ?", (team,))
        people = [r[0] for r in conn.execute("SELECT username FROM members WHERE workspace = ?",
                                             (ws,))]
        for username in people:
            conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        # A used sign-up code stays used: kept, but no longer says by whom.
        conn.execute("UPDATE signup_codes_used SET workspace = '-' WHERE workspace = ?", (ws,))
        for table, columns in tables.items():
            if "workspace" in columns and table not in ("workspaces", "signup_codes_used"):
                n = conn.execute(f'DELETE FROM "{table}" WHERE workspace = ?', (ws,)).rowcount
                if n:
                    counts[table] = n
        counts["workspaces"] = conn.execute("DELETE FROM workspaces WHERE slug = ?",
                                            (ws,)).rowcount
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return counts


def _forget_in_memory(ws):
    with state.agents_lock:
        for key in [k for k in state.agents if k[0] == ws]:
            del state.agents[key]
    with state.hits_lock:
        for key in [k for k in state.hits if ws in k]:
            del state.hits[key]


def purge(ws):
    """Delete the workspace for good (see the top of this file). Returns what went."""
    if ws == workspaces.DEFAULT or not workspaces.valid(ws):
        raise ValueError(f"workspace {ws!r} can't be purged")
    conn = db.connect()
    try:
        busy = conn.execute("SELECT COUNT(*) FROM jobs WHERE workspace = ? AND status = "
                            "'running' AND locked_until >= ?", (ws, time.time())).fetchone()[0]
    finally:
        conn.close()
    if busy:
        raise jobs.Retry(f"{busy} job(s) of the workspace are still running")
    st = store.state(ws)
    name = members_store.workspace_name(ws)
    audit_store.record(audit_store.PLATFORM, "workspace.purge_started", actor="system",
                       actor_type="system", target_type="workspace", target_id=ws)
    revoked = _revoke_outside(ws)
    now = time.time()
    conn = db.connect()
    try:
        with conn:
            keys = conn.execute("UPDATE api_keys SET revoked_at = ? WHERE workspace = ? AND "
                                "revoked_at IS NULL", (now, ws)).rowcount
            scim = conn.execute("UPDATE scim_tokens SET revoked_at = ? WHERE workspace = ? AND "
                                "revoked_at IS NULL", (now, ws)).rowcount
    finally:
        conn.close()
    dropped = _drop_collections(ws)
    for folder in workspace_dirs(ws):
        shutil.rmtree(folder, ignore_errors=True)
    rows = _delete_rows(ws)
    shredded = secret_box.shred(ws)
    _forget_in_memory(ws)
    result = {"name": name, "requested_by": st["delete_requested_by"],
              "requested_at": st["delete_requested_at"], "revoked": revoked,
              "api_keys_revoked": keys, "scim_tokens_revoked": scim, "collections": dropped,
              "rows": rows, "keys_shredded": shredded}
    audit_store.record(audit_store.PLATFORM, "workspace.purged", actor="system",
                       actor_type="system", target_type="workspace", target_id=ws,
                       details=result)
    log.warning("Workspace %s purged: %s", ws, json.dumps(rows))
    return result


# ---- Audit retention (PL-12) ------------------------------------------------------------
@jobs.periodic("audit_retention", lambda: 24 * 60 * 60)
def purge_audit(job=None, now=None):
    """Delete audit events older than each workspace's audit_days (if it set one)."""
    now = now or time.time()
    deleted = {}
    for ws, days in store.audit_retention().items():
        n = audit_store.purge_before(ws, now - max(days, store.AUDIT_MIN_DAYS) * 86400)
        if n:
            deleted[ws] = n
            audit_store.record(ws, "audit.retention_purge", actor="system", actor_type="system",
                               target_type="workspace", target_id=ws,
                               details={"deleted": n, "audit_days": days})
    return {"deleted": deleted}
