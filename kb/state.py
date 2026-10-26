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
agents = {}               # (workspace, role) -> (index it was built on, agent run() function)
agents_lock = threading.Lock()

# Re-indexing and connector syncs are jobs in the database (kb/jobs.py), not here.

# ---- Connectors (kb/routers/integrations.py) ----------------------------------------
oauth_states = {}         # state -> {"workspace", "user", "expires", "redirect_uri"}

# ---- Sign-in (kb/services/sign_in.py, kb/routers/saml.py) ---------------------------
sso_tests = {}            # "Test sign-in" ticket -> {"workspace", "cid", "expires", "result"}
sso_tests_lock = threading.Lock()
saml_parked = {}          # key -> {"cid", "request_id", "email", "name", "sent", "expires"}
saml_lock = threading.Lock()
sp_metadata_cache = {}    # (connection id, updated_at, base URL) -> XML

