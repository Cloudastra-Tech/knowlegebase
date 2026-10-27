"""Settings read from the environment that more than one part of the API uses."""

import os

SESSION_SECONDS = 12 * 60 * 60
# Uploads: each file, and each request as a whole, in MB (UPLOAD_MAX_FILE_MB,
# UPLOAD_MAX_REQUEST_MB). Every other request body has the per-request limit too.
UPLOAD_MAX_FILE_MB = 25
UPLOAD_MAX_REQUEST_MB = 100

ON = ("on", "true", "1", "yes")


def env_int(name, default, minimum=1):
    """A whole number from the environment, or `default` if it isn't set (or isn't one)."""
    try:
        return max(minimum, int(os.getenv(name, "")))
    except ValueError:
        return default


def api_docs_on():
    """FastAPI's own API pages (/docs, /redoc, /openapi.json): only on a developer's
    machine (APP_ENV=dev or local) or when asked for (API_DOCS=on). In production
    they would show anyone every endpoint and its parameters."""
    return (os.getenv("API_DOCS", "").strip().lower() in ON
            or os.getenv("APP_ENV", "").strip().lower() in ("dev", "local"))
