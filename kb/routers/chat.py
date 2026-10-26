"""Chat: a question in, the agent's answer out (whole, or streamed as SSE), citing
passages as [S1], [S2], ... Streamed questions can belong to a saved conversation
(kb/routers/conversations.py). A cited passage opens through /api/passages."""

import asyncio
import json
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import conversations_store
from agent import Cancelled
from kb.deps import current_role, current_user, ws_of
from kb.rate_limit import user_limit
from kb.services import agents, conversations, indexing, passages, question_log

router = APIRouter()
log = logging.getLogger("uvicorn.error")

STEP_PREVIEW_CHARS = 600


class ChatIn(BaseModel):
    question: str = ""


class StreamIn(ChatIn):
    conversation_id: str | None = None   # saved in it, with its history; None: a one-off
    retry_of: str | None = None          # ask the question of this failed answer again


@router.post("/api/chat")
def chat(body: ChatIn, user=Depends(current_user), role=Depends(current_role)):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    user_limit(user, "chat")
    ws = ws_of(user)
    out = {}
    # No waiting for re-indexing: it changes the index document by document, or
    # builds a new one beside it (services/indexing.py).
    try:
        answer, trace, sources = question_log.tracked(agents.get_agent(ws, role), ws, user, role,
                                                      "api")(question, with_sources=True, out=out)
    except Exception as e:
        # OpenAI down, a bad key, ...: say so plainly instead of a bare 500.
        log.exception("Chat: answering failed in workspace %s (role %s, %d characters)",
                      ws, role, len(question))
        raise HTTPException(502, f"The assistant couldn't answer just now ({type(e).__name__}). "
                                 "Please try again in a moment.")
    answer_state, sources, _ = conversations.outcome(answer, trace, sources, out)
    return {"answer": answer, "sources": sources, "state": answer_state,
            "steps": _steps(trace, user["role"] == "admin")}


@router.get("/api/passages/{chunk_id}")
def passage(chunk_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$"),
            user=Depends(current_user), role=Depends(current_role)):
    """A cited passage and the document's text around it, for the source viewer:
    {title, provider, url, access, page, heading_path, modified, text, before,
    after}. Checked against the asker's role on every call; a passage that isn't
    in the index and one they may not see are the same 404."""
    found = passages.get(indexing.current(ws_of(user))[0], chunk_id, role)
    if not found:
        raise HTTPException(404, "That passage isn't in your documents any more, "
                                 "or your role can't see it.")
    return found


def _steps(trace, details=True):
    """The tools the agent used. Their raw input and output only with details
    (admins): they can be long, and they're the model's working, not the answer."""
    if not details:
        return [{"tool": t["tool"]} for t in trace]
    return [{"tool": t["tool"], "input": t["input"],
             "output": t["output"][:STEP_PREVIEW_CHARS]
             + (" …(trimmed)" if len(t["output"]) > STEP_PREVIEW_CHARS else "")}
            for t in trace]


@router.post("/api/chat/stream")
async def chat_stream(body: StreamIn, user=Depends(current_user), role=Depends(current_role)):
    """Same as /api/chat, but streams what's happening as Server-Sent Events:
         conversation {"id", "title", "user_message_id", "message_id", "new_conversation"}
                                                    first, for a saved conversation only
         status      {"text"}                       e.g. "Checking your question"
         step_start  {"id", "tool", "label"}        e.g. "Searching company docs"
         step_end    {"id", "tool", "result"}       e.g. "found 2 relevant documents"
         answer      {"text", "sources"?}           the answer so far (PII already masked,
                                                    [S#] checked); sources when they change
         done        {"answer", "sources", "steps", "state", "standalone_question",
                      "message_id"}                 the final, checked answer
         error       {"message", "message_id"}
    With a conversation_id the question and its answer are saved there, and the
    conversation so far is the history it's answered with. If the browser goes
    away mid-answer (or the Stop button), the agent is told to stop and what was
    written so far is saved as a stopped answer."""
    question = body.question.strip()
    if not body.conversation_id and (body.retry_of or not question):
        raise HTTPException(400, "Please type a question.")
    user_limit(user, "chat")
    ws = ws_of(user)
    admin = user["role"] == "admin"

    turn = None
    if body.conversation_id:
        turn = await run_in_threadpool(conversations.start_turn, ws, user["username"], role,
                                       body.conversation_id, question, body.retry_of)
        question = turn["question"]
    history_access = turn["history_access"] if turn else "public"

    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    stop = threading.Event()                      # set when the client disconnects
    seen = {"text": "", "steps": []}              # what was shown so far (for a stop)

    def put(event):                               # called from the worker thread
        try:
            loop.call_soon_threadsafe(events.put_nowait, event)
        except RuntimeError:                      # the event loop has shut down
            pass

    def on_event(event):
        if event["type"] == "answer":
            seen["text"] = event["text"]
        elif event["type"] == "step_start":
            seen["steps"].append({"id": event["id"], "tool": event["tool"],
                                  "label": event["label"]})
        elif event["type"] == "step_end":
            for step in seen["steps"]:
                if step["id"] == event["id"]:
                    step["result"] = event["result"]
        put(event)

    def save(content, answer_state, out, **fields):
        if not turn:
            return
        try:
            conversations_store.finish_message(
                turn["message_id"], content, answer_state, steps=seen["steps"],
                max_access=fields.pop("max_access", None)
                or conversations.read_access(out, seen["steps"], history_access),
                latency_ms=int((time.monotonic() - started) * 1000), **fields)
        except Exception:
            log.exception("Chat: couldn't save an answer in workspace %s", ws)

    started = time.monotonic()

    def work():
        out = {}

        def standalone():                          # only when it differs from the question
            return out["standalone"] if out.get("standalone") not in (None, question) else None
        try:
            answer, trace, sources = question_log.tracked(agents.get_agent(ws, role), ws, user,
                                                          role, "web", turn)(
                question, with_sources=True, on_event=on_event, cancel=stop,
                history=turn["history"] if turn else None, out=out)
            answer_state, sources, access = conversations.outcome(answer, trace, sources, out,
                                                                  history_access)
            save(answer, answer_state, out, sources=sources, trace=_steps(trace),
                 max_access=access, standalone_question=standalone())
            put({"type": "done", "answer": answer, "sources": sources,
                 "steps": _steps(trace, admin), "state": answer_state,
                 "standalone_question": standalone(),
                 "message_id": turn["message_id"] if turn else None})
        except Cancelled:                          # nobody is listening any more
            # What was shown, with the passages its markers cite so far.
            save(seen["text"], "stopped", out, sources=out.get("sources") or [],
                 standalone_question=standalone())
        except Exception as e:                     # show it in the chat, keep serving
            # Not the question itself: it's tenant data (its length helps spot limits).
            log.exception("Chat: answering failed in workspace %s (role %s, %d characters)",
                          ws, role, len(question))
            message = f"Something went wrong: {type(e).__name__}"
            save(message, "error", out)
            put({"type": "error", "message": message,
                 "message_id": turn["message_id"] if turn else None})
        finally:
            put(None)

    if turn:
        conv = turn["conversation"]
        put({"type": "conversation", "id": conv["id"], "title": conv["title"],
             "user_message_id": turn["user_message_id"], "message_id": turn["message_id"],
             "new_conversation": turn["new_conversation"]})
    threading.Thread(target=work, daemon=True).start()

    async def sse():
        try:
            while (event := await events.get()) is not None:
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # Ends normally, or is cancelled here when the client disconnects:
            # either way nobody needs the rest of the work.
            stop.set()

    # X-Accel-Buffering: no -> nginx passes each event through immediately.
    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
