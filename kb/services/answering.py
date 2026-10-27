"""
ask(): one question in, a checked answer out. The web chat (kb/routers/chat.py),
the public API (kb/routers/api_v1.py), the MCP server (kb/mcp_server.py) and the
Slack app (kb/services/slack.py) all answer through it, so every surface gets the same agent and the same path:

  * the input guards (prompt injection, the question rules) and the output ones
    (staff pay, links, PII), inside the agent (agent.py);
  * citations: only [S#] markers for passages read for this question that the
    asker's role may see;
  * the answer's state (answered, not_found, blocked, ...) and max_access, the
    highest access level of everything the model read (conversations.outcome);
  * the question log, with the surface it came from (kb/services/question_log.py:
    what Insights shows), and the log line when answering fails, without the
    question (tenant data).

`role` is who the agent searches as: a viewer (access.Viewer: the asker's
principals and the role they view the app as) or a bare role ("hr" / "public":
that role's own principals). Who may ask, and as whom, is the caller's to decide
(a session, an API key, a Slack channel).
"""

import logging
from dataclasses import dataclass, field

import access
from agent import Cancelled  # noqa: F401  (re-exported: a stopped run raises it)
from kb.services import agents, conversations, question_log

log = logging.getLogger("uvicorn.error")

SURFACES = ("web", "api", "mcp", "slack")


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
    verified: dict | None = None                # served from a verified answer: {id, title, by, at}
    acl: list | None = None                     # who may read it (access.clauses); None: max_access


def ask(ws, user, role, question, *, surface, turn=None, on_event=None, cancel=None, out=None):
    """Answer `question` in workspace `ws` for `user` (a dict with "username") as
    `role` -> Answer. role: the asker as a viewer (access.Viewer: what they may
    see), or a bare role (its own principals: "public" is everyone's content
    only, as a Slack channel is answered). turn: the saved conversation's turn
    (conversations.start_turn): its history is what a follow-up is answered with.
    on_event / cancel: stream progress, and stop (raises Cancelled). out: the
    agent's running report (agent.run), for a caller that needs what was read so
    far after a stop. Raises Failed when answering fails."""
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of: {', '.join(SURFACES)}")
    out = {} if out is None else out
    who, role = role, access.role_of(role)
    history_access = turn["history_access"] if turn else "public"
    asker = {"principals": who} if isinstance(who, access.Viewer) else {}
    try:
        # No waiting for re-indexing: it changes the index document by document, or
        # builds a new one beside it (services/indexing.py).
        run = question_log.tracked(agents.get_agent(ws, role), ws, user, role, surface, turn)
        answer, trace, sources = run(question, with_sources=True, on_event=on_event,
                                     cancel=cancel, history=turn["history"] if turn else None,
                                     out=out, **asker)
        state, sources, level = conversations.outcome(answer, trace, sources, out,
                                                      history_access)
        acl = conversations.read_acl(out, trace, turn.get("history_acl") if turn else ())
    except Cancelled:
        raise
    except Exception as e:
        # Not the question itself: it's tenant data (its length helps spot limits).
        log.exception("Chat: answering failed in workspace %s (role %s, surface %s, "
                      "%d characters)", ws, role, surface, len(question))
        raise Failed(type(e).__name__) from e
    standalone = out.get("standalone")
    return Answer(answer, sources, state, level, trace,
                  standalone if standalone not in (None, question) else None,
                  out.get("verified"), acl)
