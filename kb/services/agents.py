"""One agent (agent.py) per workspace and role, built on first use and kept in
state.agents until that workspace's index changes (services/indexing.py bumps
its version, and may point it at another collection): the agent's keyword
index (BM25) is a copy of the collection, made when it's built."""

import logging
import os

import workspaces
from agent import build_agent
from connectors import outline as outline_connector
from kb import state
from kb.services import indexing

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


def get_agent(ws, role):
    index = indexing.current(ws)          # (collection, version)
    with state.agents_lock:
        built = state.agents.get((ws, role))
        if not built or built[0] != index:
            built = (index, build_agent(role, index[0], outline_on(ws, role)))
            state.agents[(ws, role)] = built
        return built[1]
