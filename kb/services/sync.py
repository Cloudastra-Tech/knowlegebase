"""Connector sync: in a background thread on request, and every DRIVE_SYNC_EVERY
seconds for every connected integration (the scheduler the lifespan starts).

Every connector module has the same shape: PROVIDER, configured(), auth_url(),
finish_connect(), sync(), disconnect().
"""

import logging
import threading
import time

import integrations_store
from connectors import google_drive, notion
from kb import state
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
    """Sync one workspace's connector (background); re-index if anything changed."""
    key = (ws, provider)
    with state.sync_lock:
        if key in state.syncing:
            return
        state.syncing.add(key)
    try:
        integrations_store.update(ws, provider, status="syncing", last_error=None)
        count, changed = CONNECTORS[provider].sync(ws)
        integrations_store.update(ws, provider, status="connected",
                                  last_sync_at=time.time(), doc_count=count)
        if changed:
            indexing.schedule_index(ws)
    except Exception as e:
        log.exception("Sync: %s failed for workspace %s", provider, ws)
        integrations_store.update(ws, provider, status="error",
                                  last_error=f"{type(e).__name__}: {e}"[:500])
    finally:
        with state.sync_lock:
            state.syncing.discard(key)


def start_sync(ws, provider):
    threading.Thread(target=sync, args=(ws, provider), daemon=True).start()


def sync_scheduler():
    """Re-sync every connected integration every DRIVE_SYNC_EVERY seconds."""
    while True:
        time.sleep(DRIVE_SYNC_EVERY)
        for provider, c in CONNECTORS.items():
            if not c.configured():
                continue
            for row in integrations_store.all_connected(provider):
                if row["status"] in ("connected", "error"):
                    start_sync(row["workspace"], provider)
