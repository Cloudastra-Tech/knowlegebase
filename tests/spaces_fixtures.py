"""
Shared by test_spaces.py and test_attachments.py: the real agent over a real index
(test_indexing.env: real Chroma, stand-in embeddings) with a model that cites every
passage it is shown, whatever block it came in, so what the tests see cited is
exactly what the search let the model read.
"""

import json
import re

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

BLOCK = re.compile(r'^<untrusted_data source="(\w+)" ref="S(\d+)"[^>\n]*>\n(.*?)\n</untrusted_data>$',
                   re.S | re.M)


class Allow:
    """The guards' classifier and the RAG helper model: allow everything."""

    def invoke(self, prompt):
        return AIMessage(content="ALLOW")


class Citing(BaseChatModel):
    """The agent's model: the first line of every passage it was shown, cited.
    record["seen"] gets each prompt it was given."""

    record: dict = None

    @property
    def _llm_type(self):
        return "citing-every-block"

    def bind_tools(self, tools, **kwargs):
        return self

    def _answer(self, messages):
        self.record.setdefault("seen", []).append(messages)
        lines = []
        for m in messages:
            if isinstance(m, ToolMessage) and m.name == "search_docs":
                for source, n, text in BLOCK.findall(m.content):
                    first = next((ln for ln in text.splitlines()[1:] if ln.strip()), text)
                    lines.append(f"{first.strip()} [S{n}]")
        return "\n\n".join(lines) or "I couldn't find that in the documents."

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = "".join(c.message.content for c in self._stream(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        for piece in re.findall(r"\S+\s*|\n+", self._answer(messages)):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk


@pytest.fixture
def citing(monkeypatch):
    import agent
    import rag
    import safety
    from kb import state
    record = {}
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: Citing(record=record))
    monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: Allow())
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Allow())
    monkeypatch.setattr(rag, "USE_HYDE", False)
    state.agents.clear()
    yield record
    state.agents.clear()


def ask(client, headers, question, cid):
    """Stream one question in a conversation: its `done` event."""
    r = client.post("/api/chat/stream", json={"question": question, "conversation_id": cid},
                    headers=headers)
    assert r.status_code == 200, r.text
    events = [json.loads(c[len("data: "):]) for c in r.text.split("\n\n") if c]
    assert events[-1]["type"] == "done", events[-1]
    return events[-1]


def new_chat(client, headers, space_id=None):
    r = client.post("/api/conversations", json={"space_id": space_id} if space_id else {},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]
