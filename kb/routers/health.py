"""Health checks: the process is up, and whether it can actually serve."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import os

import db
import health
import ingest
import safety

router = APIRouter()


@router.get("/api/health")
def health_check():
    """The process is up. Docker's HEALTHCHECK calls this: keep it cheap, always 200.
    "guards" shows whether the toxicity check is running, since the app starts
    without it unless REQUIRE_TOXICITY_GUARD says not to. "version" and "commit":
    what the image was built from (Dockerfile; dev / unknown outside of one)."""
    return {"ok": True, "guards": safety.guards_status(),
            "version": os.getenv("APP_VERSION") or "dev",
            "commit": os.getenv("GIT_SHA") or "unknown"}


@router.get("/api/health/ready")
def ready_check():
    """Can this server actually serve? (database, search index, encryption key, and
    the toxicity guard if REQUIRE_TOXICITY_GUARD; see health.py). 503 with the failing check's reason otherwise. For a deploy gate or
    an uptime monitor, not for Docker: restarting the container fixes none of them."""
    guards = safety.guards_status()
    ok, checks = health.ready(db.DB_PATH, ingest.DB_FOLDER, guards=guards)
    return JSONResponse({"ok": ok, "checks": checks, "guards": guards},
                        status_code=200 if ok else 503)
