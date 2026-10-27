"""
connectors/registry.py  —  every data source the app knows, live or coming soon.

The Integrations screen draws its cards from catalog() (GET /api/integrations),
and GET /api/integrations/catalog serves the same list without signing in, so
the landing page's "soon" list can be generated from here instead of drifting.
Adding a connector = one module with a Connector subclass, listed in LIVE.

PERSONAL connectors (Gmail, Google Calendar) are listed apart: each member
connects their own on the My connections screen (kb/routers/my_integrations.py),
never an admin for the workspace. A member's connection is keyed
personal_key(provider, username) ("gmail~<hash>"); get() never returns one (the
admin endpoints can't reach it), connector_of() does (the sync framework).
"""

import hashlib

from connectors import (asana, box, clickup, coda, confluence, dropbox, freshdesk, github,
                        gmail, google_calendar, google_drive, hubspot, intercom, jira, linear,
                        notion, outline, s3, salesforce, sharepoint, teams_messages, website,
                        zendesk)
from connectors.base import PERSONAL_MARK, is_personal

LIVE = (google_drive.CONNECTOR, notion.CONNECTOR, outline.CONNECTOR, website.CONNECTOR,
        confluence.CLOUD_CONNECTOR, confluence.DC_CONNECTOR, sharepoint.CONNECTOR,
        jira.CLOUD_CONNECTOR, jira.DC_CONNECTOR, zendesk.CONNECTOR, github.CONNECTOR,
        linear.CONNECTOR, dropbox.CONNECTOR, box.CONNECTOR, s3.CONNECTOR, intercom.CONNECTOR,
        freshdesk.CONNECTOR, hubspot.CONNECTOR, salesforce.CONNECTOR, asana.CONNECTOR,
        clickup.CONNECTOR, coda.CONNECTOR, teams_messages.CONNECTOR)
CONNECTORS = {c.PROVIDER: c for c in LIVE}
PERSONAL = (gmail.CONNECTOR, google_calendar.CONNECTOR)
PERSONAL_CONNECTORS = {c.PROVIDER: c for c in PERSONAL}

# Marketed as soon (connectors.md §3.1). `icon` is a simple-icons slug, if there is one.
SOON = (
    {"id": "slack", "name": "Slack", "icon": None},
)
ICONS = {"google_drive": "googledrive", "notion": "notion", "outline": "outline",
         "website": None, "confluence": "confluence", "confluence_dc": "confluence",
         "sharepoint": None, "jira": "jira", "jira_dc": "jira", "zendesk": "zendesk",
         "github": "github", "linear": "linear", "dropbox": "dropbox", "box": "box",
         "s3": None, "intercom": "intercom", "freshdesk": None, "hubspot": "hubspot",
         "salesforce": None, "asana": "asana", "clickup": "clickup", "coda": "coda",
         "teams_messages": None, "gmail": "gmail", "google_calendar": "googlecalendar"}


def get(provider):
    """A workspace connector by its id (never a personal connection)."""
    return CONNECTORS.get(provider)


def personal(provider):
    """A personal connector by its id ("gmail")."""
    return PERSONAL_CONNECTORS.get(provider)


def personal_key(provider, username):
    """The key of `username`'s own connection of a personal connector: the id and a
    hash of the username (a folder name, and no address in paths or logs)."""
    digest = hashlib.sha256((username or "").strip().lower().encode()).hexdigest()[:16]
    return f"{provider}{PERSONAL_MARK}{digest}"


def base_of(key):
    """The connector id of a connection key ("gmail~1a2b..." -> "gmail")."""
    return (key or "").split(PERSONAL_MARK, 1)[0]


def connector_of(key):
    """The connector of any connection key: a workspace connector's id, or a
    member's personal connection."""
    if is_personal(key):
        return PERSONAL_CONNECTORS.get(base_of(key))
    return CONNECTORS.get(key)


def label(provider):
    c = connector_of(provider)
    return c.LABEL if c else provider.replace("_", " ").title()


def manifest(c):
    """What the UI needs to draw a live connector's card."""
    return {"id": c.PROVIDER, "name": c.LABEL, "description": c.DESCRIPTION, "auth": c.AUTH,
            "picker": c.PICKER, "picker_required": c.PICKER_REQUIRED, "unit": c.UNIT,
            "acl": c.ACL, "icon": ICONS.get(c.PROVIDER), "setup_env": c.SETUP_ENV,
            "note": c.NOTE or None, "sign_in": c.can_sign_in(),
            "source_policy_note": c.SOURCE_POLICY_NOTE or None,
            "personal": c.PERSONAL, "availability": "live"}


def catalog():
    """Every connector: the live ones (each member's own last), then the ones
    coming soon."""
    return ([manifest(c) for c in LIVE + PERSONAL]
            + [{**s, "availability": "soon"} for s in SOON])
