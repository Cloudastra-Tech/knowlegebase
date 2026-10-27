"""
Chat retention (chat.md C12): a nightly job deletes each workspace's chats that
haven't been used for longer than its admin chose (conversations_store
chat_settings; default a year, decision D4). Their messages, feedback, share
links and search rows go with them; answers an admin promoted to the test set
are copies kept elsewhere (decision D2). Each purge that deletes something is in
the workspace's audit log, by "system". Share links that were revoked or expired
long enough ago are deleted by another daily job (prune_shares).
"""

import logging
import time

import audit_store
import conversations_store as store
from kb import jobs
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

PURGE_EVERY = 24 * 60 * 60
SHARE_KEEP_DAYS = 30


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


def prune_shares(now=None):
    """Delete share links that were revoked or expired over SHARE_KEEP_DAYS ago
    (default 30: until then, opening one says it was revoked or has expired,
    rather than that it never existed). Returns {workspace: count}."""
    now = now or time.time()
    return store.prune_shares(now - env_int("SHARE_KEEP_DAYS", SHARE_KEEP_DAYS) * 86400)


@jobs.periodic("share_prune", lambda: PURGE_EVERY)
def prune_shares_job(job=None):
    gone = prune_shares()
    if gone:
        log.info("Shares: deleted %d dead link(s)", sum(gone.values()))
    return {"deleted": gone}
