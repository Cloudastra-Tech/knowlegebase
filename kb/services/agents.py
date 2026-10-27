"""One agent (agent.py) per workspace and role, built on first use and kept in
state.agents until that workspace's index changes (services/indexing.py bumps
its version, and may point it at another collection): the agent's keyword
index (BM25) is a copy of the collection, made when it's built. A change to
the workspace's assistant settings, glossary or source rules builds it again
the same way (assistant_store.version)."""

import logging
import os

import assistant_store
import members_store
import workspaces
from agent import build_agent
from connectors import outline as outline_connector
from kb import state
from kb.services import assistant, indexing

log = logging.getLogger("uvicorn.error")

OUTLINE_ROLES = {"public": ("public", "hr"), "hr": ("hr",)}   # OUTLINE_MIN_ROLE -> roles


def outline_on(ws, role):
    """The LIVE Outline tool: a wiki configured server-wide (.env) for the default
    workspace. It is searched with ONE server-wide token, so the wiki has no
    per-user permissions: OUTLINE_MIN_ROLE says which roles get it at all
    (public, the default, = everyone; hr = HR / Admin only). An unknown value
    turns it off. A workspace that SYNCS an Outline wiki (connectors/outline.py)
    searches its synced copy instead, under that connection's access level, so
    the live tool is off there."""
    if ws != workspaces.DEFAULT or not os.getenv("OUTLINE_API_TOKEN"):
        return False
    if outline_connector.has_sync(ws):
        return False
    wanted = os.getenv("OUTLINE_MIN_ROLE", "public").strip().lower() or "public"
    if wanted not in OUTLINE_ROLES:
        log.warning("OUTLINE_MIN_ROLE=%r isn't public or hr; the Outline wiki is off", wanted)
        return False
    return role in OUTLINE_ROLES[wanted]


def outline_access():
    """The access level of what the Outline wiki returns: restricted when only HR /
    Admin may search it (OUTLINE_MIN_ROLE=hr), else public."""
    wanted = os.getenv("OUTLINE_MIN_ROLE", "public").strip().lower() or "public"
    return "public" if wanted == "public" else "restricted"


def _build(ws, role, index):
    """The agent with the workspace's own settings (kb/services/assistant.py): its
    preferences and tools, glossary, source rules and verified answers. index:
    (collection, version), so the agents of both roles share one keyword index."""
    from kb.services import verified
    settings = assistant.effective(ws)
    collection, version = index
    return build_agent(role, collection, outline_on(ws, role), prefs=assistant.prefs(settings),
                       glossary=assistant.glossary(ws) or None,
                       rules=assistant.rules(ws) or None,
                       verified=verified.Matcher(ws, role, settings["adapt_verified"]),
                       version=f"{version}" if version else None,
                       pii=assistant.pii_policy(ws, settings))


def get_agent(ws, role):
    """The workspace's agent for a role (public / hr: it decides the live Outline
    tool). Who asks is given on each call (run(principals=...)), so the agents
    are per role, never per person or group: the search filters per question."""
    # Built again when the index changes (indexing.py bumps its version) or the
    # workspace's assistant settings, glossary or source rules do (their version),
    # or its verified domains (the emails answers may keep, pii.Policy).
    key = (indexing.current(ws), assistant_store.version(ws),   # ((collection, version), n,
           tuple(sorted(members_store.verified_domains(ws))))   #  domains)
    with state.agents_lock:
        built = state.agents.get((ws, role))
        if not built or built[0] != key:
            built = (key, _build(ws, role, key[0]))
            state.agents[(ws, role)] = built
        return built[1]
