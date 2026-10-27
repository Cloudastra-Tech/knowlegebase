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
Streams when asked to, a word at a time.
"""

import base64
import hashlib
import json
import re
import struct

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
DIM = 64
# A passage as the agent's search_docs tool hands it to the model (untrusted.py).
PASSAGE = re.compile(r'<untrusted_data source="company_documents" ref="S(\d+)"[^>\n]*>\n'
                     r'(.*?)\n</untrusted_data>', re.S)


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
    return "ok"


def _chunk(model, delta, finish=None):
    return "data: " + json.dumps({
        "id": "fake", "object": "chat.completion.chunk", "created": 0, "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    text, model = reply_for(body), body.get("model", "fake")
    if not body.get("stream"):
        return JSONResponse({"id": "fake", "object": "chat.completion", "created": 0,
                             "model": model,
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": text}}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                       "total_tokens": 2}})

    def stream():
        for word in re.findall(r"\S+\s*", text):
            yield _chunk(model, {"role": "assistant", "content": word})
        yield _chunk(model, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
