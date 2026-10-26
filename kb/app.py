"""create_app(): the FastAPI app, with its middleware, background jobs, routers and
the site. api.py builds the one `uvicorn api:app` serves."""

import contextlib
import os
import secrets
import threading

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

import db
import observability
import safety
from kb import middleware, settings, site
from kb.routers import (admin_members, admin_sign_in, auth, chat, docs, health, index,
                        integrations, knowledge, saml, signup, sso)
from kb.services import saml_refresh, sync

ROUTERS = (auth, sso, signup, saml, admin_sign_in, chat, index, docs, integrations, knowledge,
           admin_members, health)


@contextlib.asynccontextmanager
async def _lifespan(app):
    """Background jobs that run for the life of the server."""
    safety.require_guards()          # REQUIRE_TOXICITY_GUARD=1: refuse to start without it
    # Bring the database up to date now (db.py migrates on its first connection),
    # so a failing migration stops the start instead of the first request.
    db.connect().close()
    threading.Thread(target=sync.sync_scheduler, daemon=True).start()
    threading.Thread(target=saml_refresh.saml_refresh_scheduler, daemon=True).start()
    yield


def create_app():
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
    # Added last, so outermost: on every answer.
    app.add_middleware(middleware.SecurityHeaders, docs_paths=(app.docs_url, app.redoc_url))
    for r in ROUTERS:
        app.include_router(r.router)
    site.mount(app)
    return app
