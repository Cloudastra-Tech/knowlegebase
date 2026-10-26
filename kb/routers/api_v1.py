"""
The public API, version 1: for customers' own scripts, intranet pages and
helpdesks. Every call carries "Authorization: Bearer kb_live_..." (an API key,
kb/api_auth.py); what a key can do is its scopes, and what it can see is the
role it acts as, looked up on every call. This is the API we keep stable: the
app's own /api/* may change with the site.

    POST   /api/v1/ask        {question, conversation_id?, stream?} -> {answer, sources, state}
                              (stream: true -> Server-Sent Events, as the chat streams)  ask
    POST   /api/v1/search     {query, k} -> passages the key's role may see, no answer   search
    GET    /api/v1/sources    everything the assistant can search                      search
    GET    /api/v1/documents  the workspace's uploaded documents                      docs:read
    GET    /api/v1/documents/{access}/{name}   one of them                            docs:read
    POST   /api/v1/documents  {name, access, content}: a new text document           docs:write
    POST   /api/v1/documents/upload   multipart: files[], access                     docs:write
    DELETE /api/v1/documents/{access}/{name}                                         docs:write
    GET    /api/v1/audit      ?actor&action&from&to&cursor: the audit log            audit:read
    GET    /api/v1/openapi.json   this API's OpenAPI document (no key needed)

Every call is audited, with surface "api" and the key's prefix: questions by
their length and outcome, never their text. Calls are rate-limited per key and
per workspace (kb/rate_limit.key_limit); a question also counts as one of its
person's chat questions.
"""

import asyncio
import json
import os
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import audit_store
import conversations_store
import docs_store
import workspaces
from kb.api_auth import api_key
from kb.audit import audited
from kb.rate_limit import user_limit
from kb.routers import docs as docs_router
from kb.routers import knowledge as knowledge_router
from kb.routers.audit import Filters
from kb.services import answering, conversations, retrieval

router = APIRouter(prefix="/api/v1")
VERSION = "1.0.0"
NAME = r"^[^/\\]{1,200}$"


# ---- What goes in and comes out ----------------------------------------------------------
class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(
        None, description='Continue one of your conversations (its history is used), or "new" '
                          "to start one. Personal keys only. The answer says which to send next.")
    stream: bool = Field(False, description="Answer as Server-Sent Events: status, step_start, "
                                            "step_end, answer (the text so far), then done or error.")


class Source(BaseModel):
    n: int | None = Field(None, description="The [S#] marker that cites it in the answer.")
    kind: str | None = Field(None, description='"general_knowledge" for Wikipedia, else absent.')
    title: str
    provider: str | None = None
    url: str | None = None
    access: str | None = None
    doc_id: str | None = None
    chunk_id: str | None = None
    page: int | None = None
    heading_path: str | None = None
    modified: str | float | None = None
    snippet: str | None = None


class AskOut(BaseModel):
    answer: str = Field(description="Markdown, citing sources as [S1], [S2], ...")
    sources: list[Source]
    state: Literal["answered", "partial", "not_found", "general_knowledge", "blocked"]
    conversation_id: str | None = None
    message_id: str | None = None


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    k: int = Field(5, ge=1, le=retrieval.MAX_K)


class Passage(BaseModel):
    doc_id: str | None = None
    doc_key: str | None = None
    chunk_id: str | None = None
    title: str
    provider: str | None = None
    url: str | None = None
    access: str
    page: int | None = None
    heading_path: str | None = None
    modified: str | float | None = None
    text: str
    rank: int | None = None


class SearchOut(BaseModel):
    results: list[Passage]


class SourceItem(BaseModel):
    title: str
    source: str = Field(description='"Uploaded", or the connected tool it is synced from.')
    access: str
    url: str | None = None
    modified: str | float | None = None
    size: int | None = None
    chunks: int


class SourcesOut(BaseModel):
    sources: list[SourceItem]


class Document(BaseModel):
    access: Literal["public", "restricted"]
    name: str
    size: int
    modified: float


class DocumentsOut(BaseModel):
    documents: list[Document]


class DocumentText(BaseModel):
    access: str
    name: str
    content: str


class NewDocumentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    access: Literal["public", "restricted"] = "public"
    content: str = Field(min_length=1)


class AuditEvent(BaseModel):
    id: int
    at: float
    actor: str | None = None
    actor_type: str
    action: str
    target_type: str | None = None
    target_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    details: dict
    hash: str


class AuditOut(BaseModel):
    events: list[AuditEvent]
    next_cursor: int | None = None


class _KeyAudit:
    """kb/audit.Audit for the app's own endpoints reused here (documents): their
    events are written as done with the key."""

    def __init__(self, audit, principal):
        self.audit, self.principal = audit, principal

    def record(self, target_type=None, target_id=None, details=None, **_):
        return self.principal.record(self.audit, target_type, target_id, details)


# ---- Answers -------------------------------------------------------------------------
@router.post("/ask", response_model=AskOut, tags=["Answers"],
             responses={200: {"content": {"text/event-stream": {}}}})
async def ask(body: AskIn, p=api_key("ask"), audit=audited("api.ask")):
    """A cited answer from the documents the key's role may see, with the same
    guards as the web chat. A refused question is answered (state "blocked"),
    not an error."""
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please send a question.")
    user_limit(p.user, "chat")
    turn = None
    if body.conversation_id:
        if p.kind != "personal":
            raise HTTPException(400, "Only personal keys can keep conversations.")
        cid = body.conversation_id
        if cid == "new":
            cid = conversations_store.create(p.workspace, p.username, p.role)["id"]
        turn = await run_in_threadpool(conversations.start_turn, p.workspace, p.username,
                                       p.role, cid, question)
    ids = ({"conversation_id": turn["conversation"]["id"], "message_id": turn["message_id"]}
           if turn else {"conversation_id": None, "message_id": None})
    started = time.monotonic()

    def finish(done=None, failed=None, stopped=None):
        """Save the answer in its conversation, and audit the question."""
        state = done.state if done else "error" if failed else "stopped"
        if turn:
            content = (done.answer if done else f"Something went wrong: {failed.kind}"
                       if failed else stopped or "")
            conversations_store.finish_message(
                turn["message_id"], content, state,
                sources=done.sources if done else (),
                steps=[{"tool": t["tool"]} for t in done.trace] if done else (),
                max_access=done.max_access if done else "restricted",
                standalone_question=done.standalone if done else None,
                latency_ms=int((time.monotonic() - started) * 1000))
        p.record(audit, "question", ids["message_id"],
                 {"chars": len(question), "state": state,
                  "sources": len(done.sources) if done else 0,
                  **({"conversation": ids["conversation_id"]} if turn else {})})

    def run(on_event=None, cancel=None, out=None):
        return answering.ask(p.workspace, p.user, p.role, question, surface="api", turn=turn,
                             on_event=on_event, cancel=cancel, out=out)

    if body.stream:
        return _stream(run, finish, ids)
    try:
        done = await run_in_threadpool(run)
    except answering.Failed as e:
        await run_in_threadpool(finish, failed=e)
        raise HTTPException(502, f"The assistant couldn't answer just now ({e.kind}). "
                                 "Please try again in a moment.")
    await run_in_threadpool(finish, done=done)
    return {"answer": done.answer, "sources": done.sources, "state": done.state, **ids}


def _stream(run, finish, ids):
    """Answer as Server-Sent Events, from a worker thread; the work stops when
    the caller goes away (what was written so far is saved as stopped)."""
    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    stop = threading.Event()
    seen = {"text": ""}

    def put(event):
        try:
            loop.call_soon_threadsafe(events.put_nowait, event)
        except RuntimeError:                      # the event loop has shut down
            pass

    def on_event(event):
        if event["type"] == "answer":
            seen["text"] = event["text"]
        put(event)

    def work():
        try:
            done = run(on_event=on_event, cancel=stop, out={})
            finish(done=done)
            put({"type": "done", "answer": done.answer, "sources": done.sources,
                 "state": done.state, **ids})
        except answering.Cancelled:
            finish(stopped=seen["text"])
        except answering.Failed as e:
            finish(failed=e)
            put({"type": "error", "message": f"Something went wrong: {e.kind}"})
        except Exception:                         # e.g. saving it: still end the stream
            put({"type": "error", "message": "Something went wrong."})
        finally:
            put(None)

    threading.Thread(target=work, daemon=True).start()

    async def sse():
        try:
            while (event := await events.get()) is not None:
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            stop.set()

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/search", response_model=SearchOut, tags=["Answers"])
def search(body: SearchIn, p=api_key("search"), audit=audited("api.search")):
    """The passages a query finds, best first: only ones the key's role may see,
    and no answer written (no LLM cost)."""
    try:
        results = retrieval.search(p.workspace, p.role, body.query.strip(), body.k)
    except retrieval.Refused as e:
        raise HTTPException(400, str(e))
    p.record(audit, "search", None, {"chars": len(body.query), "k": body.k,
                                     "results": len(results)})
    return {"results": results}


@router.get("/sources", response_model=SourcesOut, tags=["Answers"])
def sources(p=api_key("search"), audit=audited("api.sources.list")):
    """Everything the assistant can search that the key's role may see: uploaded
    files and files synced from connected tools."""
    items = knowledge_router.knowledge(user=p.user, role=p.role)["items"]
    p.record(audit, "sources", None, {"count": len(items)})
    return {"sources": items}


# ---- Documents (uploads) -------------------------------------------------------------
@router.get("/documents", response_model=DocumentsOut, tags=["Documents"])
def list_documents(p=api_key("docs:read"), audit=audited("api.documents.list")):
    """The workspace's uploaded documents the key's role may see (restricted ones
    for HR / Admin only). Files synced by connectors are under /sources."""
    root = workspaces.docs_root(p.workspace)
    found = [{"access": d["access"], "name": d["name"], "size": d["size"],
              "modified": os.path.getmtime(d["path"])}
             for d in docs_store.list_docs(root)
             if d["access"] == "public" or p.role == "hr"]
    p.record(audit, "documents", None, {"count": len(found)})
    return {"documents": found}


@router.get("/documents/{access}/{name}", tags=["Documents"],
            responses={200: {"model": DocumentText,
                             "description": "A text document as JSON; any other file as it is."}})
def read_document(access: Literal["public", "restricted"], name: str = Path(pattern=NAME),
                  p=api_key("docs:read"), audit=audited("api.document.read")):
    """One uploaded document: a .txt as {access, name, content}, any other file as
    it is. A restricted one is only there for HR / Admin."""
    if access == "restricted" and p.role != "hr":
        raise HTTPException(404, "Document not found.")
    found = docs_router.read_doc(access, name, user=p.user)
    p.record(audit, "doc", f"{access}/{name}")
    return found


@router.post("/documents", response_model=DocumentText, tags=["Documents"])
def create_document(body: NewDocumentIn, p=api_key("docs:write"), audit=audited("doc.create")):
    """A new text document; it's indexed in the background."""
    made = docs_router.create_doc(docs_router.NewDocIn(**body.model_dump()), user=p.user,
                                  audit=_KeyAudit(audit, p))
    return {**made, "content": body.content}


@router.post("/documents/upload", tags=["Documents"])
def upload_documents(files: list[UploadFile] = File(...), access: str = Form("public"),
                     p=api_key("docs:write"), audit=audited("doc.upload")):
    """Text, PDF, Word, PowerPoint, Excel, Markdown or HTML files -> {saved: names};
    they're indexed in the background."""
    return docs_router.upload_docs(files=files, access=access, user=p.user,
                                   audit=_KeyAudit(audit, p))


@router.delete("/documents/{access}/{name}", tags=["Documents"])
def delete_document(access: Literal["public", "restricted"], name: str = Path(pattern=NAME),
                    p=api_key("docs:write"), audit=audited("doc.delete")):
    return docs_router.delete_doc(access, name, user=p.user, audit=_KeyAudit(audit, p))


# ---- Audit ---------------------------------------------------------------------------
@router.get("/audit", response_model=AuditOut, tags=["Audit"])
def audit_log(filters: Filters = Depends(), cursor: int | None = None,
              limit: int = Query(audit_store.PAGE, ge=1, le=audit_store.MAX_PAGE),
              p=api_key("audit:read"), audit=audited("api.audit.read")):
    """The workspace's audit log, newest first (an admin's key). Pass next_cursor
    back as cursor for the next page."""
    events, next_cursor = audit_store.list_events(p.workspace, cursor=cursor, limit=limit,
                                                  **filters.kw)
    p.record(audit, "audit", p.workspace, {"count": len(events)})
    return {"events": events, "next_cursor": next_cursor}


# ---- The OpenAPI document ------------------------------------------------------------------
_schema = {}


@router.get("/openapi.json", include_in_schema=False)
def openapi():
    """This API's own OpenAPI document: only /api/v1, whether or not the app's
    own API pages are on (settings.api_docs_on)."""
    if not _schema:
        _schema.update(get_openapi(
            title="Knowledge Assistant API", version=VERSION,
            summary="Ask the knowledge base and manage its documents with an API key.",
            description=__doc__.split("\n\n")[1].replace("\n", " "),
            routes=[r for r in router.routes if getattr(r, "include_in_schema", True)]))
    return JSONResponse(_schema)
