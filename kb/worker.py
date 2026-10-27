"""
python -m kb.worker  —  run the background jobs (kb/jobs.py) in a process of
their own instead of inside the API.

Give the API JOBS_IN_PROCESS=off so it only queues jobs, and run this beside it
with the same .env, config/ and data (deploy.md, "Background jobs"). JOB_KINDS
picks which kinds of jobs it runs, JOB_WORKERS how many at once. Several
workers may run: jobs are claimed in the database, never run twice at once.
Stop it with Ctrl+C or SIGTERM: it finishes what it's running (up to 30 s);
anything still running is picked up again once its lease runs out.
"""

import logging
import signal
import threading


def main():
    import db
    import observability
    from kb import jobs, logs

    logs.configure()                      # LOG_FORMAT, as the API's (kb/logs.py)
    observability.init_error_tracking()   # job failures to Sentry, tagged with the job
    db.connect().close()                  # migrate first, as the API does
    runner = jobs.start_in_process(force=True)
    log = logging.getLogger("uvicorn.error")
    log.info("Worker %s: running %s with %d threads", runner.worker,
             ", ".join(sorted(runner.handlers)), runner.threads)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    log.info("Worker %s: stopping", runner.worker)
    jobs.stop_in_process(timeout=30)


if __name__ == "__main__":
    main()
