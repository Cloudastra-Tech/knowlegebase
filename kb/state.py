"""
Everything the API keeps in memory between requests, in one place.

It lives in this process only: the Dockerfile runs ONE uvicorn worker because
of it, and a restart forgets it (sessions are in SQLite: sessions_store.py).
Moving it to the database or Redis (engineering plan P2-1) starts here. The
code that uses each item reads it as `state.<name>` at call time, so tests can
replace any of them.
"""

import threading

# ---- Rate limits (kb/rate_limit.py) -----------------------------------------------
hits = {}                 # (bucket, address | user | workspace) -> recent times
hits_lock = threading.Lock()
failed_logins = {}        # username -> (count, locked_until)   (kb/routers/auth.py)
# Failed SSO sign-ins logged for the admin, per minute (services/sign_in.py);
# guarded by hits_lock.
failures_logged = {}      # connection id -> recent times

# ---- Agents (kb/services/agents.py) -----------------------------------------------
agents = {}               # (workspace, role) -> agent run() function
agents_lock = threading.Lock()

# ---- Background re-indexing, per workspace (kb/services/indexing.py) ----------------
# Every document change schedules a re-index of THAT workspace in a background
# thread; the UI polls GET /api/index/status. Changes made while an index runs
# set `pending`, so one more pass runs right after (they are coalesced).
indexes = {}              # workspace -> status dict
index_lock = threading.Lock()
ingest_lock = threading.Lock()   # one rebuild at a time (shared Chroma store)
# Chat questions wait while their workspace's index is swapped, and the swap
# waits for questions already running — so nobody searches a half-built index.
gate = threading.Condition()
gate_state = {}           # workspace -> {"indexing": bool, "active": int}

# ---- Connectors (kb/routers/integrations.py, kb/services/sync.py) --------------------
oauth_states = {}         # state -> {"workspace", "user", "expires", "redirect_uri"}
syncing = set()           # (workspace, provider) syncs that are running
sync_lock = threading.Lock()

# ---- Sign-in (kb/services/sign_in.py, kb/routers/saml.py) ---------------------------
sso_tests = {}            # "Test sign-in" ticket -> {"workspace", "cid", "expires", "result"}
sso_tests_lock = threading.Lock()
saml_parked = {}          # key -> {"cid", "request_id", "email", "name", "sent", "expires"}
saml_lock = threading.Lock()
sp_metadata_cache = {}    # (connection id, updated_at, base URL) -> XML


def new_index_state():
    return {"status": "idle", "step": 0, "total": 4, "message": "", "pending": False,
            "summary": None, "error": None, "finished_at": None}


def gate_of(ws):
    return gate_state.setdefault(ws, {"indexing": False, "active": 0})
