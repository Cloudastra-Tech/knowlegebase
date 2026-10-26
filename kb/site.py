"""
The website.

In production (Docker) the built React app lives in frontend/dist and is served
from here, so one container + one port serves both the site and the API.
Locally, `npm run dev` serves the site instead and this folder doesn't exist.
"""

import os

from fastapi.staticfiles import StaticFiles

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "frontend", "dist")


class _Site(StaticFiles):
    """index.html is never cached, so a deploy shows up on the next load.
    Built assets have content hashes in their names, so they cache forever."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount(app):
    """Serve the site at / when it's built. Last: it answers every path the API doesn't."""
    if os.path.isdir(SITE):
        app.mount("/", _Site(directory=SITE, html=True), name="site")
