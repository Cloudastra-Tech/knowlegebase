"""Background re-indexing, one workspace at a time (status and gate: kb/state.py).

Every document change schedules a re-index of THAT workspace in a background
thread; the UI polls GET /api/index/status. Changes made while an index runs
set `pending`, so one more pass runs right after (they are coalesced).
"""

import contextlib
import io
import logging
import threading
import time

import ingest
import integrations_store
import workspaces
from kb import state

log = logging.getLogger("uvicorn.error")


def run_index(ws):
    """Background worker: re-index one workspace until no more changes are pending."""
    def progress(step, total, message):
        with state.index_lock:
            state.indexes[ws].update(step=step, total=total, message=message)

    gate = state.gate
    while True:
        with state.index_lock:
            state.indexes[ws].update(status="indexing", step=0, message="Starting",
                                     pending=False, error=None)
        with gate:
            state.gate_of(ws)["indexing"] = True
            gate.wait_for(lambda: state.gate_of(ws)["active"] == 0, timeout=300)
        summary, error = None, None
        try:
            with state.ingest_lock, contextlib.redirect_stdout(io.StringIO()):
                summary = ingest.main(progress=progress,
                                      docs_folder=workspaces.docs_root(ws),
                                      collection=workspaces.collection(ws),
                                      connector_access=integrations_store
                                      .chunk_access_levels(ws))
            with state.agents_lock:
                for key in [k for k in state.agents if k[0] == ws]:
                    del state.agents[key]   # next question loads the new index
        except Exception as e:        # keep the server alive; show it in the UI
            log.exception("Index: re-indexing workspace %s failed", ws)
            error = f"{type(e).__name__}: {e}"
        finally:
            with gate:
                state.gate_of(ws)["indexing"] = False
                gate.notify_all()
        with state.index_lock:
            if state.indexes[ws]["pending"]:
                continue               # more changes arrived meanwhile
            state.indexes[ws].update(status="error" if error else "idle", summary=summary,
                                     error=error, finished_at=time.time(),
                                     message=error or "Up to date")
            return


def schedule_index(ws):
    with state.index_lock:
        status = state.indexes.setdefault(ws, state.new_index_state())
        if status["status"] == "indexing":
            status["pending"] = True
            return
        status.update(status="indexing", step=0, message="Queued")
    threading.Thread(target=run_index, args=(ws,), daemon=True).start()
