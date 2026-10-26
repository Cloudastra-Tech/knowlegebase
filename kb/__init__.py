"""
kb  —  the JSON API behind the React frontend (frontend/), and the site itself.

`uvicorn api:app` runs it: api.py only builds the app (kb/app.py, create_app()).
The sign-in, document, search and connector logic it calls stays in the modules
at the project root (auth.py, sso.py, saml_sp.py, members_store.py, docs_store.py,
integrations_store.py, agent.py, ingest.py, connectors/, ...).

    app.py         create_app(): middleware, lifespan, routers, the site
    settings.py    numbers from the environment; whether the API docs are on
    state.py       EVERYTHING kept in memory between requests (one worker only)
    deps.py        who is asking: current_user, current_role, require_hr / _admin
    rate_limit.py  per-address and per-person limits
    middleware.py  security headers, request body limit
    site.py        the built React app (frontend/dist)
    jobs.py        the background job queue (a table in config/app.db) and its workers
    worker.py      `python -m kb.worker`: the jobs in a process of their own
    routers/       the endpoints, one module per area (table in routers/__init__.py)
    services/      the work behind them: agents, re-indexing, connector sync,
                   SAML metadata refresh, shared sign-in steps, sign-up
"""

from dotenv import load_dotenv

load_dotenv()      # before anything imports the stores: they read their settings once
