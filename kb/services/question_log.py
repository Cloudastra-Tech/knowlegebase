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
full keeps it, redacted masks emails, phone and card numbers with the same rules
answers are masked with (safety.redact_pii), metadata keeps none. It's applied
here, before the row is written, so unmasked text is never stored.
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

import question_log_store as store
from agent import Cancelled
from kb.services import agents, conversations, webhooks
from safety import redact_pii

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
def text_for(mode, text, limit):
    """`text` as the log keeps it under `mode`."""
    if mode == "metadata" or not text:
        return None
    text = text[:limit]
    return redact_pii(text) if mode == "redacted" else text


def block_reason(answer):
    """Which guard refused a question, from the message it gave (safety.py)."""
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


def _sources(sources):
    """One entry per document (a passage-level list cites a document once per passage)."""
    out, seen = [], set()
    for s in sources or []:
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
           max_access="restricted", latency_ms=None, usage=None, message_id=None):
    """Write one row. Never raises: a question is never failed by its log. A
    question the documents couldn't answer is also a question.unanswered webhook,
    with its text only as the log keeps it (none in metadata mode)."""
    try:
        mode = store.settings(ws)["mode"]
        kept = text_for(mode, question, QUESTION_CHARS)
        row_id = store.add(
            ws, user["username"] if isinstance(user, dict) else user, surface, outcome,
            text_mode=mode, question=kept,
            answer=text_for(mode, answer, ANSWER_CHARS), message_id=message_id, role=role,
            block_reason=block_reason(answer) if outcome == "blocked" else None,
            sources=[] if outcome == "blocked" else _sources(sources), tools=_tools(trace),
            max_access=max_access, latency_ms=latency_ms,
            tokens_in=usage.tokens_in if usage else None,
            tokens_out=usage.tokens_out if usage else None,
            tokens_cached=usage.tokens_cached if usage else None,
            model=usage.model if usage else None)
        if outcome == "not_found":
            webhooks.question_unanswered(ws, row_id, surface, kept)
        return row_id
    except Exception:
        log.exception("Question log: couldn't log a question in workspace %s", ws)
        return None


def tracked(run, ws, user, role, surface, turn=None):
    """The agent `run`, logging each question it's asked (see the top of this file).
    turn: the saved conversation's turn (kb/services/conversations.start_turn), for
    the answer's message id and the access level of the history it was given."""
    message_id = turn["message_id"] if turn else None
    history_access = turn["history_access"] if turn else "public"

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
                if agents.outline_on(ws, role):
                    levels.append(agents.outline_access())
                fields["max_access"] = conversations.max_access(levels)
            record(ws, user, role, surface, question, outcome, usage=usage,
                   latency_ms=int((time.monotonic() - started) * 1000), message_id=message_id,
                   **fields)

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
            outcome, sources, access = conversations.outcome(answer, trace, sources, out,
                                                             history_access)
        except Exception:
            log.exception("Question log: couldn't tell the outcome of an answer")
            outcome, access = "answered", "restricted"
        finish(outcome, answer=answer, trace=trace, sources=sources, max_access=access)
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
        store.redact_text(ws, redact_pii)
    return store.settings(ws)
