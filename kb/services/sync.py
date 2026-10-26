"""Connector sync, as background jobs (kb/jobs.py): on request, and every
DRIVE_SYNC_EVERY seconds for every connected integration (the periodic
`sync_all` job). One lane per (workspace, provider): a connection never has two
syncs at once, and a sync asked for while one is queued is the same sync.

Every connector module has the same shape: PROVIDER, configured(), auth_url(),
finish_connect(), sync(), disconnect().
"""

import logging
import time

import integrations_store
from connectors import google_drive, notion
from kb import jobs
from kb.services import indexing

log = logging.getLogger("uvicorn.error")

DRIVE_SYNC_EVERY = 30 * 60        # seconds between automatic connector syncs
CONNECTORS = {c.PROVIDER: c for c in (google_drive, notion)}
LABELS = {"google_drive": "Google Drive", "notion": "Notion"}
SETUP_ENV = {"google_drive": "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET",
             "notion": "NOTION_CLIENT_ID / NOTION_CLIENT_SECRET"}


def ready_to_sync(ws, provider):
    """Drive syncs only what the admin picked; Notion's picking happens on its own
    consent screen, so anything shared with us is wanted."""
    if provider != google_drive.PROVIDER:
        return True
    row = integrations_store.get(ws, provider) or {}
    return bool((row.get("settings") or {}).get("folders"))


def sync(ws, provider):
    """Sync one workspace's connector; re-index if anything changed. False if it
    failed (logged, and shown on the connection)."""
    try:
        integrations_store.update(ws, provider, status="syncing", last_error=None)
        count, changed = CONNECTORS[provider].sync(ws)
        integrations_store.update(ws, provider, status="connected",
                                  last_sync_at=time.time(), doc_count=count)
        if changed:
            indexing.schedule_index(ws)
        return True
    except Exception as e:
        log.exception("Sync: %s failed for workspace %s", provider, ws)
        integrations_store.update(ws, provider, status="error",
                                  last_error=f"{type(e).__name__}: {e}"[:500])
        return False


def start_sync(ws, provider):
    jobs.enqueue("sync", ws, {"provider": provider}, lane=f"sync:{ws}:{provider}",
                 dedupe_key=f"sync:{ws}:{provider}")


@jobs.handler("sync", max_attempts=3)
def run_sync(job):
    ws, provider = job.workspace, job.payload["provider"]
    if provider not in CONNECTORS or not integrations_store.get(ws, provider):
        return {"skipped": "not connected"}         # disconnected while it waited
    if not sync(ws, provider):
        raise jobs.Retry(f"{LABELS.get(provider, provider)} sync failed")
    return {"ok": True}


@jobs.periodic("sync_all", lambda: DRIVE_SYNC_EVERY)
def sync_all(job=None):
    """Queue a sync of every connected integration."""
    queued = 0
    for provider, c in CONNECTORS.items():
        if not c.configured():
            continue
        for row in integrations_store.all_connected(provider):
            if row["status"] in ("connected", "error"):
                start_sync(row["workspace"], provider)
                queued += 1
    return {"queued": queued}
