"""
connectors/registry.py  —  every data source the app knows, live or coming soon.

The Integrations screen draws its cards from catalog() (GET /api/integrations),
and GET /api/integrations/catalog serves the same list without signing in, so
the landing page's "soon" list can be generated from here instead of drifting.
Adding a connector = one module with a Connector subclass, listed in LIVE.
"""

from connectors import (box, confluence, dropbox, freshdesk, github, google_drive, hubspot,
                        intercom, jira, linear, notion, outline, s3, salesforce, sharepoint,
                        website, zendesk)

LIVE = (google_drive.CONNECTOR, notion.CONNECTOR, outline.CONNECTOR, website.CONNECTOR,
        confluence.CLOUD_CONNECTOR, confluence.DC_CONNECTOR, sharepoint.CONNECTOR,
        jira.CLOUD_CONNECTOR, jira.DC_CONNECTOR, zendesk.CONNECTOR, github.CONNECTOR,
        linear.CONNECTOR, dropbox.CONNECTOR, box.CONNECTOR, s3.CONNECTOR, intercom.CONNECTOR,
        freshdesk.CONNECTOR, hubspot.CONNECTOR, salesforce.CONNECTOR)
CONNECTORS = {c.PROVIDER: c for c in LIVE}

# Marketed as soon (connectors.md §3.1). `icon` is a simple-icons slug, if there is one.
SOON = (
    {"id": "slack", "name": "Slack messages", "icon": None},
)
ICONS = {"google_drive": "googledrive", "notion": "notion", "outline": "outline",
         "website": None, "confluence": "confluence", "confluence_dc": "confluence",
         "sharepoint": None, "jira": "jira", "jira_dc": "jira", "zendesk": "zendesk",
         "github": "github", "linear": "linear", "dropbox": "dropbox", "box": "box",
         "s3": None, "intercom": "intercom", "freshdesk": None, "hubspot": "hubspot",
         "salesforce": None}


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
            "source_policy_note": c.SOURCE_POLICY_NOTE or None,
            "availability": "live"}


def catalog():
    """Every connector: the live ones, then the ones coming soon."""
    return ([manifest(c) for c in LIVE]
            + [{**s, "availability": "soon"} for s in SOON])
