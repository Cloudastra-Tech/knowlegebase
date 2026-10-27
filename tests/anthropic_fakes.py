"""
A stand-in for Anthropic's API, at the SDK's edge: anthropic.Client is replaced
by FakeAnthropic, which answers messages.create in the Messages API's own shapes
(anthropic.types, validated by the SDK's models), whole or as a stream of
events. No network, no key. `calls`: every request's payload, as sent.
"""

import json
from types import SimpleNamespace

import anthropic
import httpx2
from pydantic import TypeAdapter

import models

USAGE = {"input_tokens": 40, "output_tokens": 8, "cache_read_input_tokens": 30,
         "cache_creation_input_tokens": 0}
_EVENTS = TypeAdapter(anthropic.types.RawMessageStreamEvent)


def text_of_request(payload):
    """Every text the request carries (system, messages), joined."""
    parts = []
    system = payload.get("system") or []
    parts += [system] if isinstance(system, str) else [b.get("text", "") for b in system]
    for m in payload.get("messages") or []:
        content = m["content"]
        if isinstance(content, str):
            parts.append(content)
            continue
        for b in content:
            if b.get("type") == "text":
                parts.append(b["text"])
            elif b.get("type") == "tool_result":
                inner = b.get("content")
                parts += [inner] if isinstance(inner, str) else [
                    c.get("text", "") for c in inner or []]
    return "\n".join(parts)


def api_error(cls, status, kind, message):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx2.Response(status, request=request),
               body={"type": "error", "error": {"type": kind, "message": message}})


class FakeAnthropic:
    """What anthropic.Client(**params) returns in the tests. One per test (the
    fixture); every client made shares it."""

    def __init__(self, answer="Refunds are allowed within 30 days [S1].", tool_call=None,
                 thinking=None):
        self.calls, self.keys = [], []
        self.answer, self.tool_call, self.thinking = answer, tool_call, thinking
        self.error = None
        self.verdict = "ALLOW"           # what the guards' classifiers are told
        create = SimpleNamespace(create=self._create)
        self.messages = SimpleNamespace(with_raw_response=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(with_raw_response=create))

    def __call__(self, api_key=None, **params):
        self.keys.append(api_key)
        return self

    def reply(self, payload):
        text = text_of_request(payload)
        if "ALLOW" in text and "BLOCK" in text:          # the guards' classifiers
            return self.verdict
        if models.TEST_PROMPT in text:
            return "OK"
        return self.answer

    def _blocks(self, payload):
        # Its own tool call answered (the agent's search_docs, run in code first, isn't).
        answered = any(isinstance(m["content"], list) and any(
            b.get("type") == "tool_result" and b.get("tool_use_id") == "toolu_1"
            for b in m["content"]) for m in payload["messages"])
        blocks = []
        if self.thinking:
            blocks.append({"type": "thinking", "thinking": self.thinking, "signature": "sig-1"})
        if self.tool_call and payload.get("tools") and not answered:
            blocks.append({"type": "tool_use", "id": "toolu_1", **self.tool_call})
            return blocks, "tool_use"
        return blocks + [{"type": "text", "text": self.reply(payload)}], "end_turn"

    def _message(self, payload, content, stop):
        return anthropic.types.Message.model_validate({
            "id": "msg_1", "type": "message", "role": "assistant", "model": payload["model"],
            "content": content, "stop_reason": stop, "stop_sequence": None, "usage": USAGE})

    def _create(self, **payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        content, stop = self._blocks(payload)
        if not payload.get("stream"):
            return SimpleNamespace(parse=lambda: self._message(payload, content, stop))
        return SimpleNamespace(parse=lambda: iter(self._events(payload, content, stop)))

    def _events(self, payload, content, stop):
        events = [{"type": "message_start",
                   "message": self._message(payload, [], None).model_dump()}]
        for i, block in enumerate(content):
            if block["type"] == "text":
                events.append({"type": "content_block_start", "index": i,
                               "content_block": {"type": "text", "text": ""}})
                for word in block["text"].split(" "):
                    events.append({"type": "content_block_delta", "index": i,
                                   "delta": {"type": "text_delta", "text": word + " "}})
            elif block["type"] == "tool_use":
                events.append({"type": "content_block_start", "index": i,
                               "content_block": {**block, "input": {}}})
                events.append({"type": "content_block_delta", "index": i,
                               "delta": {"type": "input_json_delta",
                                         "partial_json": json.dumps(block["input"])}})
            elif block["type"] == "thinking":
                events.append({"type": "content_block_start", "index": i,
                               "content_block": {"type": "thinking", "thinking": "",
                                                 "signature": ""}})
                events.append({"type": "content_block_delta", "index": i,
                               "delta": {"type": "thinking_delta",
                                         "thinking": block["thinking"]}})
                events.append({"type": "content_block_delta", "index": i,
                               "delta": {"type": "signature_delta",
                                         "signature": block["signature"]}})
            events.append({"type": "content_block_stop", "index": i})
        events += [{"type": "message_delta", "delta": {"stop_reason": stop,
                                                       "stop_sequence": None},
                    "usage": USAGE},          # message_delta's usage is the whole call's
                   {"type": "message_stop"}]
        return [_EVENTS.validate_python(e) for e in events]
