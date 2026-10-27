"""
The job queue (kb/jobs.py): claims with a lease, one running job per lane,
round robin across workspaces, retries with backoff, jobs folded together, a
job whose worker died claimed again, and periodic jobs kept queued.

Each test uses job kinds of its own ("t_..."), run by its own Runner with no
threads (run_once()), so the app's runner never picks them up.
"""

import time
import uuid

import pytest

from kb import jobs


def kind():
    return f"t_{uuid.uuid4().hex[:8]}"


def ws():
    return f"w-{uuid.uuid4().hex[:8]}"


def _set(job_id, **fields):
    conn = jobs.db.connect()
    with conn:
        conn.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     (*fields.values(), job_id))
    conn.close()


def runner(handlers, **kwargs):
    return jobs.Runner(handlers={k: jobs.Spec(fn, n, None) for k, (fn, n) in handlers.items()},
                       threads=0, **kwargs)


def test_a_job_runs_once_and_keeps_its_result():
    k, seen = kind(), []
    job_id = jobs.enqueue(k, ws(), {"n": 1})
    r = runner({k: (lambda job: seen.append(job.payload) or {"ok": job.payload["n"]}, 3)})
    assert r.run_once() and not r.run_once()
    row = jobs.get(job_id)
    assert seen == [{"n": 1}]
    assert (row["status"], row["attempts"], row["result"]) == ("done", 1, '{"ok": 1}')
    assert row["locked_by"] is None and row["finished_at"] >= row["started_at"]


def test_one_job_per_lane_runs_at_a_time_and_lanes_run_side_by_side():
    k, w = kind(), ws()
    first = jobs.enqueue(k, w, lane=f"lane-a-{w}")
    second = jobs.enqueue(k, w, lane=f"lane-a-{w}")
    other = jobs.enqueue(k, w, lane=f"lane-b-{w}")
    got = jobs.claim("worker-1", [k])
    assert got.id == first
    # Its lane is busy: the next claim skips `second` for the other lane.
    assert jobs.claim("worker-2", [k]).id == other
    assert jobs.claim("worker-3", [k]) is None
    jobs.complete(got)
    assert jobs.claim("worker-3", [k]).id == second


def test_workspaces_take_turns():
    k, busy, quiet = kind(), ws(), ws()
    busy_jobs = [jobs.enqueue(k, busy, lane=f"{busy}-{n}") for n in range(3)]
    quiet_job = jobs.enqueue(k, quiet)
    order = []
    while (job := jobs.claim("w", [k])) is not None:
        order.append(job.id)
        jobs.complete(job)
        time.sleep(0.01)                  # started_at decides the turn
    # The quiet workspace doesn't wait behind every job of the busy one.
    assert order == [busy_jobs[0], quiet_job, busy_jobs[1], busy_jobs[2]]


def test_a_failing_job_is_retried_after_a_backoff_then_left_failed():
    k = kind()
    job_id = jobs.enqueue(k, ws(), max_attempts=2)

    def broken(job):
        raise ValueError("nope")

    r = runner({k: (broken, 2)}, backoff=60)
    assert r.run_once()
    row = jobs.get(job_id)
    assert (row["status"], row["attempts"], row["error"]) == ("queued", 1, "ValueError: nope")
    assert 60 * 0.8 - 1 <= row["run_after"] - time.time() <= 60 * 1.2
    assert not r.run_once()               # not before its backoff
    _set(job_id, run_after=0)
    assert r.run_once()
    row = jobs.get(job_id)
    assert (row["status"], row["attempts"], row["error"]) == ("failed", 2, "ValueError: nope")
    assert not r.run_once()


def test_a_handler_that_logged_its_failure_says_why():
    k = kind()
    job_id = jobs.enqueue(k, ws(), max_attempts=1)

    def broken(job):
        raise jobs.Retry("the source said no")

    runner({k: (broken, 1)}).run_once()
    assert jobs.get(job_id)["error"] == "the source said no"


def test_a_job_queued_while_the_same_one_waits_is_folded_into_it(monkeypatch):
    k, w = kind(), ws()
    monkeypatch.setitem(jobs.HANDLERS, k, jobs.Spec(
        None, 3, lambda queued, new: {"docs": queued["docs"] + new["docs"]}))
    a = jobs.enqueue(k, w, {"docs": ["a"]}, dedupe_key=f"{k}:{w}")
    b = jobs.enqueue(k, w, {"docs": ["b"]}, dedupe_key=f"{k}:{w}")
    assert a == b
    assert jobs.get(a)["payload"] == '{"docs": ["a", "b"]}'
    # Once it runs, the same request makes a new job (to run after it).
    running = jobs.claim("w", [k])
    c = jobs.enqueue(k, w, {"docs": ["c"]}, dedupe_key=f"{k}:{w}")
    assert c != a
    # It fails meanwhile: the queued one takes its work along instead of a retry.
    assert jobs.fail(running, "boom")
    assert jobs.get(a)["status"] == "failed"
    assert jobs.get(c)["payload"] == '{"docs": ["c", "a", "b"]}'


def test_a_job_whose_worker_died_is_claimed_again_after_its_lease():
    k = kind()
    job_id = jobs.enqueue(k, ws(), max_attempts=2)
    dead = jobs.claim("dead-worker", [k], lease=0.3)     # ...and it never finishes
    assert dead.id == job_id
    ran = []
    r = runner({k: (lambda job: ran.append(job.attempts), 2)})
    assert not r.run_once()               # its lease still holds
    time.sleep(0.4)
    assert r.run_once()
    assert ran == [2]
    assert jobs.get(job_id)["status"] == "done"
    # The dead worker can't overwrite the result.
    assert not jobs.complete(dead, {"late": True})
    assert jobs.get(job_id)["result"] == "null"


def test_a_job_whose_worker_died_on_its_last_try_is_left_failed():
    k = kind()
    job_id = jobs.enqueue(k, ws(), max_attempts=1)
    jobs.claim("dead-worker", [k], lease=0.1)
    time.sleep(0.2)
    assert jobs.claim("w", [k]) is None
    row = jobs.get(job_id)
    assert (row["status"], row["error"]) == ("failed", "The worker running it stopped.")


def test_a_worker_renews_the_leases_of_jobs_it_runs():
    k = kind()
    jobs.enqueue(k, ws())
    r = jobs.Runner(handlers={k: jobs.Spec(lambda job: time.sleep(1.2), 1, None)}, threads=1,
                    lease=0.6, poll=0.05).start()
    try:
        time.sleep(0.9)                    # past the first lease: renewed, not reclaimable
        assert jobs.claim("thief", [k]) is None
    finally:
        r.stop()


def test_periodic_jobs_stay_queued_once(monkeypatch):
    k = kind()
    monkeypatch.setitem(jobs.HANDLERS, k, jobs.Spec(lambda job: None, 1, None))
    monkeypatch.setitem(jobs.PERIODIC, k, lambda: 600)
    jobs.ensure_periodic([k])
    jobs.ensure_periodic([k])                     # e.g. two workers: still one
    conn = jobs.db.connect()
    rows = conn.execute("SELECT * FROM jobs WHERE kind = ?", (k,)).fetchall()
    conn.close()
    assert len(rows) == 1 and rows[0]["status"] == "queued"
    assert rows[0]["run_after"] == pytest.approx(time.time() + 600, abs=5)


def test_old_finished_jobs_are_pruned_but_each_lanes_last_stays():
    k, w = kind(), ws()
    ids = [jobs.enqueue(k, w) for _ in range(3)]
    for _ in ids:
        jobs.complete(jobs.claim("w", [k]))
    for i in ids:
        _set(i, finished_at=0)
    jobs.prune()
    assert [jobs.get(i) is not None for i in ids] == [False, False, True]


def test_an_answer_someone_waits_for_never_waits_behind_heavy_jobs():
    """Slack and Teams answers (interactive jobs): first in line, and with a worker
    of their own, so a re-index on every other worker can't hold them up."""
    import threading
    heavy, answer, w = kind(), kind(), ws()
    started, release, answered = threading.Event(), threading.Event(), threading.Event()

    def index(job):
        started.set()
        release.wait(10)
    handlers = {heavy: jobs.Spec(index, 1, None),
                answer: jobs.Spec(lambda job: answered.set(), 1, None, True)}

    # First in line: queued after the index run, still run before it.
    first = jobs.enqueue(heavy, w)
    reply = jobs.enqueue(answer, w)
    r = jobs.Runner(handlers=handlers, threads=0, interactive_threads=0)
    assert r.run_once()
    assert jobs.get(reply)["status"] == "done" and jobs.get(first)["status"] == "queued"

    # A worker of their own: the only other one is busy indexing.
    answered.clear()
    r = jobs.Runner(handlers=handlers, threads=1, interactive_threads=1, poll=0.05).start()
    try:
        assert started.wait(5)
        jobs.enqueue(answer, w)
        assert answered.wait(5)
        assert jobs.get(first)["status"] == "running"
    finally:
        release.set()
        r.stop()
    jobs.load_handlers()
    assert jobs.HANDLERS["slack"].interactive and jobs.HANDLERS["teams"].interactive
    assert not jobs.HANDLERS["index"].interactive and not jobs.HANDLERS["sync"].interactive
