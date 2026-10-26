"""
ask(): one question in, a checked answer out. The web chat (kb/routers/chat.py),
the public API (kb/routers/api_v1.py) and the MCP server (kb/mcp_server.py) all
answer through it, so every surface gets the same agent and the same path:

  * the input guards (prompt injection, the question rules) and the output ones
    (staff pay, links, PII), inside the agent (agent.py);
  * citations: only [S#] markers for passages read for this question that the
    asker's role may see;
  * the answer's state (answered, not_found, blocked, ...) and max_access, the
    highest access level of everything the model read (conversations.outcome);
  * the log line when answering fails, without the question (tenant data).

`role` is the access role the agent searches as: "hr" or "public" (rag.ROLE_ACCESS).
Who may ask, and as which role, is the caller's to decide (a session, an API key).
"""

import logging
from dataclasses import dataclass, field

from agent import Cancelled  # noqa: F401  (re-exported: a stopped run raises it)
from kb.services import agents, conversations

log = logging.getLogger("uvicorn.error")

SURFACES = ("web", "api", "mcp")
_LABEL = {"web": "Chat", "api": "API", "mcp": "MCP"}


class Failed(Exception):
    """The assistant couldn't answer (OpenAI down, a bad key, ...). `kind`: the
    original error's type name, safe to show; the error itself is logged."""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


@dataclass
class Answer:
    answer: str
    sources: list
    state: str              # answered, partial, not_found, general_knowledge, blocked
    max_access: str         # public / restricted: everything the model read for it
    trace: list = field(default_factory=list)   # the tools it used, with their input and output
    standalone: str | None = None               # the question as searched, when it differs


def ask(ws, role, question, *, surface, history=None, history_access="public", on_event=None,
        cancel=None, out=None):
    """Answer `question` in workspace `ws` as `role` -> Answer. history: the
    conversation so far (conversations.start_turn), and history_access the highest
    access level in it. on_event / cancel: stream progress, and stop (raises
    Cancelled). out: the agent's running report (agent.run), for a caller that
    needs what was read so far after a stop. Raises Failed when answering fails."""
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of: {', '.join(SURFACES)}")
    out = {} if out is None else out
    try:
        # No waiting for re-indexing: it changes the index document by document, or
        # builds a new one beside it (services/indexing.py).
        answer, trace, sources = agents.get_agent(ws, role)(
            question, with_sources=True, on_event=on_event, cancel=cancel, history=history,
            out=out)
        state, sources, access = conversations.outcome(answer, trace, sources, out,
                                                       history_access)
    except Cancelled:
        raise
    except Exception as e:
        # Not the question itself: it's tenant data (its length helps spot limits).
        log.exception("%s: answering failed in workspace %s (role %s, %d characters)",
                      _LABEL[surface], ws, role, len(question))
        raise Failed(type(e).__name__) from e
    standalone = out.get("standalone")
    # TODO(PL-11 question log): record the question with surface=`surface` once the
    # question log (feat/question-log-insights) is on dev.
    return Answer(answer, sources, state, access, trace,
                  standalone if standalone not in (None, question) else None)
