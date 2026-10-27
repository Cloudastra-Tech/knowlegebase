"""
The jobs that move secrets between keys (secret_box.py, platform.md PL-05). All
in one lane, "secrets", so two never run at once.

    secrets_reseal     every stored secret under its workspace's data key. Queued
                       at start while any is still in the old format (sealed by
                       the server key itself); resumable, and each secret stays
                       readable at every step
    secrets_rotate     a workspace's new data key version, its secrets re-sealed
                       under it (payload {"workspace"})
    secrets_rewrap     after a new server key (INTEGRATIONS_KEY="<new>,<old>"):
                       every data key wrapped under the new one
    secrets_rotate_due daily: rotate each workspace whose data key is older than
                       KEY_ROTATION_DAYS (default 365; 0: never)
"""

import logging
import time

import db
import secret_box
from kb import jobs
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")
LANE = "secrets"


def schedule_reseal():
    """Queue a re-seal if any secret is still in the old format. Returns the job id or None."""
    if not secret_box.old_format_count():
        return None
    return jobs.enqueue("secrets_reseal", lane=LANE, dedupe_key="secrets_reseal")


def schedule_rotate(workspace):
    return jobs.enqueue("secrets_rotate", payload={"workspace": workspace}, lane=LANE,
                        dedupe_key=f"secrets_rotate:{workspace}")


def schedule_rewrap():
    return jobs.enqueue("secrets_rewrap", lane=LANE, dedupe_key="secrets_rewrap")


@jobs.handler("secrets_reseal", max_attempts=5)
def run_reseal(job):
    result = secret_box.reseal(progress=lambda done: None)
    if result["failed"]:
        raise jobs.Retry(f"{result['failed']} secret(s) couldn't be re-sealed")
    return result


@jobs.handler("secrets_rotate", max_attempts=3)
def run_rotate(job):
    return secret_box.rotate(job.payload["workspace"])


@jobs.handler("secrets_rewrap", max_attempts=3)
def run_rewrap(job):
    return secret_box.rewrap()


def rotation_days():
    return env_int("KEY_ROTATION_DAYS", 365, minimum=0)


@jobs.periodic("secrets_rotate_due", lambda: 24 * 60 * 60)
def rotate_due(job=None, now=None):
    days = rotation_days()
    if not days:
        return {"rotated": 0}
    cutoff = (now or time.time()) - days * 86400
    conn = db.connect()
    try:
        due = [r[0] for r in conn.execute(
            "SELECT workspace FROM workspace_keys GROUP BY workspace HAVING MAX(created_at) < ?",
            (cutoff,))]
    finally:
        conn.close()
    for ws in due:
        schedule_rotate(ws)
    return {"rotated": len(due)}
