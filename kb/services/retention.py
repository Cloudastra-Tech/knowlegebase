"""
Chat retention (chat.md C12): a nightly job deletes each workspace's chats that
haven't been used for longer than its admin chose (conversations_store
chat_settings; default a year, decision D4). Their messages, feedback, share
links and search rows go with them; answers an admin promoted to the test set
are copies kept elsewhere (decision D2). Each purge that deletes something is in
the workspace's audit log, by "system".
"""

import logging
import time

import audit_store
import conversations_store as store
from kb import jobs

log = logging.getLogger("uvicorn.error")

PURGE_EVERY = 24 * 60 * 60


def purge(now=None):
    """Delete every workspace's chats past its retention. Returns {workspace: count}."""
    now = now or time.time()
    deleted = {}
    for ws in store.workspaces_with_chats():
        days = store.chat_settings(ws)["retention_days"]
        if not days:
            continue
        n = store.purge(ws, now - days * 86400)
        if n:
            deleted[ws] = n
            audit_store.record(ws, "chat.retention_purge", actor="system", actor_type="system",
                               target_type="workspace", target_id=ws,
                               details={"deleted": n, "retention_days": days})
            log.info("Retention: deleted %d chat(s) in workspace %s (kept %d days)", n, ws, days)
    return deleted


@jobs.periodic("chat_retention", lambda: PURGE_EVERY)
def purge_job(job=None):
    return {"deleted": purge()}
