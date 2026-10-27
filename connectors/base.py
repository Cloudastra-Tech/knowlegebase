"""
connectors/base.py  —  what every connector is, and what it hands the framework.

A connector is one class (one per provider: google_drive.py, notion.py,
outline.py, website.py) that knows its provider's API. Everything else is
shared and lives in kb/services/sync.py: the sync run, skip-if-unchanged, the
files on disk, source_items, the mass-delete guard, history, and indexing.

    changes(ctx, cursor)   what changed since `cursor` (None: list everything).
                           Yields Upsert / Delete / Permissions / Skipped
                           events, and once, Cursor(new) for the next run.
    fetch(ctx, item)       an Upsert's content, when the framework decides it
                           needs it (new, or its version changed). Bytes go
                           through connectors/extract.py; None skips it.

Failures are TYPED (ConnectorError below): the framework turns the code into a
status and a plain-language message, never an exception's text.
"""

import dataclasses
from typing import Any, Iterator


# ---- Errors ------------------------------------------------------------------------
class ConnectorError(Exception):
    """A sync failed for a reason the UI can explain. `code` is one of ERRORS;
    the message is for the server log only."""
    code = "unknown"
    retry = True                   # worth trying again later by itself

    def __init__(self, detail="", retry_after=None):
        super().__init__(detail or self.code)
        self.retry_after = retry_after


class AuthExpired(ConnectorError):
    """The token was revoked or can't be refreshed: someone has to reconnect."""
    code = "auth_expired"
    retry = False


class RateLimited(ConnectorError):
    """The provider kept saying "slow down" after our retries."""
    code = "rate_limited"


class Transient(ConnectorError):
    """5xx, timeouts, connection resets: try again later."""
    code = "transient"


class ConfigError(ConnectorError):
    """Something about the connection's settings is wrong (an address we may not
    call, nothing picked, a collection that is gone): an admin has to fix it."""
    code = "config"
    retry = False


class NotFound(ConnectorError):
    """The thing asked for isn't there (404 / 410). Usually caught per item."""
    code = "not_found"
    retry = False


class Forbidden(ConnectorError):
    """403 that isn't a rate limit: no access to that item."""
    code = "forbidden"
    retry = False


class MissingScope(ConnectorError):
    """The provider says the app wasn't granted a permission this call needs
    (Atlassian: 401 "scope does not match"; Graph: 403 on a listing the app's
    permission should allow): an admin has to connect again and approve it."""
    code = "missing_scope"
    retry = False


class ConnectRefused(ConnectorError):
    """finish_connect: connecting can't finish, for a reason the Integrations
    screen explains (`reason` is the callback's error code: wrong_account,
    admin_consent_required, missing_scope, no_site, ...)."""
    code = "config"
    retry = False

    def __init__(self, reason, detail=""):
        super().__init__(detail or reason)
        self.reason = reason


class NextStep(Exception):
    """finish_connect: the browser has to make one more trip to the provider
    before the connection exists (SharePoint: the admin signs in, then approves
    the app for their tenant). `url(state)` is where to send it, with a new
    one-time state; `data` comes back to finish_connect as `step`."""

    def __init__(self, url, data):
        super().__init__("one more step")
        self.url, self.data = url, data


class FormError(ValueError):
    """What an admin typed into a connect / settings form is wrong. Unlike the
    errors above, the message is written for them and shown as it is."""


# What each code means, in words an admin can act on. The API sends these, never
# an exception's text (which can hold provider internals or URLs with tokens).
ERRORS = {
    "auth_expired": "Access was revoked or has expired. Reconnect to keep syncing.",
    "rate_limited": "The service asked us to slow down. The sync will try again shortly.",
    "transient": "The service didn't answer properly. The sync will try again shortly.",
    "config": "The connection's settings need attention: check what it syncs and its address.",
    "forbidden": "The connected account can't read what was picked. Check its sharing.",
    "not_found": "Something picked to sync no longer exists. Check what it syncs.",
    "missing_scope": "The app is missing a permission it needs. Reconnect and approve every "
                     "permission it asks for.",
    "mass_delete": "This sync would remove a large share of the synced items, so nothing was "
                   "removed. Confirm to remove them, or check the connection.",
    "unknown": "The last sync failed. It will be tried again; tell your admin if this stays.",
}


def error_code(exc):
    """The ERRORS code for any exception (unknown ones are "unknown")."""
    return exc.code if isinstance(exc, ConnectorError) and exc.code in ERRORS else "unknown"


def message(code):
    """The plain-language message for a code; anything else is "unknown"'s (a
    connection whose last_error still holds raw text from before codes existed)."""
    return ERRORS.get(code or "", ERRORS["unknown"])


# ---- What a connector yields -------------------------------------------------------
@dataclasses.dataclass
class Item:
    """One document at the source, as listed (no content yet)."""
    source_id: str                 # stable provider id ("1AbC..." for Drive, a URL for a site)
    title: str
    url: str = ""
    version: str = ""              # changes whenever the content does (modified time, etag)
    kind: str = "document"         # file | page | document | webpage
    mime: str = "text/plain"
    parent_id: str | None = None
    modified: str | None = None
    size: int | None = None
    acl: dict | None = None        # who can read it at the source, as the provider says
    attrs: dict | None = None      # provider extras kept with the item
    content: Any = None            # already in hand (Outline lists the text): no fetch


@dataclasses.dataclass
class Upsert:
    item: Item


@dataclasses.dataclass
class Delete:
    source_id: str


@dataclasses.dataclass
class Permissions:
    """The item's permissions at the source changed (content didn't)."""
    source_id: str
    acl: dict


@dataclasses.dataclass
class Skipped:
    """Seen, but not synced: too large, a type we can't read, no access."""
    source_id: str
    reason: str
    title: str = ""
    url: str = ""


@dataclasses.dataclass
class Cursor:
    """Where the next run starts. `full`: this run listed everything, so items it
    didn't mention are gone (the connector yields their Deletes itself)."""
    value: str | None
    full: bool = False


@dataclasses.dataclass
class LimitReached:
    """The connector stopped at the item limit (settings or CONNECTOR_MAX_ITEMS):
    shown on the card instead of quietly syncing part of the source."""
    limit: int


@dataclasses.dataclass
class Fetched:
    body: bytes | str
    mime: str


Change = Upsert | Delete | Permissions | Skipped | Cursor | LimitReached


@dataclasses.dataclass
class SyncContext:
    """What a connector gets for one run."""
    workspace: str
    provider: str
    settings: dict                 # the connection's settings (picked folders, URLs, ...)
    known: dict                    # {source_id: version} from source_items
    parents: dict = dataclasses.field(default_factory=dict)   # {source_id: parent_id}
    items: dict = dataclasses.field(default_factory=dict)     # {source_id: source_items row}
    secret: Any = None             # decrypted tokens (refresh through the connector)
    max_items: int = 0             # 0: no limit
    full: bool = False             # asked for a full re-list

    def missing(self, seen):
        """Deletes for known items a full listing didn't see."""
        for source_id in sorted(set(self.known) - set(seen)):
            yield Delete(source_id)


# ---- The connector -----------------------------------------------------------------
class Connector:
    """Base class. A connector overrides what its provider supports; the class
    attributes are its entry in connectors/registry.py."""
    PROVIDER = ""
    LABEL = ""
    DESCRIPTION = ""
    AUTH = "oauth"                 # oauth | token | url
    PICKER = "none"                # tree | list | urls | none
    PICKER_REQUIRED = False        # nothing syncs until something is picked
    SETTINGS_KEY = "containers"    # where the picks are kept in the connection's settings
    UNIT = "documents"             # what the card counts ("files", "pages")
    SETUP_ENV = ""                 # env vars an oauth connector needs on the server
    NOTE = ""                      # a line the card shows before connecting, if any
    ACL = False                    # captures source permissions

    def configured(self):
        """Can a workspace connect it on this server?"""
        return True

    # auth (oauth)
    def auth_url(self, redirect_uri, state):
        raise NotImplementedError

    def finish_connect(self, workspace, code, redirect_uri, user):
        """Save the connection; may raise NextStep (a connector that needs it also
        takes `step=`, the NextStep's data, on the way back)."""
        raise NotImplementedError

    def callback_code(self, params, step=None):
        """What finish_connect gets as `code` from the callback's query (empty:
        the provider sent nothing to connect with). `step`: a NextStep's data."""
        return params.get("code", "")

    # auth (token / url): check the form, save the connection; returns its account label
    def connect_form(self, workspace, form, user):
        raise NotImplementedError

    def check_containers(self, picked):
        """The picks an admin saved, cleaned: [{"id", "name", "kind", "mimeType"?}].
        Raises FormError for one that can't be used."""
        out = []
        for p in picked[:200]:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            entry = {"id": str(p["id"])[:300], "name": str(p.get("name") or "")[:200],
                     "kind": str(p.get("kind") or "folder")[:20]}
            if p.get("mimeType"):
                entry["mimeType"] = str(p["mimeType"])[:100]
            out.append(entry)
        return out

    def list_containers(self, ctx, parent=None):
        """What can be picked under `parent` (None: the top level):
        [{"id", "name", "kind", "has_children"?}]."""
        return []

    def changes(self, ctx, cursor) -> Iterator[Change]:
        raise NotImplementedError

    def fetch(self, ctx, item) -> Fetched | None:
        if item.content is not None:
            return Fetched(item.content, item.mime)
        return None

    def disconnect(self, workspace):
        """Revoke what can be revoked at the provider. Our side is removed by the caller."""
