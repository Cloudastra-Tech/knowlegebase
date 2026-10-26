"""
sync_store.py  —  what each connection has synced, and how its syncs went.

Three tables in config/app.db (migration 0005), keyed like `integrations` by
(workspace, provider):

    source_items   one row per document at the source: its id there, the file
                   its text is in (<docs>/connectors/<provider>/<file_name>),
                   title, link, version, content hash, permissions at the source
                   and why it was skipped, if it was. Replaces manifest.json.
    sync_state     the cursor the next run starts from, and when the last full
                   and incremental runs were
    sync_runs      the history the Integrations screen shows (the last KEEP_RUNS)

A connector folder that still has a manifest.json (written before this) is
imported into source_items the first time it's met (import_manifests): every
entry is kept, the file stays where it is, and the import is recorded in the
`imports` table so it runs once.
"""

import json
import logging
import os
import time

import db
import workspaces

log = logging.getLogger("uvicorn.error")

KEEP_RUNS = 50
ITEM_FIELDS = ("file_name", "title", "url", "kind", "parent_id", "version", "content_hash",
               "mime", "acl", "attrs", "modified", "size", "skipped_reason", "synced_at")


def _conn():
    return db.connect()


def _item(row):
    d = dict(row)
    for key in ("acl", "attrs"):
        d[key] = json.loads(d[key]) if d.get(key) else None
    return d


# ---- source_items -----------------------------------------------------------------
def items(workspace, provider):
    """{source_id: item dict} of a connection."""
    conn = _conn()
    try:
        return {r["source_id"]: _item(r) for r in conn.execute(
            "SELECT * FROM source_items WHERE workspace = ? AND provider = ?",
            (workspace, provider))}
    finally:
        conn.close()


def item(workspace, provider, source_id):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM source_items WHERE workspace = ? AND provider = ? "
                           "AND source_id = ?", (workspace, provider, source_id)).fetchone()
        return _item(row) if row else None
    finally:
        conn.close()


def versions(workspace, provider):
    """{source_id: version} of the items that have a copy (skipped ones are tried again)."""
    conn = _conn()
    try:
        return {r[0]: r[1] or "" for r in conn.execute(
            "SELECT source_id, version FROM source_items WHERE workspace = ? AND provider = ? "
            "AND file_name IS NOT NULL", (workspace, provider))}
    finally:
        conn.close()


def save_item(workspace, provider, source_id, **fields):
    """Insert or update one item (fields: any of ITEM_FIELDS)."""
    unknown = set(fields) - set(ITEM_FIELDS)
    if unknown:
        raise ValueError(f"unknown item fields: {sorted(unknown)}")
    for key in ("acl", "attrs"):
        if key in fields and fields[key] is not None:
            fields[key] = json.dumps(fields[key], sort_keys=True)
    fields.setdefault("synced_at", time.time())
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
    conn = _conn()
    try:
        with conn:
            conn.execute(f"""INSERT INTO source_items (workspace, provider, source_id, {cols})
                             VALUES (?, ?, ?, {marks})
                             ON CONFLICT(workspace, provider, source_id) DO UPDATE SET {updates}""",
                         (workspace, provider, source_id, *fields.values()))
    finally:
        conn.close()


def delete_item(workspace, provider, source_id):
    conn = _conn()
    try:
        with conn:
            conn.execute("DELETE FROM source_items WHERE workspace = ? AND provider = ? AND "
                         "source_id = ?", (workspace, provider, source_id))
    finally:
        conn.close()


def forget(workspace, provider):
    """A disconnected connection: its items, state and history go."""
    conn = _conn()
    try:
        with conn:
            for table in ("source_items", "sync_state", "sync_runs"):
                conn.execute(f"DELETE FROM {table} WHERE workspace = ? AND provider = ?",
                             (workspace, provider))
            conn.execute("DELETE FROM imports WHERE source = ?",
                         (_import_key(workspace, provider),))
    finally:
        conn.close()


def doc_info(workspace):
    """{doc key: {"title", "url", "provider", "modified"}} for every synced file of
    a workspace, keyed as ingest.doc_files keys them (connectors/<provider>/<file>);
    ingest puts these on the file's chunks so answers cite the source by name."""
    import connectors.registry as registry
    import_manifests(workspace)
    conn = _conn()
    try:
        rows = conn.execute("SELECT provider, file_name, title, url, modified FROM source_items "
                            "WHERE workspace = ? AND file_name IS NOT NULL", (workspace,))
        return {f"connectors/{r['provider']}/{r['file_name']}":
                {"title": r["title"] or "", "url": r["url"] or "",
                 "provider": registry.label(r["provider"]), "modified": r["modified"] or ""}
                for r in rows}
    finally:
        conn.close()


def counts(workspace, provider):
    """(synced, skipped) items of a connection."""
    conn = _conn()
    try:
        row = conn.execute("""SELECT SUM(file_name IS NOT NULL), SUM(skipped_reason IS NOT NULL)
                              FROM source_items WHERE workspace = ? AND provider = ?""",
                           (workspace, provider)).fetchone()
        return row[0] or 0, row[1] or 0
    finally:
        conn.close()


def skipped(workspace, provider, limit=50):
    conn = _conn()
    try:
        return [{"title": r["title"] or r["source_id"], "url": r["url"],
                 "reason": r["skipped_reason"]} for r in conn.execute(
            "SELECT source_id, title, url, skipped_reason FROM source_items WHERE workspace = ? "
            "AND provider = ? AND skipped_reason IS NOT NULL ORDER BY title LIMIT ?",
            (workspace, provider, limit))]
    finally:
        conn.close()


# ---- manifest.json -> source_items ---------------------------------------------------
def _import_key(workspace, provider):
    return f"manifest:{workspace}:{provider}"


def import_manifests(workspace):
    """Import every connector folder's manifest.json of a workspace that hasn't
    been imported yet. Returns how many items were added."""
    root = os.path.join(workspaces.docs_root(workspace), "connectors")
    if not os.path.isdir(root):
        return 0
    added = 0
    for provider in sorted(os.listdir(root)):
        path = os.path.join(root, provider, "manifest.json")
        if os.path.isfile(path):
            added += import_manifest(workspace, provider, path)
    return added


def import_manifest(workspace, provider, path):
    """One manifest.json ({file name: {id, title, url, modified, provider}}) into
    source_items, once. Items already there (a sync since) are left as they are."""
    key = _import_key(workspace, provider)
    conn = _conn()
    try:
        if conn.execute("SELECT 1 FROM imports WHERE source = ?", (key,)).fetchone():
            return 0
    finally:
        conn.close()
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
        if not isinstance(manifest, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        log.warning("Sync: couldn't read %s; it isn't imported", path)
        manifest = {}
    folder = os.path.dirname(path)
    added = 0
    now = time.time()
    conn = _conn()
    try:
        with conn:
            for name, info in manifest.items():
                info = info if isinstance(info, dict) else {}
                if os.path.basename(name) != name or name == "manifest.json":
                    continue
                source_id = str(info.get("id") or os.path.splitext(name)[0])
                size = (os.path.getsize(os.path.join(folder, name))
                        if os.path.isfile(os.path.join(folder, name)) else None)
                added += conn.execute(
                    """INSERT OR IGNORE INTO source_items (workspace, provider, source_id,
                           file_name, title, url, kind, version, modified, size, mime, synced_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (workspace, provider, source_id, name, info.get("title") or name,
                     info.get("url"), "file" if provider == "google_drive" else "page",
                     info.get("modified") or "", info.get("modified"), size,
                     "application/pdf" if name.endswith(".pdf") else "text/plain",
                     now)).rowcount
            conn.execute("INSERT OR IGNORE INTO imports (source, imported_at) VALUES (?, ?)",
                         (key, now))
            if added:
                conn.execute("""INSERT INTO sync_runs (workspace, provider, kind, status,
                                    started_at, finished_at, upserted)
                                VALUES (?, ?, 'imported', 'done', ?, ?, ?)""",
                             (workspace, provider, now, now, added))
    finally:
        conn.close()
    if added:
        log.warning("Sync: imported %d items of %s in workspace %s from manifest.json",
                    added, provider, workspace)
    return added


# ---- sync_state --------------------------------------------------------------------
def state(workspace, provider):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM sync_state WHERE workspace = ? AND provider = ?",
                           (workspace, provider)).fetchone()
        return dict(row) if row else {"workspace": workspace, "provider": provider,
                                      "cursor": None, "last_full_sync_at": None,
                                      "last_incremental_at": None, "consecutive_failures": 0,
                                      "pending_deletes": None}
    finally:
        conn.close()


def save_state(workspace, provider, **fields):
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
    conn = _conn()
    try:
        with conn:
            conn.execute(f"""INSERT INTO sync_state (workspace, provider, {cols})
                             VALUES (?, ?, {marks})
                             ON CONFLICT(workspace, provider) DO UPDATE SET {updates}""",
                         (workspace, provider, *fields.values()))
    finally:
        conn.close()


# ---- sync_runs ---------------------------------------------------------------------
def start_run(workspace, provider, kind):
    conn = _conn()
    try:
        with conn:
            return conn.execute("INSERT INTO sync_runs (workspace, provider, kind, started_at) "
                                "VALUES (?, ?, ?, ?)",
                                (workspace, provider, kind, time.time())).lastrowid
    finally:
        conn.close()


def finish_run(run_id, status, **counts):
    fields = {"status": status, "finished_at": time.time(), **counts}
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = _conn()
    try:
        with conn:
            row = conn.execute("SELECT workspace, provider FROM sync_runs WHERE id = ?",
                               (run_id,)).fetchone()
            conn.execute(f"UPDATE sync_runs SET {cols} WHERE id = ?", (*fields.values(), run_id))
            if row:
                conn.execute("""DELETE FROM sync_runs WHERE workspace = ? AND provider = ? AND id
                                NOT IN (SELECT id FROM sync_runs WHERE workspace = ? AND provider = ?
                                        ORDER BY id DESC LIMIT ?)""",
                             (row[0], row[1], row[0], row[1], KEEP_RUNS))
    finally:
        conn.close()


def runs(workspace, provider, limit=KEEP_RUNS):
    conn = _conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sync_runs WHERE workspace = ? AND provider = ? ORDER BY id DESC LIMIT ?",
            (workspace, provider, limit))]
    finally:
        conn.close()


def last_run(workspace, provider):
    got = runs(workspace, provider, 1)
    return got[0] if got else None
