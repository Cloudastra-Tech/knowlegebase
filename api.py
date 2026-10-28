"""
api.py  —  the ASGI entry point: `uvicorn api:app`.

The app is the kb package (kb/app.py builds it; the endpoints are in kb/routers/,
listed in kb/routers/__init__.py). This file only makes the one app the server,
the Dockerfile and the tests use.

Run it (from the project folder):
    venv/bin/uvicorn api:app --reload --port 8010
then, in frontend/:   npx vite --port 5180 --strictPort
"""

from kb.app import create_app

app = create_app()
