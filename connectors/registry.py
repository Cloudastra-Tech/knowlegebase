"""
connectors/registry.py  —  every data source the app knows, live or coming soon.

The Integrations screen draws its cards from catalog() (GET /api/integrations),
and GET /api/integrations/catalog serves the same list without signing in, so
the landing page's "soon" list can be generated from here instead of drifting.
Adding a connector = one module with a Connector subclass, listed in LIVE.
"""

from connectors import (confluence, github, google_drive, jira, notion, outline, sharepoint,
                        website, zendesk)

LIVE = (google_drive.CONNECTOR, notion.CONNECTOR, outline.CONNECTOR, website.CONNECTOR,
        confluence.CLOUD_CONNECTOR, confluence.DC_CONNECTOR, sharepoint.CONNECTOR,
        jira.CLOUD_CONNECTOR, jira.DC_CONNECTOR, zendesk.CONNECTOR, github.CONNECTOR)
CONNECTORS = {c.PROVIDER: c for c in LIVE}

# Marketed as soon (connectors.md §3.1). `icon` is a simple-icons slug, if there is one.
SOON = (
    {"id": "slack", "name": "Slack", "icon": None},
    {"id": "dropbox", "name": "Dropbox", "icon": "dropbox"},
)
ICONS = {"google_drive": "googledrive", "notion": "notion", "outline": "outline",
         "website": None, "confluence": "confluence", "confluence_dc": "confluence",
         "sharepoint": None, "jira": "jira", "jira_dc": "jira", "zendesk": "zendesk",
         "github": "github"}


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
            "note": c.NOTE or None, "sign_in": c.can_sign_in(),
            "availability": "live"}


def catalog():
    """Every connector: the live ones, then the ones coming soon."""
    return ([manifest(c) for c in LIVE]
            + [{**s, "availability": "soon"} for s in SOON])
