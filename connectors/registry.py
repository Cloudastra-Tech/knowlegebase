"""
connectors/registry.py  —  every data source the app knows, live or coming soon.

The Integrations screen draws its cards from catalog() (GET /api/integrations),
and GET /api/integrations/catalog serves the same list without signing in, so
the landing page's "soon" list can be generated from here instead of drifting.
Adding a connector = one module with a Connector subclass, listed in LIVE.
"""

from connectors import google_drive, notion, outline, website

LIVE = (google_drive.CONNECTOR, notion.CONNECTOR, outline.CONNECTOR, website.CONNECTOR)
CONNECTORS = {c.PROVIDER: c for c in LIVE}

# Marketed as soon (connectors.md §3.1). `icon` is a simple-icons slug, if there is one.
SOON = (
    {"id": "confluence", "name": "Confluence", "icon": "confluence"},
    {"id": "sharepoint", "name": "SharePoint", "icon": None},
    {"id": "slack", "name": "Slack", "icon": None},
    {"id": "jira", "name": "Jira", "icon": "jira"},
    {"id": "github", "name": "GitHub", "icon": "github"},
    {"id": "dropbox", "name": "Dropbox", "icon": "dropbox"},
    {"id": "zendesk", "name": "Zendesk", "icon": "zendesk"},
)
ICONS = {"google_drive": "googledrive", "notion": "notion", "outline": "outline",
         "website": None}


def get(provider):
    return CONNECTORS.get(provider)


def label(provider):
    c = CONNECTORS.get(provider)
    return c.LABEL if c else provider.replace("_", " ").title()


def manifest(c):
    """What the UI needs to draw a live connector's card."""
    return {"id": c.PROVIDER, "name": c.LABEL, "description": c.DESCRIPTION, "auth": c.AUTH,
            "picker": c.PICKER, "picker_required": c.PICKER_REQUIRED, "unit": c.UNIT,
            "acl": c.ACL, "icon": ICONS.get(c.PROVIDER), "setup_env": c.SETUP_ENV,
            "availability": "live"}


def catalog():
    """Every connector: the live ones, then the ones coming soon."""
    return ([manifest(c) for c in LIVE]
            + [{**s, "availability": "soon"} for s in SOON])
