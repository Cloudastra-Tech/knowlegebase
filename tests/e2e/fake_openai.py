"""
A stand-in for the OpenAI API, for the end-to-end tests (tests/e2e/server.py):
the app talks to it through OPENAI_BASE_URL, so no key and no network are needed.

    /v1/embeddings          a bag-of-words vector per text (64 dimensions, hashed
                            words): texts sharing words are close, so search works
    /v1/chat/completions    answers by who is asking (the prompt's first words):
        the agent (a request with tools)  one sentence from each of the first
                                          three passages search_docs gave it,
                                          each cited [S#]; a greeting if none
        the guards                        ALLOW (the salary check: BLOCK when the
                                          question says "earn")
        the question rewrite              the question, unchanged
        HyDE                              a fixed guess
        a question saying "calculate 2*3"  the calculator tool first, then the
                                          cited answer
Streams when asked to, a word at a time. A question that says "in full detail" gets
a long answer (the same sentences, in several paragraphs), streamed slowly, so the
chat's scrolling can be watched while it's written (frontend/e2e/chat-scroll.spec.ts).

FAKE_OPENAI_DELAY=<seconds>: answer that much slower, a word at a time (and the
searches and checks a few times that), to watch the chat's steps and streaming by
eye. Unset (the tests), it answers at once.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import struct

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
DIM = 64
# A passage as the agent's search_docs tool hands it to the model (untrusted.py):
# a document's, or a file attached to the chat's (source "attached_file").
PASSAGE = re.compile(r'<untrusted_data source="(?:company_documents|attached_file)" ref="S(\d+)"'
                     r'[^>\n]*>\n'
                     r'(.*?)\n</untrusted_data>', re.S)


def delay(times=1):
    return float(os.getenv("FAKE_OPENAI_DELAY") or 0) * times


def vector(item):
    words = (re.findall(r"[a-z0-9]+", item.lower()) if isinstance(item, str)
             else [str(t) for t in item])           # token ids
    v = [0.0] * DIM
    for w in words:
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / norm for x in v]


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    body = await req.json()
    await asyncio.sleep(delay(6))                   # a search that takes a moment
    items = body["input"] if isinstance(body["input"], list) else [body["input"]]
    if items and isinstance(items[0], int):          # one text as token ids
        items = [items]
    data = []
    for i, item in enumerate(items):
        v = vector(item)
        if body.get("encoding_format") == "base64":
            v = base64.b64encode(struct.pack(f"{DIM}f", *v)).decode()
        data.append({"object": "embedding", "index": i, "embedding": v})
    return {"object": "list", "data": data, "model": body.get("model"),
            "usage": {"prompt_tokens": 1, "total_tokens": 1}}


def text_of(message):
    content = message.get("content") or ""
    return content if isinstance(content, str) else " ".join(p.get("text", "") for p in content)


def cited_answer(messages):
    passages = []
    for m in messages:
        if m["role"] == "tool":
            passages += PASSAGE.findall(text_of(m))
    if not passages:
        return "Hello! Ask me about the documents."
    sentences = []
    for n, text in passages[:3]:
        # The passage's first line is its heading; take its first real sentence.
        lines = [line.strip() for line in text.splitlines()[1:]
                 if line.strip() and not line.strip().endswith((":", "?"))
                 and not line.startswith("#") and len(line.split()) > 4]
        first = re.split(r"(?<=[.!?])\s", lines[0])[0] if lines else text[:80]
        sentences.append(f"{first.rstrip('.')} [S{n}].")
    return " ".join(sentences)


def calculation(messages):
    """The expression to work out first, when the question asks to calculate and
    the calculator hasn't been called yet."""
    if any(tc.get("function", {}).get("name") == "calculator"
           for m in messages if m["role"] == "assistant" for tc in m.get("tool_calls") or []):
        return None
    question = next((text_of(m) for m in reversed(messages) if m["role"] == "user"), "")
    found = re.search(r"calculate\s+([\d.+\-*/() ]+)", question, re.I)
    return found.group(1).strip() if found else None


def reply_for(body):
    messages = body["messages"]
    first = text_of(messages[0])
    if body.get("tools"):
        return cited_answer(messages)
    if first.startswith("You are an intent classifier"):
        question = first.split('Question: "', 1)[1].split('"', 1)[0]
        return "BLOCK" if "earn" in question.lower() else "ALLOW"
    if first.startswith(("You detect prompt-injection", "You check whether text reveals")):
        return "ALLOW"
    if first.startswith("Rewrite the user's last question"):
        return first.rsplit("Last question: ", 1)[1].split("\n\n", 1)[0]
    if first.startswith("Write a short, factual"):
        return "A guess."
    # Digests and drafts (kb/services/summaries.py).
    if first.startswith("You summarise one"):
        title = re.search(r'title="([^"]*)"', first)
        return f"A short summary of {title.group(1) if title else 'the document'}."
    if first.startswith("You write an overview"):
        refs = sorted(set(re.findall(r'ref="(\d+)"', first)), key=int)
        cites = "".join(f"[{n}]" for n in refs[:3])
        return ("Overview of the collection\nThese documents set out the company's policies "
                f"{cites}.\n" + "\n".join(f"- Point from document {n} [{n}]." for n in refs))
    if first.startswith("You draft a new"):
        return ("Draft answer\nWhat the passages say [1].\n"
                "TO CONFIRM: who owns this policy.")
    return "ok"


def _chunk(model, delta, finish=None):
    return "data: " + json.dumps({
        "id": "fake", "object": "chat.completion.chunk", "created": 0, "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


DETAIL = "in full detail"     # the question that makes a long, slow answer
DETAIL_PARAGRAPHS = 10
DETAIL_WORD_DELAY = 0.02      # seconds a word


def in_detail(body):
    return bool(body.get("tools")) and any(
        m["role"] == "user" and DETAIL in text_of(m).lower() for m in body["messages"])


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    model = body.get("model", "fake")
    expression = calculation(body["messages"]) if body.get("tools") else None
    if expression:
        return await tool_call(model, "calculator", {"expression": expression}, body.get("stream"))
    text = reply_for(body)
    slow = in_detail(body)
    if slow:
        text = "\n\n".join([text] * DETAIL_PARAGRAPHS)
    if not body.get("stream"):
        await asyncio.sleep(delay(4))               # the guards and the rewrite
        return JSONResponse({"id": "fake", "object": "chat.completion", "created": 0,
                             "model": model,
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": text}}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                       "total_tokens": 2}})

    async def stream():
        for word in re.findall(r"\S+\s*", text):
            await asyncio.sleep(delay())
            yield _chunk(model, {"role": "assistant", "content": word})
            if slow:
                await asyncio.sleep(DETAIL_WORD_DELAY)
        yield _chunk(model, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def tool_call(model, name, args, stream):
    """The model asking for a tool, as OpenAI words it (streamed, or whole)."""
    await asyncio.sleep(delay(4))
    call = {"id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}
    if not stream:
        return JSONResponse({"id": "fake", "object": "chat.completion", "created": 0,
                             "model": model,
                             "choices": [{"index": 0, "finish_reason": "tool_calls",
                                          "message": {"role": "assistant", "content": None,
                                                      "tool_calls": [call]}}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                       "total_tokens": 2}})

    def chunks():
        yield _chunk(model, {"role": "assistant", "content": None,
                             "tool_calls": [{"index": 0, **call}]})
        yield _chunk(model, {}, "tool_calls")
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")


# A webhook receiver (kb/services/webhooks.py), for trying webhooks against this
# server by hand: CONNECTOR_ALLOW_PRIVATE_HOSTS=127.0.0.1:<this port>.
@app.post("/webhook-receiver")
async def webhook_receiver(req: Request):
    await req.body()
    return {"ok": True}
