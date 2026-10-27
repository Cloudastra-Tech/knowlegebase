"""
The durable job queue: background work (re-indexing, connector syncs, SAML
metadata refresh) as rows of the `jobs` table in config/app.db, run by a pool
of worker threads.

    enqueue(kind, ...)   add a job, or fold it into the same one already queued
    Runner               N threads that claim jobs and run their handlers
    @handler(kind)       how a kind of job is run (kb/services/ register theirs)
    @periodic(kind)      a job that is queued again every so often

A job is claimed the way members_store.claim_metadata_fetch claims a fetch: in
one BEGIN IMMEDIATE transaction it is marked as this worker's for a LEASE, so no
two threads or processes run the same job. The worker renews the leases of the
jobs it is running; if it dies (a crash, a deploy), the lease runs out and any
worker, this one after its restart included, claims the job again. Handlers are
written to be run again safely.

Every claim follows three rules:
  - one running job per LANE: a workspace's index jobs share one lane, so a
    workspace never has two index runs at once, but workspaces don't wait for
    each other (there is no lock across workspaces);
  - round robin: of the jobs that may start, the one whose workspace started a
    job longest ago goes first, so a busy workspace can't starve the others;
  - a failed job is tried again after an exponential backoff, up to its
    max_attempts; then it stays `failed`, with its error, for the UI.

Where it runs: in the API process by default (the app's lifespan starts it;
JOBS_IN_PROCESS=off turns that off), or in a process of its own,
`python -m kb.worker` (deploy.md, "Background jobs"). JOB_KINDS limits which
kinds a process runs; JOB_WORKERS is the number of threads (default 2).
"""

import collections
import contextlib
import json
import logging
import os
import random
import socket
import threading
import time
import uuid

import db
from kb.settings import ON, env_int

log = logging.getLogger("uvicorn.error")

LEASE = 60                  # seconds a claim lasts unless its worker renews it
POLL = 2                    # seconds an idle worker waits before looking again
BACKOFF = 30                # first retry after about this many seconds ...
BACKOFF_MAX = 30 * 60       # ... doubling each time, up to this
KEEP_DAYS = 7               # finished jobs are deleted after this (the last per lane stays)

Spec = collections.namedtuple("Spec", "fn max_attempts merge")
HANDLERS = {}               # kind -> Spec
PERIODIC = {}               # kind -> function giving the seconds between runs


class Retry(Exception):
    """Raised by a handler that has already logged its failure: try the job again
    later (or give up after max_attempts). The message is kept as the job's error."""


def handler(kind, max_attempts=3, merge=None):
    """Register how a kind of job runs. merge(queued payload, new payload): the
    payload of two such jobs folded into one (see enqueue); default: the queued one's."""
    def register(fn):
        HANDLERS[kind] = Spec(fn, max_attempts, merge)
        return fn
    return register


def periodic(kind, every, max_attempts=1):
    """A job queued again `every()` seconds after its last run finished."""
    def register(fn):
        PERIODIC[kind] = every
        return handler(kind, max_attempts)(fn)
    return register


# ---- The table --------------------------------------------------------------------
@contextlib.contextmanager
def _transaction():
    """A connection inside BEGIN IMMEDIATE: holds the write lock until it commits."""
    conn = db.connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


class Job:
    """A claimed job, as its handler sees it."""

    def __init__(self, row, worker):
        self.id = row["id"]
        self.kind = row["kind"]
        self.workspace = row["workspace"]
        self.lane = row["lane"]
        self.payload = json.loads(row["payload"] or "{}")
        self.attempts = row["attempts"]
        self.max_attempts = row["max_attempts"]
        self.worker = worker

    def progress(self, step, total, message):
        """Shown while it runs (GET /api/index/status reads it for index jobs)."""
        conn = db.connect()
        try:
            with conn:
                conn.execute("UPDATE jobs SET progress = ? WHERE id = ? AND locked_by = ?",
                             (json.dumps({"step": step, "total": total, "message": message}),
                              self.id, self.worker))
        finally:
            conn.close()


def _fold_into_queued(conn, kind, dedupe_key, payload, run_after):
    """Fold `payload` into the job with this dedupe_key that's queued, if there is
    one (it then runs no later than `run_after`). Its id, or None."""
    row = dedupe_key and conn.execute(
        "SELECT id, payload FROM jobs WHERE dedupe_key = ? AND status = 'queued'",
        (dedupe_key,)).fetchone()
    if not row:
        return None
    queued = json.loads(row["payload"] or "{}")
    combine = HANDLERS[kind].merge if kind in HANDLERS else None
    conn.execute("UPDATE jobs SET payload = ?, run_after = MIN(run_after, ?) WHERE id = ?",
                 (json.dumps(combine(queued, payload) if combine else queued), run_after,
                  row["id"]))
    return row["id"]


def enqueue(kind, workspace=None, payload=None, lane=None, dedupe_key=None, delay=0,
            max_attempts=None):
    """Queue a job; returns its id. lane: one job per lane runs at a time (default:
    the kind and workspace). dedupe_key: if a job with this key is queued and not
    yet started, it absorbs this one (its handler's merge() combines the
    payloads), runs no later than this one would, and its attempts start again."""
    now = time.time()
    payload = payload or {}
    lane = lane or f"{kind}:{workspace or ''}"
    if max_attempts is None:
        max_attempts = HANDLERS[kind].max_attempts if kind in HANDLERS else 3
    with _transaction() as conn:
        job_id = _fold_into_queued(conn, kind, dedupe_key, payload, now + delay)
        if job_id:
            conn.execute("UPDATE jobs SET attempts = 0 WHERE id = ?", (job_id,))
        else:
            job_id = conn.execute(
                "INSERT INTO jobs (kind, workspace, lane, dedupe_key, payload, max_attempts, "
                "run_after, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, workspace, lane, dedupe_key, json.dumps(payload), max_attempts,
                 now + delay, now)).lastrowid
    start_in_process()
    _wake_all()
    return job_id


def claim(worker, kinds, lease=LEASE, now=None):
    """The next job of `kinds` this worker may run, marked as its own for `lease`
    seconds (see the top of this file); or None."""
    kinds = list(kinds)
    if not kinds:
        return None
    now = now or time.time()
    marks = ", ".join("?" * len(kinds))
    with _transaction() as conn:
        while True:
            row = conn.execute(f"""
                SELECT j.* FROM jobs j
                 WHERE j.kind IN ({marks})
                   AND ((j.status = 'queued' AND j.run_after <= ?)
                        OR (j.status = 'running' AND j.locked_until < ?))
                   AND NOT EXISTS (SELECT 1 FROM jobs r
                                    WHERE r.lane = j.lane AND r.id != j.id
                                      AND r.status = 'running' AND r.locked_until >= ?)
                 ORDER BY COALESCE((SELECT MAX(s.started_at) FROM jobs s
                                     WHERE s.workspace IS j.workspace), 0),
                          j.run_after, j.id
                 LIMIT 1""", (*kinds, now, now, now)).fetchone()
            if row is None:
                return None
            if row["status"] == "running" and row["attempts"] >= row["max_attempts"]:
                # Its worker stopped mid-run on the last try it had: give up on it.
                conn.execute("UPDATE jobs SET status = 'failed', finished_at = ?, "
                             "locked_by = NULL, locked_until = NULL, "
                             "error = COALESCE(error, 'The worker running it stopped.') "
                             "WHERE id = ?", (now, row["id"]))
                continue
            if row["status"] == "running":
                log.warning("Job %d (%s, workspace %s): its worker %s stopped; running it again",
                            row["id"], row["kind"], row["workspace"], row["locked_by"])
            conn.execute("UPDATE jobs SET status = 'running', locked_by = ?, locked_until = ?, "
                         "attempts = attempts + 1, started_at = ?, progress = NULL WHERE id = ?",
                         (worker, now + lease, now, row["id"]))
            return Job(conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone(),
                       worker)


def _finish(job, **fields):
    """Update a job this worker still holds. False if it doesn't any more (its
    lease ran out and another worker took it over)."""
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = db.connect()
    try:
        with conn:
            return conn.execute(f"UPDATE jobs SET {cols}, locked_by = NULL, locked_until = NULL "
                                "WHERE id = ? AND locked_by = ? AND status = 'running'",
                                (*fields.values(), job.id, job.worker)).rowcount == 1
    finally:
        conn.close()


def complete(job, result=None):
    return _finish(job, status="done", result=json.dumps(result), error=None,
                   finished_at=time.time())


def fail(job, error, backoff=BACKOFF):
    """Try it again after a backoff, or, out of attempts, leave it failed. If the
    same job was queued again while this one ran, that one takes this one's work
    along and this one ends (failed) instead. False if this worker no longer
    holds the job."""
    now = time.time()
    ended = ("UPDATE jobs SET status = 'failed', error = ?, finished_at = ?, locked_by = NULL, "
             "locked_until = NULL WHERE id = ?")
    with _transaction() as conn:
        row = conn.execute("SELECT dedupe_key FROM jobs WHERE id = ? AND locked_by = ? AND "
                           "status = 'running'", (job.id, job.worker)).fetchone()
        if row is None:
            return False
        if job.attempts >= job.max_attempts:
            conn.execute(ended, (error, now, job.id))
            return True
        wait = min(backoff * 2 ** (job.attempts - 1), BACKOFF_MAX) * random.uniform(0.8, 1.2)
        if _fold_into_queued(conn, job.kind, row["dedupe_key"], job.payload, now + wait):
            conn.execute(ended, (error, now, job.id))
        else:
            conn.execute("UPDATE jobs SET status = 'queued', error = ?, run_after = ?, "
                         "locked_by = NULL, locked_until = NULL WHERE id = ?",
                         (error, now + wait, job.id))
    return True


def renew(worker, ids, lease=LEASE):
    if not ids:
        return
    conn = db.connect()
    try:
        with conn:
            conn.execute(f"UPDATE jobs SET locked_until = ? WHERE locked_by = ? AND "
                         f"status = 'running' AND id IN ({', '.join('?' * len(ids))})",
                         (time.time() + lease, worker, *ids))
    finally:
        conn.close()


def get(job_id):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def lane_jobs(lane, kinds):
    """A lane's queued and running jobs of `kinds`, newest first, then the last
    one that finished (if any), as dicts."""
    marks = ", ".join("?" * len(kinds))
    conn = db.connect()
    try:
        rows = conn.execute(f"""
            SELECT * FROM jobs WHERE lane = ? AND kind IN ({marks}) AND (
                status IN ('queued', 'running')
                OR id = (SELECT MAX(id) FROM jobs WHERE lane = ? AND kind IN ({marks})
                          AND status IN ('done', 'failed')))
            ORDER BY status IN ('done', 'failed'), id DESC""",
                            (lane, *kinds, lane, *kinds)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def ensure_periodic(kinds, now=None):
    """Queue each periodic kind among `kinds` that has no job waiting or running,
    to run `every()` seconds after its last run finished (or from now)."""
    now = now or time.time()
    for kind, every in PERIODIC.items():
        if kind not in kinds:
            continue
        with _transaction() as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE kind = ? AND status IN "
                            "('queued', 'running')", (kind,)).fetchone():
                continue
            last = conn.execute("SELECT MAX(finished_at) FROM jobs WHERE kind = ?",
                                (kind,)).fetchone()[0]
            conn.execute("INSERT INTO jobs (kind, lane, dedupe_key, max_attempts, run_after, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (kind, kind, kind, HANDLERS[kind].max_attempts,
                          (last or now) + every(), now))


@periodic("jobs_prune", lambda: 24 * 60 * 60)
def prune(job=None, keep_days=KEEP_DAYS):
    """Delete finished jobs older than keep_days, except each lane's last one
    (the index status shows it)."""
    conn = db.connect()
    try:
        with conn:
            n = conn.execute("""
                DELETE FROM jobs WHERE status IN ('done', 'failed') AND finished_at < ?
                   AND id NOT IN (SELECT MAX(id) FROM jobs WHERE status IN ('done', 'failed')
                                   GROUP BY lane)""",
                             (time.time() - keep_days * 86400,)).rowcount
    finally:
        conn.close()
    return {"deleted": n}


# ---- Workers ----------------------------------------------------------------------
class Runner:
    """`threads` worker threads claiming jobs of the kinds in `handlers`, plus one
    that renews their leases and keeps the periodic jobs queued."""

    def __init__(self, handlers=None, threads=None, lease=LEASE, poll=POLL, backoff=BACKOFF):
        self.handlers = HANDLERS if handlers is None else handlers
        self.threads = env_int("JOB_WORKERS", 2) if threads is None else threads
        self.lease, self.poll, self.backoff = lease, poll, backoff
        self.worker = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._held = set()
        self._held_lock = threading.Lock()
        self._threads = []

    def start(self):
        for n in range(self.threads):
            t = threading.Thread(target=self._loop, name=f"jobs-{n}", daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._keep_alive, name="jobs-lease", daemon=True)
        t.start()
        self._threads.append(t)
        with _runners_lock:
            _runners.add(self)
        return self

    def stop(self, timeout=5):
        """Stop claiming; wait up to `timeout` seconds for running jobs. A job still
        running after that is claimed again once its lease runs out."""
        self._stop.set()
        self._wake.set()
        with _runners_lock:
            _runners.discard(self)
        deadline = time.time() + timeout
        for t in self._threads:
            t.join(max(0, deadline - time.time()))

    def wake(self):
        self._wake.set()

    def run_once(self):
        """Claim one job and run it. False if there was none to run."""
        job = claim(self.worker, self.handlers, self.lease)
        if job is None:
            return False
        with self._held_lock:
            self._held.add(job.id)
        try:
            result = self.handlers[job.kind].fn(job)
        except Retry as e:
            log.warning("Job %d (%s, workspace %s) failed, attempt %d of %d: %s",
                        job.id, job.kind, job.workspace, job.attempts, job.max_attempts, e)
            fail(job, str(e)[:1000], self.backoff)
        except Exception as e:
            log.exception("Job %d (%s, workspace %s) failed, attempt %d of %d",
                          job.id, job.kind, job.workspace, job.attempts, job.max_attempts)
            fail(job, f"{type(e).__name__}: {e}"[:1000], self.backoff)
        else:
            if not complete(job, result):
                log.warning("Job %d (%s) finished after another worker took it over",
                            job.id, job.kind)
        finally:
            with self._held_lock:
                self._held.discard(job.id)
            _wake_all()             # its lane is free: a job waiting on it may start
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                ran = self.run_once()
            except Exception:       # e.g. the database stayed locked: try again soon
                log.exception("Jobs: couldn't claim a job")
                ran = False
            if not ran:
                self._wake.wait(self.poll)
                self._wake.clear()

    def _keep_alive(self):
        next_periodic = 0
        while not self._stop.wait(min(self.lease / 3, 20)):
            try:
                with self._held_lock:
                    held = list(self._held)
                renew(self.worker, held, self.lease)
                if time.time() >= next_periodic:
                    ensure_periodic(self.handlers)
                    next_periodic = time.time() + 60
            except Exception:
                log.exception("Jobs: couldn't renew leases")


_runners = set()            # Runners started in this process (enqueue wakes them)
_runners_lock = threading.Lock()
_runner = None              # this process's own (start_in_process)
_start_lock = threading.Lock()


def _wake_all():
    with _runners_lock:
        for r in _runners:
            r.wake()


def load_handlers():
    """Import the modules that register handlers."""
    from kb.services import (indexing, insights, retention, saml_refresh, scim,  # noqa: F401
                             sync, verified)


def kinds_here():
    """The kinds this process runs: JOB_KINDS (comma-separated), or all of them."""
    load_handlers()
    wanted = [k.strip() for k in os.getenv("JOB_KINDS", "").split(",") if k.strip()]
    unknown = set(wanted) - set(HANDLERS)
    if unknown:
        log.warning("JOB_KINDS: no such job kinds: %s", ", ".join(sorted(unknown)))
    return {k: s for k, s in HANDLERS.items() if not wanted or k in wanted}


def start_in_process(force=False):
    """Start this process's runner, once; unless JOBS_IN_PROCESS is off (then a
    separate `python -m kb.worker` runs the jobs). Returns it, or None."""
    global _runner
    if _runner is not None:
        return _runner
    if not force and os.getenv("JOBS_IN_PROCESS", "on").strip().lower() not in ON:
        return None
    with _start_lock:
        if _runner is None:
            runner = Runner(handlers=kinds_here()).start()
            ensure_periodic(runner.handlers)
            _runner = runner
    return _runner


def stop_in_process(timeout=5):
    global _runner
    runner, _runner = _runner, None
    if runner:
        runner.stop(timeout)
