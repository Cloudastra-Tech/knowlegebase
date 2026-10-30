"""
ask(): one question in, a checked answer out. The web chat (kb/routers/chat.py),
the public API (kb/routers/api_v1.py), the MCP server (kb/mcp_server.py), the
Slack app (kb/services/slack.py), the Teams bot (kb/services/teams.py), the browser
extension (through the public API) and email-in (kb/services/email_in.py) all answer
through it, so every surface gets the same agent and the same path:

  * the input guards (prompt injection, the question rules) and the output ones
    (staff pay, links, PII), inside the agent (agent.py);
  * citations: only [S#] markers for passages read for this question that the
    asker's role may see;
  * the answer's state (answered, not_found, blocked, ...) and max_access, the
    highest access level of everything the model read (conversations.outcome);
  * the question log, with the surface it came from (kb/services/question_log.py:
    what Insights shows), and the log line when answering fails, without the
    question (tenant data);
  * the answer cache (kb/services/answer_cache.py): a question asked without
    history is answered from the answer given before to the SAME set of
    principals, while the index and settings are unchanged.

`role` is who the agent searches as: a viewer (access.Viewer: the asker's
principals and the role they view the app as) or a bare role ("hr" / "public":
that role's own principals). Who may ask, and as whom, is the caller's to decide
(a session, an API key, a Slack channel).
"""

import json
import logging
import time
from dataclasses import dataclass, field

import access
import language
import models
import safety
import spaces_store
from agent import Cancelled  # noqa: F401  (re-exported: a stopped run raises it)
from kb.services import agents, answer_cache, conversations, question_log, spaces

log = logging.getLogger("uvicorn.error")

SURFACES = ("web", "api", "mcp", "slack", "teams", "extension", "email")


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
    cached: str | None = None                   # exact / semantic: from the answer cache
    # The support reply (safety.support_reply): the toxicity check's SELF_HARM
    # verdict, a blocked state shown as a calm reply, not as a refusal.
    support: bool = False


def _space_key(ws, context):
    """What a cached answer depends on of the space it's asked in ('' outside one):
    the space, its instructions and its sources, so a change to it is a new key."""
    space = context.space if context else None
    if not space:
        return ""
    sources = sorted((s["match_type"], s["match_value"])
                     for s in spaces_store.sources(ws, space["id"]))
    return json.dumps([space["id"], space.get("instructions") or "", sources])


def _from_cache(ws, user, role, surface, question, hit, turn, on_event, out, started,
                model=None):
    """A cached answer, as ask() returns one: shown, logged (no tokens), with the
    answer_cache step first in its steps."""
    row = hit.row
    trace = [answer_cache.step(hit)] + list(row["trace"])
    out.update(standalone=question, sources=row["sources"], cached=hit.kind)
    if on_event:
        on_event({"type": "answer", "text": row["answer"], "sources": row["sources"]})
    question_log.record(ws, user, role, surface, question, row["state"], answer=row["answer"],
                        trace=trace, sources=row["sources"], max_access=row["max_access"],
                        acl=access.message_clauses(row),
                        latency_ms=int((time.monotonic() - started) * 1000),
                        message_id=turn["message_id"] if turn else None, cache_hit=hit.kind,
                        # No model ran: no model logged (usage.model), as before picks.
                        **({"provider": model[0]} if model else {}),
                        language=language.detect(question))
    return Answer(row["answer"], row["sources"], row["state"], row["max_access"], trace, None,
                  None, row["acl"], hit.kind)


def ask(ws, user, role, question, *, surface, turn=None, on_event=None, cancel=None, out=None,
        cache=True, model=None, search=True):
    """_ask() with the workspace's models (models.scope): the answer cache's
    embeddings and every model the answer needs are its provider's. model:
    (provider, model id), the answer model the chat picked, already checked
    against the workspace's enabled ones (kb/services/model_choice.py); None:
    the workspace's default. It's part of the answer cache's key (release()).
    search: False answers without searching the knowledge (a saved prompt set not
    to search, agent.run(search=)); such an answer is never cached or served from it."""
    with models.scope_for(ws) as on:
        with models.scope(on.with_answer(*model) if model else on):
            return _ask(ws, user, role, question, surface=surface, turn=turn,
                        on_event=on_event, cancel=cancel, out=out, cache=cache, model=model,
                        search=search)


def _ask(ws, user, role, question, *, surface, turn=None, on_event=None, cancel=None,
         out=None, cache=True, model=None, search=True):
    """Answer `question` in workspace `ws` for `user` (a dict with "username") as
    `role` -> Answer. role: the asker as a viewer (access.Viewer: what they may
    see), or a bare role (its own principals: "public" is everyone's content
    only, as a Slack channel is answered). turn: the saved conversation's turn
    (conversations.start_turn): its history is what a follow-up is answered with.
    on_event / cancel: stream progress, and stop (raises Cancelled). out: the
    agent's running report (agent.run), for a caller that needs what was read so
    far after a stop. cache: False asks again without reading the answer cache
    (retry, regenerate); the new answer still replaces the cached one. Raises
    Failed when answering fails.
    A conversation asked in a space searches only the space's sources (among
    what the asker may see), and one with files attached reads them too
    (spaces.context_for)."""
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of: {', '.join(SURFACES)}")
    started = time.monotonic()
    out = {} if out is None else out
    who, role = role, access.role_of(role)
    history_access = turn["history_access"] if turn else "public"
    asker = {"principals": who} if isinstance(who, access.Viewer) else {}
    try:
        conversation = (turn or {}).get("conversation")    # a saved chat (not every surface)
        context = spaces.context_for(ws, user, conversation) if conversation else None
    except Exception as e:
        log.exception("Chat: couldn't read the space of a question in workspace %s", ws)
        raise Failed(type(e).__name__) from e
    if context:
        asker["context"] = context
    # ANSWER CACHE: never for a follow-up (its meaning depends on the conversation),
    # nor with files attached (they're the asker's, and only this chat's); in a
    # space, keyed by the space as it is now (its sources and instructions); nor
    # without the search (its answer isn't one from the knowledge).
    scope = None
    if search and not (turn and turn.get("history")) and not (context and context.files):
        try:
            scope = answer_cache.scope(ws, who, _space_key(ws, context))
        except Exception:
            log.exception("Answer cache: unavailable in workspace %s; answering as usual", ws)
    if scope and cache:
        hit = answer_cache.lookup(scope, question)
        if hit:
            return _from_cache(ws, user, role, surface, question, hit, turn, on_event, out,
                               started, model)
    try:
        # No waiting for re-indexing: it changes the index document by document, or
        # builds a new one beside it (services/indexing.py).
        run = question_log.tracked(agents.get_agent(ws, role), ws, user, role, surface, turn)
        if model:
            asker["answer_model"] = model
        if not search:
            asker["search"] = False
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
    done = Answer(answer, sources, state, level, trace,
                  standalone if standalone not in (None, question) else None,
                  out.get("verified"), acl,
                  support=out.get("refused_by") == safety.SELF_HARM)
    answer_cache.save(scope, question, done, out,
                      user.get("username") if isinstance(user, dict) else user)
    return done
