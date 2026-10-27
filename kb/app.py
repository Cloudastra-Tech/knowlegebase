"""create_app(): the FastAPI app, with its middleware, background jobs, routers and
the site. api.py builds the one `uvicorn api:app` serves."""

import contextlib
import logging
import os
import secrets

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

import db
import observability
import safety
from kb import jobs, logs, mcp_server, middleware, settings, site
from kb.routers import (admin_members, admin_sign_in, api_keys, api_v1, audit, auth, chat,
                        conversations, docs, health, index, insights, integrations, invites,
                        knowledge, saml, scim, shares, signup, sso)
from kb.services import indexing

log = logging.getLogger("uvicorn.error")

ROUTERS = (auth, sso, signup, saml, admin_sign_in, chat, conversations, shares, index, docs,
           integrations, knowledge, admin_members, invites, audit, insights, health, scim,
           api_keys, api_v1)


@contextlib.asynccontextmanager
async def _lifespan(app):
    """Background jobs that run for the life of the server."""
    safety.require_guards()          # REQUIRE_TOXICITY_GUARD=1: refuse to start without it
    # Bring the database up to date now (db.py migrates on its first connection),
    # so a failing migration stops the start instead of the first request.
    db.connect().close()
    # Re-indexing, connector syncs and the SAML metadata refresh run as jobs
    # (kb/jobs.py): here, unless JOBS_IN_PROCESS=off and `python -m kb.worker` does.
    jobs.start_in_process()
    # An index built with other chunking or embedding settings is rebuilt (a
    # shadow rebuild: the old one answers until the new one is ready).
    try:
        indexing.schedule_stale()
    except Exception:
        log.exception("Index: couldn't check which indexes need rebuilding")
    # The MCP server's requests run in its session manager's task group.
    async with app.state.mcp.running():
        yield
    jobs.stop_in_process()


def create_app():
    logs.configure()                      # LOG_FORMAT=json|text (kb/logs.py)
    observability.init_error_tracking()   # only with SENTRY_DSN; before the app is made
    docs_on = settings.api_docs_on()
    app = FastAPI(title="Knowledge Assistant API", lifespan=_lifespan,
                  docs_url="/docs" if docs_on else None,
                  redoc_url="/redoc" if docs_on else None,
                  openapi_url="/openapi.json" if docs_on else None)
    # Holds Authlib's state / nonce / PKCE verifier between the redirect to Google or
    # Microsoft and the callback — nothing else goes in it. Without SESSION_SECRET a
    # random key is used, which only cancels sign-ins in progress during a restart.
    app.add_middleware(SessionMiddleware,
                       secret_key=os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32),
                       session_cookie="kb_oauth", max_age=10 * 60, same_site="lax",
                       https_only=os.getenv("PUBLIC_URL", "").startswith("https://"))
    app.add_middleware(middleware.BodyLimit)
    app.add_middleware(middleware.SecurityHeaders, docs_paths=(app.docs_url, app.redoc_url))
    # Added last, so outermost: every request gets an id and a log line, even a refused one.
    app.add_middleware(logs.RequestLog)
    for r in ROUTERS:
        app.include_router(r.router)
    mcp_server.mount(app)              # /mcp: other agents query the knowledge base
    site.mount(app)
    return app
