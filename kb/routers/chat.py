"""Chat: a question in, the agent's answer out (whole, or streamed as SSE)."""

import asyncio
import json
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import Cancelled
from kb.deps import current_role, current_user, ws_of
from kb.rate_limit import user_limit
from kb.services import agents

router = APIRouter()
log = logging.getLogger("uvicorn.error")

STEP_PREVIEW_CHARS = 600


class ChatIn(BaseModel):
    question: str


@router.post("/api/chat")
def chat(body: ChatIn, user=Depends(current_user), role=Depends(current_role)):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    user_limit(user, "chat")
    # No waiting for re-indexing: it changes the index document by document, or
    # builds a new one beside it (services/indexing.py).
    answer, trace, sources = agents.get_agent(ws_of(user), role)(question, with_sources=True)
    return {"answer": answer, "sources": sources, "steps": _steps(trace)}


def _steps(trace):
    return [{"tool": t["tool"], "input": t["input"],
             "output": t["output"][:STEP_PREVIEW_CHARS]
             + (" …(trimmed)" if len(t["output"]) > STEP_PREVIEW_CHARS else "")}
            for t in trace]


@router.post("/api/chat/stream")
async def chat_stream(body: ChatIn, user=Depends(current_user), role=Depends(current_role)):
    """Same as /api/chat, but streams what's happening as Server-Sent Events:
         status      {"text"}                       e.g. "Checking your question"
         step_start  {"id", "tool", "label"}        e.g. "Searching company docs"
         step_end    {"id", "tool", "result"}       e.g. "found 2 relevant documents"
         answer      {"text"}                       the answer so far (PII already masked)
         done        {"answer", "sources", "steps"} the final, checked answer
         error       {"message"}
    If the browser goes away mid-answer, the agent is told to stop."""
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    user_limit(user, "chat")
    ws = ws_of(user)

    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    stop = threading.Event()                      # set when the client disconnects

    def put(event):                               # called from the worker thread
        try:
            loop.call_soon_threadsafe(events.put_nowait, event)
        except RuntimeError:                      # the event loop has shut down
            pass

    def work():
        try:
            answer, trace, sources = agents.get_agent(ws, role)(question, with_sources=True,
                                                                on_event=put, cancel=stop)
            put({"type": "done", "answer": answer, "sources": sources,
                 "steps": _steps(trace)})
        except Cancelled:                          # nobody is listening any more
            pass
        except Exception as e:                     # show it in the chat, keep serving
            # Not the question itself: it's tenant data (its length helps spot limits).
            log.exception("Chat: answering failed in workspace %s (role %s, %d characters)",
                          ws, role, len(question))
            put({"type": "error", "message": f"Something went wrong: {type(e).__name__}"})
        finally:
            put(None)

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
