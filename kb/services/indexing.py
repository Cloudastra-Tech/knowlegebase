"""Re-indexing, as background jobs (kb/jobs.py) in one lane per workspace: a
workspace never has two runs at once, and workspaces don't wait for each other.

    schedule_index(ws, docs)     documents changed: queue a run. Changes that
                                 arrive while one runs are folded into ONE more
                                 run after it (the job queue's dedupe_key).
    schedule_retag(ws, provider) a connection's access level changed
    index_status(ws)             what GET /api/index/status shows
    current(ws)                  the collection questions are answered from

A run is INCREMENTAL (ingest.update): only new, edited and deleted documents
touch the index, and questions are answered throughout. It is a FULL REBUILD
instead when asked for (POST /api/reindex?full=true), when the index was built
with other chunking or embedding settings (ingest.FINGERPRINT), or when the
workspace has no row in `search_indexes` yet (an index from before stable chunk
ids). A full rebuild goes into a NEW collection while questions keep using the
current one; then the workspace's row is pointed at the new one, and the old
one is dropped DROP_AFTER seconds later, once questions that started on it are
done. A rebuild that fails drops what it built and leaves the old one serving.

Agents notice a change by the row's `version` (services/agents.py), so this
works the same when the jobs run in another process (kb/worker.py).
"""

import json
import logging
import os
import time

import db
import ingest
import integrations_store
import sync_store
import workspaces
from kb import jobs
from kb.services import assistant, webhooks

log = logging.getLogger("uvicorn.error")

KINDS = ("index", "retag")         # what the index status follows
DROP_AFTER = 10 * 60               # seconds an old collection stays after a rebuild
SUMMARY = ("documents", "chunks", "restricted", "embedded", "added", "changed", "retagged",
           "removed", "failed", "blocked")   # counts only: the status is shown to every member


def new_index_state():
    return {"status": "idle", "step": 0, "total": 4, "message": "", "pending": False,
            "summary": None, "error": None, "finished_at": None}


def lane(ws):
    return f"index:{ws}"


# ---- Which collection answers a workspace's questions -----------------------------
def _row(ws):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM search_indexes WHERE workspace = ?", (ws,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def current(ws):
    """(collection, version): where the workspace's questions are answered from,
    and a number that changes whenever what's in it does."""
    row = _row(ws)
    return (row["collection"], row["version"]) if row else (workspaces.collection(ws), 0)


def _point(ws, collection, generation):
    """Answer the workspace's questions from `collection` from now on."""
    conn = db.connect()
    try:
        with conn:
            conn.execute("""INSERT INTO search_indexes
                    (workspace, collection, fingerprint, generation, version, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(workspace) DO UPDATE SET collection = excluded.collection,
                    fingerprint = excluded.fingerprint, generation = excluded.generation,
                    version = version + 1, updated_at = excluded.updated_at""",
                         (ws, collection, ingest.FINGERPRINT, generation, time.time()))
    finally:
        conn.close()


def _changed(ws):
    """What's in the workspace's collection changed: agents load it again."""
    conn = db.connect()
    try:
        with conn:
            conn.execute("UPDATE search_indexes SET version = version + 1, updated_at = ? "
                         "WHERE workspace = ?", (time.time(), ws))
    finally:
        conn.close()


# ---- Queueing ---------------------------------------------------------------------
def _merge(queued, new):
    """Two index runs folded into one: full if either was; every document either
    named (None = all of them)."""
    docs = (None if queued.get("docs") is None or new.get("docs") is None
            else sorted(set(queued["docs"]) | set(new["docs"])))
    return {"full": bool(queued.get("full") or new.get("full")), "docs": docs}


def schedule_index(ws, docs=None, full=False):
    """Queue a re-index of the workspace. docs: the keys of the documents that
    changed (ingest.doc_files), if the caller knows them; None checks them all.
    full: rebuild everything into a new collection."""
    jobs.enqueue("index", ws, {"full": full, "docs": docs}, lane=lane(ws),
                 dedupe_key=f"index:{ws}")


def schedule_stale():
    """Queue a run for every workspace whose index was built with other chunking or
    embedding settings (ingest.FINGERPRINT): index() rebuilds it into a new
    collection while the old one keeps answering, then switches. Called at start,
    so a release that changes how documents are chunked re-indexes on its own.
    REINDEX_ON_SETTINGS_CHANGE=off leaves them until their next change. Returns
    the workspaces queued."""
    if (os.getenv("REINDEX_ON_SETTINGS_CHANGE") or "on").strip().lower() in ("0", "off",
                                                                            "false", "no"):
        return []
    conn = db.connect()
    try:
        rows = conn.execute("SELECT workspace, fingerprint FROM search_indexes").fetchall()
    finally:
        conn.close()
    stale = [r["workspace"] for r in rows if r["fingerprint"] != ingest.FINGERPRINT]
    for ws in stale:
        schedule_index(ws)
    if stale:
        log.info("Index: %d workspace(s) were indexed with other settings; rebuilding them",
                 len(stale))
    return stale


def schedule_retag(ws, provider):
    """A connection's access level changed: re-tag its chunks (no embedding)."""
    jobs.enqueue("retag", ws, {"provider": provider}, lane=lane(ws),
                 dedupe_key=f"retag:{ws}:{provider}")


# ---- The jobs ---------------------------------------------------------------------
@jobs.handler("index", max_attempts=3, merge=_merge)
def run_index(job):
    try:
        result = index(job.workspace, full=job.payload.get("full"),
                       docs=job.payload.get("docs"), progress=job.progress)
    except Exception as e:        # kept as the job's error: the UI shows it
        log.exception("Index: re-indexing workspace %s failed", job.workspace)
        raise jobs.Retry(f"{type(e).__name__}: {e}")
    _tell_webhooks(job.workspace, job.payload.get("docs"), result)
    return result


def _tell_webhooks(ws, docs, result):
    """document.indexed (kb/services/webhooks.py): which documents, and counts only."""
    counts = {k: result[k] for k in ("added", "changed", "removed", "embedded", "chunks")
              if isinstance(result.get(k), int)}
    if docs is not None and not docs:
        return
    webhooks.emit(ws, "document.indexed", {"mode": result.get("mode"),
                                           "documents": None if docs is None else docs[:200],
                                           "counts": counts})


def index(ws, full=False, docs=None, progress=None):
    """Bring the workspace's index up to date: incrementally, or by a full
    rebuild (see the top of this file). Returns the run's summary."""
    row = _row(ws)
    access = integrations_store.chunk_access_levels(ws)
    info = sync_store.doc_info(ws)        # synced files' titles and links
    if full or not row or row["fingerprint"] != ingest.FINGERPRINT:
        return _rebuild(ws, row, access, progress, info)
    result = ingest.update(progress, docs_folder=workspaces.docs_root(ws),
                           collection=row["collection"], connector_access=access,
                           workspace=ws, only=docs, doc_info=info,
                           skip=assistant.blocker(ws))
    if any(result[k] for k in ("embedded", "deleted_chunks", "retagged", "changed")):
        _changed(ws)
    return {**result, "mode": "incremental"}


def _rebuild(ws, row, access, progress, info):
    generation = (row["generation"] if row else 0) + 1
    new = f"{workspaces.collection(ws)}__g{generation}"
    old = row["collection"] if row else workspaces.collection(ws)
    try:
        result = ingest.main(progress, docs_folder=workspaces.docs_root(ws), collection=new,
                             connector_access=access, workspace=ws, doc_info=info,
                             skip=assistant.blocker(ws))
        stored = ingest.summary(new)["chunks"]
        if stored != result["chunks"]:
            raise RuntimeError(f"the new index holds {stored} chunks, not {result['chunks']}")
    except BaseException:
        try:
            ingest.drop(new)          # the old one keeps answering
        except Exception:
            log.exception("Index: couldn't drop the unfinished collection %s", new)
        raise
    _point(ws, new, generation)
    if old != new:
        jobs.enqueue("drop_collection", ws, {"collection": old}, lane=lane(ws),
                     delay=DROP_AFTER)
    return {**result, "mode": "full", "collection": new}


def rebuild_now(ws, progress=None):
    """A full rebuild, run here rather than as a job (`python ingest.py`)."""
    return index(ws, full=True, progress=progress)


@jobs.handler("retag", max_attempts=3)
def run_retag(job):
    ws, provider = job.workspace, job.payload["provider"]
    row = _row(ws)
    if not row or row["fingerprint"] != ingest.FINGERPRINT:
        return run_index(job)         # no index to re-tag: build one (with the new level)
    # The level as it is NOW: two quick changes need one run, and the last one wins.
    level = integrations_store.chunk_access_levels(ws).get(provider, "restricted")
    job.progress(2, 4, "Updating who can see synced files")
    try:
        n = ingest.retag(row["collection"], provider, level)
    except Exception as e:
        log.exception("Index: re-tagging %s in workspace %s failed", provider, ws)
        raise jobs.Retry(f"{type(e).__name__}: {e}")
    if n:
        _changed(ws)
    return {**ingest.summary(row["collection"]), "retagged_chunks": n, "mode": "retag"}


@jobs.handler("drop_collection", max_attempts=3)
def drop_collection(job):
    """Drop a workspace's collection that questions no longer use."""
    name = job.payload["collection"]
    base = workspaces.collection(job.workspace)
    if name == current(job.workspace)[0] or not (name == base or name.startswith(base + "__g")):
        return {"dropped": False}
    ingest.drop(name)
    return {"dropped": True}


# ---- Status -----------------------------------------------------------------------
def index_status(ws):
    """The shape the UI polls (frontend/src/lib/api.ts, IndexStatus), from the jobs table."""
    status = new_index_state()
    rows = jobs.lane_jobs(lane(ws), KINDS)
    running = [r for r in rows if r["status"] == "running"]
    queued = [r for r in rows if r["status"] == "queued"]
    finished = [r for r in rows if r["status"] in ("done", "failed")]
    if running:
        p = json.loads(running[0]["progress"] or "null") or {"step": 0, "total": 4,
                                                            "message": "Starting"}
        status.update(status="indexing", step=p["step"], total=p["total"],
                      message=p["message"], pending=bool(queued))
    elif queued:
        retry = queued[-1]["error"]
        status.update(status="indexing", message=f"Trying again soon ({retry})" if retry
                      else "Queued")
    elif finished:
        last = finished[0]
        if last["status"] == "done":
            result = json.loads(last["result"] or "null") or {}
            status.update(message="Up to date", finished_at=last["finished_at"],
                          summary={k: result[k] for k in SUMMARY if k in result} or None)
        else:
            status.update(status="error", error=last["error"], message=last["error"],
                          finished_at=last["finished_at"])
    return status
