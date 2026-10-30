"""
The question log's write path (question_log_store.py holds the rows; platform.md
PL-11 part 2). Every place that asks the agent wraps it once:

    answer, trace, sources = question_log.tracked(agents.get_agent(ws, role), ws, user,
                                                  role, "web", turn)(question, ...)

tracked() calls the agent as it was called and logs the question whatever happens
(answered, refused, failed or stopped): the outcome, block reason, the tools and
sources it used, the highest access level the model read, the latency, and the
tokens of every model call made while answering (counting(), LangChain callbacks).
Logging never fails a question: an error writing the row is only logged.

The TEXT logged follows the workspace's mode (question_log_store.settings):
full keeps it, redacted masks the personal data the workspace's policy names for
logs (pii.py, PL-13: by default every type), metadata keeps none. It's applied
here, before the row is written, so unmasked text is never stored.

Each row also says whether the answer cache served it (cache_hit), how many of
its figures the faithfulness check didn't find in what it cited (unsupported),
and the question's language: the quality trend Insights shows (P2-3).
"""

import contextlib
import contextvars
import logging
import threading
import time

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration
from langchain_core.tracers.context import register_configure_hook

import access
import models
import pii
import question_log_store as store
import safety
from agent import Cancelled
from kb.services import agents, assistant, conversations, webhooks

log = logging.getLogger("uvicorn.error")

ANSWER_CHARS = 4000               # the log keeps the start of a long answer
QUESTION_CHARS = 2000


# ---- Tokens: every model call made while answering ------------------------------------
class UsageCounter(BaseCallbackHandler):
    """Adds up the tokens of each model call it sees. Calls that stream report
    their usage at the end (langchain-openai asks for it by default)."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.tokens_in = self.tokens_out = self.calls = 0
        # Input tokens OpenAI served from its prompt cache (part of tokens_in);
        # None until a call reports it (models.py, PROMPT CACHING).
        self.tokens_cached = None
        self.models = {}              # model name -> tokens

    def on_llm_end(self, response, **kwargs):
        tokens_in = tokens_out = 0
        cached = None
        model = (response.llm_output or {}).get("model_name")
        for generations in response.generations or []:
            for g in generations:
                message = g.message if isinstance(g, ChatGeneration) else None
                usage = getattr(message, "usage_metadata", None) if isinstance(
                    message, AIMessage) else None
                if usage:
                    tokens_in += usage.get("input_tokens") or 0
                    tokens_out += usage.get("output_tokens") or 0
                    read = (usage.get("input_token_details") or {}).get("cache_read")
                    if read is not None:
                        cached = (cached or 0) + read
                    model = (message.response_metadata or {}).get("model_name") or model
        if not (tokens_in or tokens_out):             # older providers: llm_output only
            usage = (response.llm_output or {}).get("token_usage") or {}
            tokens_in = usage.get("prompt_tokens") or 0
            tokens_out = usage.get("completion_tokens") or 0
            read = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            cached = read if read is not None else cached
        with self._lock:
            self.calls += 1
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out
            if cached is not None:
                self.tokens_cached = (self.tokens_cached or 0) + cached
            if model:
                self.models[model] = self.models.get(model, 0) + tokens_in + tokens_out

    @property
    def model(self):
        """The model that used the most tokens (the one that wrote the answer)."""
        return max(self.models, key=self.models.get) if self.models else None


_counter = contextvars.ContextVar("question_log_usage", default=None)
# Every LangChain run started while _counter is set gets the counter as a callback,
# and hands it on to the runs inside it (a graph node, a tool's own model call).
register_configure_hook(_counter, inheritable=True)


@contextlib.contextmanager
def counting():
    """A UsageCounter for the model calls made in this context. LangGraph carries
    the context into the threads its nodes and tools run in; a plain thread pool
    doesn't, so agent.py and rag.py submit to theirs (the input guards, the
    parallel search, HyDE and multi-query) in a copy of the context."""
    counter = UsageCounter()
    token = _counter.set(counter)
    try:
        yield counter
    finally:
        _counter.reset(token)


# ---- What's written --------------------------------------------------------------------
def text_for(mode, text, limit, mask=None):
    """`text` as the log keeps it under `mode`. mask: the workspace's policy for logs
    (pii.Policy.log); None masks every type."""
    if mode == "metadata" or not text:
        return None
    text = text[:limit]
    if mode != "redacted":
        return text
    return mask(text) if mask else pii.redact(text)


# The log's block_reason for the reason a guard gave (agent.run's report
# "refused_by": safety.ASKED_REASONS, or the guard's name).
_LOGGED_AS = {safety.SELF_HARM: "self_harm", "injection": "injection", "toxic": "policy",
              "length": "policy"}


def block_reason(answer, refused_by=None):
    """Which guard refused a question: by the reason it gave (refused_by), else
    from the message it gave (safety.py). self_harm: given the support reply
    (the SELF_HARM verdict), logged under the blocked outcome, since outcome's
    CHECK has no value of its own for it; only ever from the verdict."""
    if refused_by in _LOGGED_AS:
        return _LOGGED_AS[refused_by]
    text = answer or ""
    if "prompt-injection" in text:
        return "injection"
    if text.startswith("Staff salary information is restricted"):
        return "restricted_topic"
    if text.startswith("Sorry, I can't help with that request"):
        return "policy"
    return "other"


def _source(s):
    keep = ("doc_id", "doc_key", "title", "provider", "url", "access", "kind", "modified")
    return {k: s[k] for k in keep if s.get(k) not in (None, "")}


def _personal(doc_key):
    parts = (doc_key or "").split("/")
    return len(parts) > 2 and parts[0] == "connectors" and "~" in parts[1]


def personal_source(s):
    """Is this cited source from a member's own Gmail or Calendar (a personal
    connection, connectors/base.py)? By its doc_key when it has one; an answer's
    sources usually don't, so also by its provider: only personal connections are
    Gmail or Google Calendar."""
    from connectors import registry
    return _personal(s.get("doc_key")) or s.get("provider") in {
        c.LABEL for c in registry.PERSONAL}


def _sources(sources):
    """One entry per document (a passage-level list cites a document once per passage).
    A file the asker attached, or keeps in their space, is theirs alone: not logged,
    so its name never reaches Insights. Nor does a message or event from the
    asker's own Gmail or Calendar (a personal connection: connectors/base.py)."""
    out, seen = [], set()
    for s in sources or []:
        if s.get("attachment") or personal_source(s):
            continue
        entry = _source(s)
        key = entry.get("doc_id") or entry.get("doc_key") or (entry.get("provider"),
                                                              entry.get("title"))
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


def _tools(trace):
    return list(dict.fromkeys(t.get("tool") for t in trace or [] if t.get("tool")))


def record(ws, user, role, surface, question, outcome, *, answer=None, trace=(), sources=(),
           max_access="restricted", latency_ms=None, usage=None, message_id=None,
           cache_hit=None, unsupported=None, language=None, provider=None, acl=None,
           refused_by=None):
    """Write one row. Never raises: a question is never failed by its log. A
    question the documents couldn't answer is also a question.unanswered webhook,
    with its text only as the log keeps it (none in metadata mode). acl: the
    answer's audience (access.py clauses), which Insights reads it by; None: by
    max_access. provider: who answered, the provider of the answer model the chat
    picked (kb/services/model_choice.py); None: the workspace's. The model is
    always usage.model, the one that used the most tokens, as on every surface:
    the column's meaning (and Insights' cost) never depends on a pick.
    refused_by: the reason the guard that refused it gave (block_reason)."""
    try:
        mode = store.settings(ws)["mode"]
        mask = assistant.pii_policy(ws).log if mode == "redacted" else None
        kept = text_for(mode, question, QUESTION_CHARS, mask)
        row_id = store.add(
            ws, user["username"] if isinstance(user, dict) else user, surface, outcome,
            text_mode=mode, question=kept,
            answer=text_for(mode, answer, ANSWER_CHARS, mask), message_id=message_id, role=role,
            block_reason=block_reason(answer, refused_by) if outcome == "blocked" else None,
            sources=[] if outcome == "blocked" else _sources(sources), tools=_tools(trace),
            max_access=max_access, latency_ms=latency_ms,
            tokens_in=usage.tokens_in if usage else None,
            tokens_out=usage.tokens_out if usage else None,
            tokens_cached=usage.tokens_cached if usage else None,
            model=usage.model if usage else None, cache_hit=cache_hit,
            unsupported=unsupported, language=language,
            provider=provider or _provider(ws), acl=acl)
        if outcome == "not_found":
            webhooks.question_unanswered(ws, row_id, surface, kept)
        return row_id
    except Exception:
        log.exception("Question log: couldn't log a question in workspace %s", ws)
        return None


def _provider(ws):
    """Who answers in the workspace now (models.py): openai or bedrock."""
    try:
        return models.for_workspace(ws).provider
    except Exception:
        return None


def tracked(run, ws, user, role, surface, turn=None):
    """The agent `run`, logging each question it's asked (see the top of this file).
    turn: the saved conversation's turn (kb/services/conversations.start_turn), for
    the answer's message id and the access level of the history it was given."""
    message_id = turn["message_id"] if turn else None
    history_access = turn["history_access"] if turn else "public"
    history_acl = (turn or {}).get("history_acl") or ()

    def logged(question, *args, **kwargs):
        out = kwargs.get("out")
        if out is None:
            out = kwargs["out"] = {}
        shown = {"text": None}                    # what a stopped answer had written
        on_event = kwargs.get("on_event")
        if on_event:
            def watch(event):
                if event.get("type") == "answer":
                    shown["text"] = event.get("text")
                on_event(event)
            kwargs["on_event"] = watch
        started = time.monotonic()

        def finish(outcome, **fields):
            # Stopped or failed: what the model read may include the Outline wiki.
            if "max_access" not in fields:
                levels = [conversations.read_access(out, [], history_access)]
                found = list(conversations.read_acl(out, [], history_acl))
                if agents.outline_on(ws, role):
                    levels.append(agents.outline_access())
                    found.append(access.level_acl(agents.outline_access()))
                fields["max_access"] = conversations.max_access(levels)
                fields["acl"] = access.clauses(found)
            unsupported = out.get("unsupported")
            picked = kwargs.get("answer_model")
            record(ws, user, role, surface, question, outcome, usage=usage,
                   provider=picked[0] if picked else getattr(run, "provider", None),
                   latency_ms=int((time.monotonic() - started) * 1000), message_id=message_id,
                   unsupported=None if unsupported is None else len(unsupported),
                   language=out.get("language"), refused_by=out.get("refused_by"), **fields)

        with counting() as usage:
            try:
                result = run(question, *args, **kwargs)
            except Cancelled:
                finish("stopped", answer=shown["text"])
                raise
            except Exception:
                finish("error")
                raise
        answer, trace = result[0], result[1]
        sources = result[2] if len(result) > 2 else []
        try:
            outcome, sources, level = conversations.outcome(answer, trace, sources, out,
                                                            history_access)
            # Its audience, as the saved answer's (access.message_clauses): what the
            # model read and each source cited. Insights is read by it.
            acl = access.message_clauses({"acl": conversations.read_acl(out, trace, history_acl),
                                          "sources": sources})
        except Exception:
            log.exception("Question log: couldn't tell the outcome of an answer")
            outcome, level, acl = "answered", "restricted", None
        finish(outcome, answer=answer, trace=trace, sources=sources, max_access=level, acl=acl)
        return result

    return logged


# ---- The workspace's setting -----------------------------------------------------------
def change_settings(ws, mode, days):
    """Set how much text the log keeps, and for how long. A stricter mode applies
    to what was logged before too: metadata clears the text now (the store does
    it), redacted masks what full had kept."""
    before = store.settings(ws)["mode"]
    store.set_settings(ws, mode, days)
    if before == "full" and mode == "redacted":
        store.redact_text(ws, assistant.pii_policy(ws).log)
    return store.settings(ws)
