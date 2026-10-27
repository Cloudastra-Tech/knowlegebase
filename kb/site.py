"""
The website.

In production (Docker) the built React app lives in frontend/dist and is served
from here, so one container + one port serves both the site and the API.
Locally, `npm run dev` serves the site instead and this folder doesn't exist.
"""

import os

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "frontend", "dist")


class _Site(StaticFiles):
    """index.html is never cached, so a deploy shows up on the next load.
    Built assets have content hashes in their names, so they cache forever.
    Any other path that isn't a file (/chat, /admin/sign-in, ...) is a screen of
    the app: it gets index.html, whose router (frontend/src/routes.tsx) shows it."""
    async def get_response(self, path, scope):
        path = path.replace("\\", "/")
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            # A missing API route, asset or file (a "." in its name) stays a 404.
            screen = (not path.startswith(("api/", "assets/", "scim/"))
                      and "." not in path.rsplit("/", 1)[-1])
            if exc.status_code != 404 or not screen:
                raise
            response = await super().get_response("index.html", scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount(app):
    """Serve the site at / when it's built. Last: it answers every path the API doesn't."""
    if os.path.isdir(SITE):
        app.mount("/", _Site(directory=SITE, html=True), name="site")
