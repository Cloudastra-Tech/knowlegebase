"""Connector sync, as background jobs (kb/jobs.py): on request, and every
SYNC_EVERY seconds for every connected integration (the periodic `sync_all`
job). One lane per (workspace, provider): a connection never has two syncs at
once, and a sync asked for while one is queued is the same sync.

One run (sync()):
  1. the connector (connectors/registry.py) lists what changed since the
     connection's cursor (sync_state), as Upsert / Delete / Permissions /
     Skipped events (connectors/base.py);
  2. an Upsert whose version is the one already synced costs nothing (a new
     title or link is just recorded); otherwise its content is fetched, turned
     into text (connectors/extract.py; PDFs stay PDFs) and written to
     <docs>/connectors/<provider>/<file>, and source_items records it;
  3. deletes are applied last, and only if they aren't a MASS DELETE: more than
     MASS_DELETE_PERCENT of the connection's items (and at least
     MASS_DELETE_MIN) in one run is held back, the cursor isn't moved, and the
     card asks an admin to confirm (a sync with confirm_deletes) or look into it;
  4. the cursor is saved, the run recorded in sync_runs, and the EXACT files
     that changed are re-indexed (indexing.schedule_index(ws, docs=...)).
A run that fails part-way keeps what it wrote (and re-indexes it), deletes
nothing and keeps the old cursor. Its error is a code (connectors/base.py
ERRORS), shown in plain words; auth_expired, config and missing_scope wait for
an admin instead of being retried.
"""

import hashlib
import logging
import os
import re
import time

import integrations_store
import sync_store
import workspaces
from connectors import base, extract, registry
from kb import jobs
from kb.services import indexing
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

SYNC_EVERY = 30 * 60              # seconds between automatic connector syncs
DRIVE_SYNC_EVERY = SYNC_EVERY     # the old name
CONNECTORS = registry.CONNECTORS
LABELS = {p: c.LABEL for p, c in CONNECTORS.items()}
SETUP_ENV = {p: c.SETUP_ENV for p, c in CONNECTORS.items()}
MASS_DELETE_PERCENT = 30          # deletes above this share of a connection's items ...
MASS_DELETE_MIN = 10              # ... (and at least this many) wait for an admin
MAX_ITEMS = 10_000                # per connection, unless CONNECTOR_MAX_ITEMS says otherwise


def connector(provider):
    return CONNECTORS.get(provider)


def picks(row, provider):
    c = CONNECTORS[provider]
    return ((row or {}).get("settings") or {}).get(c.SETTINGS_KEY) or []


def ready_to_sync(ws, provider):
    """A connector that syncs only what an admin picked (Drive, Outline, a
    website) has nothing to do until something is picked."""
    c = CONNECTORS[provider]
    return not c.PICKER_REQUIRED or bool(picks(integrations_store.get(ws, provider), provider))


def _file_name(source_id, ext):
    """The file a source item's copy is in: its id if that's a plain file name
    (Drive and Notion ids are, and their files kept those names), else a hash."""
    stem = source_id if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_id) else \
        hashlib.sha256(source_id.encode()).hexdigest()[:32]
    return stem + ext


def _write(folder, name, body):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    partial = path + ".part"
    with open(partial, "wb") as f:
        f.write(body)
    os.replace(partial, path)


def _remove(folder, name):
    try:
        os.remove(os.path.join(folder, name))
    except FileNotFoundError:
        pass


class _Run:
    """One sync run's bookkeeping."""

    def __init__(self, ws, provider, full):
        self.ws, self.provider = ws, provider
        self.folder = workspaces.connector_dir(ws, provider)
        self.items = sync_store.items(ws, provider)
        self.changed = set()                  # doc keys to re-index
        self.counts = {"upserted": 0, "deleted": 0, "skipped": 0, "unchanged": 0}
        self.deletes = []
        self.cursor, self.full, self.limit = None, full, 0

    def key(self, name):
        return f"connectors/{self.provider}/{name}"

    def _drop_file(self, row):
        if row and row.get("file_name"):
            _remove(self.folder, row["file_name"])
            self.changed.add(self.key(row["file_name"]))

    def upsert(self, c, ctx, item):
        row = self.items.get(item.source_id)
        same = (row and row.get("file_name") and row.get("version") == item.version
                and item.version and os.path.exists(os.path.join(self.folder, row["file_name"])))
        meta = {"title": item.title, "url": item.url, "kind": item.kind,
                "parent_id": item.parent_id, "acl": item.acl, "modified": item.modified}
        if item.attrs is not None:
            meta["attrs"] = item.attrs
        if same:
            if (row.get("title"), row.get("url")) != (item.title, item.url):
                self.changed.add(self.key(row["file_name"]))   # citations show the new name
            if any(row.get(k) != v for k, v in meta.items()):
                sync_store.save_item(self.ws, self.provider, item.source_id, **meta)
            self.counts["unchanged"] += 1
            return
        try:
            fetched = c.fetch(ctx, item)
        except (base.NotFound, base.Forbidden) as e:
            return self.skip(item.source_id, "not_found" if isinstance(e, base.NotFound)
                             else "no_access", item.title, item.url)
        if fetched is None:
            return self.skip(item.source_id, "too_large" if item.size else "unsupported",
                             item.title, item.url)
        body = fetched.body
        kind = extract.kind_of(fetched.mime, item.title)
        if kind == "pdf":
            if not isinstance(body, bytes) or b"%PDF-" not in body[:1024]:
                return self.skip(item.source_id, "unreadable", item.title, item.url)
            ext = ".pdf"
        else:
            try:
                text = extract.extract(body, fetched.mime or "text/plain", item.title)
            except extract.Unsupported:
                return self.skip(item.source_id, "unreadable", item.title, item.url)
            if not text.strip() and kind != "txt":
                return self.skip(item.source_id, "empty", item.title, item.url)
            # A title line, so the text carries the document's name into the index.
            body, ext = f"{item.title}\n\n{text}".encode("utf-8"), ".txt"
        name = _file_name(item.source_id, ext)
        digest = hashlib.sha256(body).hexdigest()
        if row and row.get("file_name") and row["file_name"] != name:
            self._drop_file(row)                       # e.g. was a PDF, now exported as text
        unchanged = (row and row.get("content_hash") == digest and row.get("file_name") == name
                     and os.path.exists(os.path.join(self.folder, name)))
        if not unchanged:
            _write(self.folder, name, body)
        if not unchanged or (row.get("title"), row.get("url")) != (item.title, item.url):
            self.changed.add(self.key(name))
        sync_store.save_item(self.ws, self.provider, item.source_id, file_name=name,
                             version=item.version, content_hash=digest, mime=fetched.mime,
                             size=len(body), skipped_reason=None, **meta)
        self.items[item.source_id] = {**(row or {}), "file_name": name, "version": item.version,
                                      "content_hash": digest, "title": item.title,
                                      "url": item.url}
        self.counts["upserted" if not unchanged else "unchanged"] += 1

    def skip(self, source_id, reason, title="", url=""):
        row = self.items.get(source_id)
        self._drop_file(row)
        sync_store.save_item(self.ws, self.provider, source_id, file_name=None, version=None,
                             content_hash=None, skipped_reason=reason, title=title or None,
                             url=url or None)
        self.items[source_id] = {"file_name": None, "skipped_reason": reason}
        self.counts["skipped"] += 1

    def permissions(self, source_id, acl):
        if source_id in self.items:
            sync_store.save_item(self.ws, self.provider, source_id, acl=acl)

    def apply_deletes(self, allow_mass):
        """Apply the run's deletes, unless they are a mass delete. Returns how many
        were held back (0: all applied)."""
        targets = [s for s in dict.fromkeys(self.deletes) if s in self.items]
        synced = sum(1 for r in self.items.values() if r.get("file_name"))
        percent = env_int("CONNECTOR_MASS_DELETE_PERCENT", MASS_DELETE_PERCENT)
        floor = env_int("CONNECTOR_MASS_DELETE_MIN", MASS_DELETE_MIN)
        if not allow_mass and len(targets) >= floor and len(targets) * 100 > synced * percent:
            return len(targets)
        for source_id in targets:
            self._drop_file(self.items.pop(source_id))
            sync_store.delete_item(self.ws, self.provider, source_id)
        self.counts["deleted"] = len(targets)
        return 0


def sync(ws, provider, full=False, confirm_deletes=False):
    """Run one sync of a workspace's connection (see the top of this file).
    Returns the run's summary; raises the connector's error if it failed."""
    c = CONNECTORS[provider]
    row = integrations_store.get(ws, provider)
    if not row:
        return {"skipped": "not connected"}
    sync_store.import_manifests(ws)
    state = sync_store.state(ws, provider)
    run = _Run(ws, provider, full or not state["cursor"])
    run_id = sync_store.start_run(ws, provider, "full" if run.full else "incremental")
    integrations_store.update(ws, provider, status="syncing")
    ctx = base.SyncContext(
        workspace=ws, provider=provider, settings=row.get("settings") or {},
        known={s: r.get("version") or "" for s, r in run.items.items() if r.get("file_name")},
        parents={s: r.get("parent_id") for s, r in run.items.items()}, items=run.items,
        max_items=env_int("CONNECTOR_MAX_ITEMS", MAX_ITEMS), full=full)
    try:
        cursor = None if full else state["cursor"]
        for event in c.changes(ctx, cursor):
            if isinstance(event, base.Upsert):
                run.upsert(c, ctx, event.item)
            elif isinstance(event, base.Delete):
                run.deletes.append(event.source_id)
            elif isinstance(event, base.Skipped):
                run.skip(event.source_id, event.reason, event.title, event.url)
            elif isinstance(event, base.Permissions):
                run.permissions(event.source_id, event.acl)
            elif isinstance(event, base.LimitReached):
                run.limit = event.limit
            elif isinstance(event, base.Cursor):
                run.cursor, run.full = event, event.full or run.full
        if run.cursor is None:
            raise base.ConnectorError("the connector ended without a cursor")
        held = run.apply_deletes(confirm_deletes)
    except Exception as e:
        code = base.error_code(e)
        # An unknown failure is a bug or an outage: an error, with its traceback (Sentry).
        log.log(logging.ERROR if code == "unknown" else logging.WARNING,
                "Sync: %s failed for workspace %s (%s): %s", provider, ws, code, e,
                exc_info=code == "unknown")
        sync_store.finish_run(run_id, "failed", error_code=code, error_detail=str(e)[:300],
                              limit_reached=run.limit, **run.counts)
        sync_store.save_state(ws, provider,
                              consecutive_failures=state["consecutive_failures"] + 1)
        synced, _ = sync_store.counts(ws, provider)
        integrations_store.update(ws, provider, status="error", last_error=code,
                                  doc_count=synced)
        _reindex(ws, run)
        raise
    now = time.time()
    fields = {"consecutive_failures": 0, "pending_deletes": held or None}
    if not held:                      # held deletes: the next run starts from the same place
        fields["cursor"] = run.cursor.value
        fields["last_full_sync_at" if run.full else "last_incremental_at"] = now
    sync_store.save_state(ws, provider, **fields)
    sync_store.finish_run(run_id, "held" if held else "done", held_deletes=held,
                          limit_reached=run.limit, error_code="mass_delete" if held else None,
                          **run.counts)
    synced, _ = sync_store.counts(ws, provider)
    integrations_store.update(ws, provider, status="connected", last_sync_at=now,
                              doc_count=synced, last_error=None)
    _reindex(ws, run)
    # PERMISSION SYNC (kb/services/permissions.py): with the source policy, every
    # file's ACL is worked out again from what the source says now and this
    # workspace's members and groups, and the chunks whose ACL changed are
    # re-tagged (no embedding), so permissions are at most one sync old.
    if integrations_store.policy_of(integrations_store.get(ws, provider)) == "source":
        indexing.schedule_retag(ws, provider)
    return {**run.counts, "held_deletes": held, "limit_reached": run.limit,
            "mode": "full" if run.full else "incremental", "reindexed": len(run.changed)}


def _reindex(ws, run):
    if run.changed:
        indexing.schedule_index(ws, docs=sorted(run.changed))


def start_sync(ws, provider, full=False, confirm_deletes=False):
    """Queue a sync. Asked again while one is queued, the queued one takes the
    stronger options (full, confirm)."""
    jobs.enqueue("sync", ws, {"provider": provider, "full": full,
                              "confirm_deletes": confirm_deletes},
                 lane=f"sync:{ws}:{provider}", dedupe_key=f"sync:{ws}:{provider}")


def _merge(queued, new):
    return {"provider": queued["provider"],
            "full": bool(queued.get("full") or new.get("full")),
            "confirm_deletes": bool(queued.get("confirm_deletes") or new.get("confirm_deletes"))}


@jobs.handler("sync", max_attempts=3, merge=_merge)
def run_sync(job):
    ws, p = job.workspace, job.payload
    provider = p["provider"]
    if provider not in CONNECTORS or not integrations_store.get(ws, provider):
        return {"skipped": "not connected"}         # disconnected while it waited
    try:
        return sync(ws, provider, full=bool(p.get("full")),
                    confirm_deletes=bool(p.get("confirm_deletes")))
    except Exception as e:
        code = base.error_code(e)
        if isinstance(e, base.ConnectorError) and not e.retry:
            return {"error": code}                  # waits for an admin: no retries
        raise jobs.Retry(f"{LABELS.get(provider, provider)} sync failed ({code})")


def disconnect(ws, provider):
    """Revoke what the provider lets us, delete the synced copies, their index
    entries and the connection."""
    c = CONNECTORS[provider]
    try:
        c.disconnect(ws)
    except Exception:
        log.exception("Disconnect: %s couldn't revoke its access for workspace %s", provider, ws)
    sync_store.import_manifests(ws)
    folder = workspaces.connector_dir(ws, provider)
    keys = set()
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            keys.add(f"connectors/{provider}/{name}")
            _remove(folder, name)
    keys |= {f"connectors/{provider}/{r['file_name']}"
             for r in sync_store.items(ws, provider).values() if r.get("file_name")}
    sync_store.forget(ws, provider)
    integrations_store.remove(ws, provider)
    if keys:
        indexing.schedule_index(ws, docs=sorted(keys))


@jobs.periodic("sync_all", lambda: SYNC_EVERY)
def sync_all(job=None):
    """Queue a sync of every connected integration, except ones waiting for an
    admin (reconnect, settings) or with nothing picked yet."""
    queued = 0
    for provider, c in CONNECTORS.items():
        if not c.configured():
            continue
        for row in integrations_store.all_connected(provider):
            if row["status"] not in ("connected", "error"):
                continue
            if row["status"] == "error" and row.get("last_error") in ("auth_expired", "config",
                                                                       "missing_scope"):
                continue
            if c.PICKER_REQUIRED and not picks(row, provider):
                continue
            start_sync(row["workspace"], provider)
            queued += 1
    return {"queued": queued}
