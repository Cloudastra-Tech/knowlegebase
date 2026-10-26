"""
Grounded answers with passage-level citations (pipeline.md P0-5, chat.md C8, C13,
C14): every chunk carries where it sits in its document, search_docs hands the
model numbered passages, the model writes ONE answer citing them as [S#], and the
server keeps only the markers that point at a passage read for that question
which the asker may see. A cited passage opens through /api/passages, checked
against the asker's role every time.

The model is a fake that cites whatever it is shown, plus markers it was never
given; the index is eval/corpus/ built by the real ingest (stand-in embedding).
So what these tests prove is the server's checks, not the model's manners.
"""

import os
import re

import pytest
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

import agent
import ingest
import rag
import safety
from agent import build_agent
from eval import harness
from kb.services import conversations
from test_chat import PASSAGE
from test_indexing import env  # noqa: F401  (a fixture)


# ---- cite(): which markers survive -------------------------------------------
def _doc(access="public"):
    return Document(page_content="text", metadata={"access": access})


READ = {1: _doc(), 2: _doc(), 3: _doc("restricted")}


def test_markers_are_renumbered_in_order_of_first_use():
    text, used, dropped = agent.cite("B is so [S2]. A is so [S1]. B again [S2].", READ, ["public"])
    assert text == "B is so [S1]. A is so [S2]. B again [S1]."
    assert (used, dropped) == ([2, 1], 0)


def test_markers_for_passages_never_read_are_removed():
    text, used, dropped = agent.cite("Made up [S9]. Also [S0]. Real [S1].", READ, ["public"])
    assert (text, used, dropped) == ("Made up. Also. Real [S1].", [1], 2)


def test_a_restricted_passage_is_never_cited_to_a_public_asker():
    assert agent.cite("Pay is high [S3].", READ, ["public"])[:2] == ("Pay is high.", [])
    assert agent.cite("Pay is high [S3].", READ, ["public", "restricted"])[:2] == (
        "Pay is high [S1].", [3])


def test_grouped_and_repeated_markers():
    assert agent.cite("X [S1, S2] and Y [S2;S9].", READ, ["public"])[0] == "X [S1][S2] and Y [S2]."
    assert agent.cite("X [S1][S1].", READ, ["public"])[0] == "X [S1]."


def test_a_marker_still_being_written_is_held_back():
    for partial in ("Refunds take 30 days [", "Refunds take 30 days [S", "Refunds take 30 days [S1"):
        assert agent.cite(partial, READ, ["public"], partial=True)[0] == "Refunds take 30 days "
    assert agent.cite("Refunds [S1", READ, ["public"])[0] == "Refunds [S1"   # finished: as is


def test_markers_from_an_earlier_answer_are_stripped():
    assert agent.strip_markers("Refunds take 30 days [S1][S4].") == "Refunds take 30 days."


# ---- Passage metadata (C8) ------------------------------------------------------
@pytest.fixture(scope="module")
def corpus_index():
    with harness.fixture_index(offline=True):
        yield


def _chunks(collection=harness.COLLECTION):
    got = ingest._store(collection)._collection.get(include=["documents", "metadatas"])
    return list(zip(got["ids"], got["documents"], got["metadatas"]))


def test_every_chunk_knows_where_it_sits(corpus_index):
    chunks = _chunks()
    assert chunks
    docs = {}
    for cid, text, meta in chunks:
        assert meta["chunk_id"] == cid and meta["chunk_meta"] == ingest.CHUNK_META
        docs.setdefault(meta["doc_key"], set()).add(meta["doc_id"])
        assert meta["title"] == os.path.basename(meta["doc_key"])
        assert meta["provider"] == "Company documents" and meta["modified"]
        with open(os.path.join(harness.CORPUS, meta["doc_key"]), encoding="utf-8") as f:
            source = f.read()
        # char_start / char_end are the passage's place in the extracted text.
        assert source[meta["char_start"]:meta["char_end"]] == rag.passage_text(text, meta)
    assert all(len(ids) == 1 for ids in docs.values())                # one doc_id each
    assert len({next(iter(ids)) for ids in docs.values()}) == len(docs)


def test_a_chunk_knows_the_heading_it_is_under(corpus_index):
    handbook = sorted((m["char_start"], m.get("heading_path")) for _, _, m in _chunks()
                      if m["doc_key"] == "public/handbook.txt")
    # The first chunk starts with the title, before any heading: its own first one.
    assert handbook[0] == (0, "About us")
    assert "Support hours" in [path for _, path in handbook]


def test_headings_nest():
    text = "# Handbook\n\nIntro.\n\n## Leave\nTwenty days.\n\n### Sick leave\nTen days.\n\n## Pay\nMonthly."
    headings = ingest._headings(text)
    assert ingest.heading_path(headings, text.index("Intro")) == "Handbook"
    assert ingest.heading_path(headings, text.index("Ten days")) == "Handbook › Leave › Sick leave"
    assert ingest.heading_path(headings, text.index("Monthly")) == "Handbook › Pay"
    assert ingest.heading_path(ingest._headings("Plain text, no headings."), 5) == ""


def test_an_index_from_before_gets_the_metadata_without_embedding_again(monkeypatch, tmp_path):
    from chromadb.api.client import SharedSystemClient

    embedded = []

    class Counting(harness.HashEmbeddings):
        def embed_documents(self, texts):
            embedded.append(len(texts))
            return super().embed_documents(texts)

    monkeypatch.setattr(ingest, "OpenAIEmbeddings", lambda **kwargs: Counting(dim=64))
    monkeypatch.setattr(ingest, "DB_FOLDER", str(tmp_path / "chroma"))
    folder = tmp_path / "docs" / "public"
    folder.mkdir(parents=True)
    (folder / "guide.txt").write_text("# Guide\n\n## Leave\n" + "Leave is twenty days. " * 40)

    # Built by the code before passage metadata: none of its fields.
    real = ingest.load_and_split
    new_fields = ("chunk_id", "doc_id", "chunk_meta", "char_start", "char_end",
                  "heading_path", "title", "provider", "modified")

    def old_split(*args):
        return [(cid, text, {k: v for k, v in meta.items() if k not in new_fields})
                for cid, text, meta in real(*args)]
    monkeypatch.setattr(ingest, "load_and_split", old_split)
    try:
        ingest.main(docs_folder=str(tmp_path / "docs"), collection="old")
        before = {cid for cid, _, _ in _chunks("old")}
        assert before and all("chunk_meta" not in m for _, _, m in _chunks("old"))
        calls = len(embedded)

        monkeypatch.setattr(ingest, "load_and_split", real)
        result = ingest.update(docs_folder=str(tmp_path / "docs"), collection="old")
        assert result["embedded"] == 0 and len(embedded) == calls          # nothing embedded
        after = _chunks("old")
        assert {cid for cid, _, _ in after} == before                       # the same chunks
        assert all(m["chunk_meta"] == ingest.CHUNK_META and m["chunk_id"] == cid
                   and m["heading_path"].startswith("Guide") for cid, _, m in after)
        assert "Guide › Leave" in {m["heading_path"] for _, _, m in after}
        # ...and the next update finds nothing to do.
        assert ingest.update(docs_folder=str(tmp_path / "docs"), collection="old")["changed"] == 0
    finally:
        SharedSystemClient.clear_system_cache()


# ---- One generation, cited: through the agent ---------------------------------------
class Allow:
    """The guards' classifier and the RAG helper model: allow everything."""

    def invoke(self, prompt):
        return AIMessage(content="ALLOW")


class CitingModel(BaseChatModel):
    """The agent's model: one sentence from every passage it was shown, cited, in
    REVERSE order of the numbers; plus a marker for a passage it never saw.
    script["reply"] overrides the answer."""

    script: dict = None

    @property
    def _llm_type(self):
        return "citing-fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _answer(self, messages):
        if self.script.get("reply"):
            return self.script["reply"]
        passages = [(int(n), text) for m in messages
                    if isinstance(m, ToolMessage) and m.name == "search_docs"
                    for n, text in PASSAGE.findall(m.content)]
        lines = []
        for n, text in sorted(dict(passages).items(), reverse=True):
            first = next((line for line in text.splitlines()[1:] if line.strip()), "")
            lines.append(f"{first.strip()} [S{n}]")
        return "\n\n".join(lines + ["And something it never read [S99]."])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = "".join(c.message.content for c in self._stream(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        for piece in re.findall(r"\S+\s*", self._answer(messages)):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk


@pytest.fixture
def citing(monkeypatch, corpus_index):
    script = {}
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: CitingModel(script=script))
    monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: Allow())
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Allow())
    monkeypatch.setattr(rag, "USE_HYDE", False)
    return script


def _markers(text):
    return [int(n) for n in re.findall(r"\[S(\d+)\]", text)]


def test_the_answer_cites_passages_in_order_with_their_place(citing):
    events = []
    answer, trace, sources = build_agent("public", harness.COLLECTION, outline=False)(
        "What is the refund policy?", with_sources=True, on_event=events.append)

    # Kept markers read 1, 2, 3, ... in order; the invented one is gone.
    assert "S99" not in answer
    first_use = list(dict.fromkeys(_markers(answer)))
    assert first_use == list(range(1, len(first_use) + 1)) and len(first_use) >= 2
    # The sources list follows the markers, one entry per cited passage.
    assert [s["n"] for s in sources] == first_use
    by_n = {s["n"]: s for s in sources}
    for n in first_use:
        sentence = answer.split(f"[S{n}]")[0].rsplit("\n", 1)[-1].strip()
        assert sentence and sentence in by_n[n]["snippet"]
    refund = next(s for s in sources if "full refund within 30 days" in s["snippet"])
    assert refund["title"] == "handbook.txt" and refund["heading_path"] == "About us"
    assert refund["provider"] == "Company documents" and refund["url"] is None
    assert refund["access"] == "public" and refund["chunk_id"] and refund["doc_id"]
    assert isinstance(refund["char_start"], int) and refund["char_end"] > refund["char_start"]
    # Why this source (C14): its rank among the matches, and the words it shares.
    assert refund["why"]["rank"] >= 1 and refund["why"]["of"] >= refund["why"]["rank"]
    assert "refund" in refund["why"]["terms"] and "the" not in refund["why"]["terms"]
    # Live: the invented marker never reached the screen, and the sources came along.
    streamed = [e for e in events if e["type"] == "answer"]
    assert streamed and not any("S99" in e["text"] for e in streamed)
    assert [e for e in streamed if "sources" in e][-1]["sources"] == sources
    # The steps panel shows the passages as plain text, numbered, without the blocks.
    (search,) = [t for t in trace if t["tool"] == "search_docs"]
    assert search["output"].startswith("[S1] ") and "<untrusted_data" not in search["output"]


def test_a_public_asker_never_gets_a_restricted_citation(citing):
    """The leak suite, for citations: every golden question, and every line of a
    restricted document typed back, asked as a public user with a model that
    cites everything it is shown."""
    golden = harness.load_golden()
    restricted = harness.restricted_lines()
    run = build_agent("public", harness.COLLECTION, outline=False)
    secret_titles = {os.path.basename(k) for k in restricted}
    for question in harness.leak_queries(golden, restricted):
        answer, _, sources = run(question, with_sources=True)
        cited_text = harness._norm(" ".join(s.get("snippet", "") for s in sources) + answer)
        assert not [s for s in sources if s.get("access") != "public"
                    or s["title"] in secret_titles], question
        assert not any(line in cited_text for lines in restricted.values() for line in lines), \
            question
        assert max(_markers(answer), default=0) <= len(sources), question


def test_citations_hold_even_if_retrieval_lets_restricted_text_through(citing, monkeypatch):
    """Defence in depth: should the search ever hand a public asker restricted
    chunks, their markers are still removed (and the answer is marked restricted,
    so conversations.visible_to hides it)."""
    real = agent.load_chain
    monkeypatch.setattr(agent, "load_chain", lambda role, collection: real("hr", collection))
    out = {}
    answer, _, sources = build_agent("public", harness.COLLECTION, outline=False)(
        "When does the Brightlearn contract end?", with_sources=True, out=out)
    assert "restricted" in out["access"]                         # it was read...
    assert sources and all(s["access"] == "public" for s in sources)   # ...never cited
    assert out["dropped"] >= 2 and max(_markers(answer)) == len(sources)


def test_hr_may_cite_restricted_passages(citing):
    _, _, sources = build_agent("hr", harness.COLLECTION, outline=False)(
        "When does the Brightlearn contract end?", with_sources=True)
    assert any(s["access"] == "restricted" and s["title"] == "vendor_contracts.txt"
               for s in sources)


def test_a_not_found_answer_cites_nothing(citing):
    citing["reply"] = "I couldn't find that in the documents [S1]."
    out = {}
    answer, trace, sources = build_agent("public", harness.COLLECTION, outline=False)(
        "What is the refund policy?", with_sources=True, out=out)
    assert answer == "I couldn't find that in the documents." and sources == []
    assert conversations.outcome(answer, trace, sources, out)[:2] == ("not_found", [])


def test_follow_ups_do_not_reuse_old_markers(citing, monkeypatch):
    seen = []
    real = CitingModel._answer
    monkeypatch.setattr(CitingModel, "_answer",
                        lambda self, messages: seen.append(messages) or real(self, messages))
    history = [{"role": "user", "content": "Refunds?"},
               {"role": "assistant", "content": "Within 30 days [S1][S2]."}]
    build_agent("public", harness.COLLECTION, outline=False)(
        "What is the refund policy?", with_sources=True, history=history)
    earlier = [m.content for m in seen[-1] if isinstance(m, AIMessage) and not m.tool_calls]
    assert earlier == ["Within 30 days."]


# ---- The passage viewer's endpoint (C13) -----------------------------------------
GUIDE = "# Guide\n\n## Refunds\n" + "Refunds take thirty days. " * 30 + "\n\n## Support\nWe answer email."
PAY = "Teachers are paid sixty thousand rupees a month. " * 3


def test_a_passage_opens_for_who_may_see_it(env):  # noqa: F811
    from kb.services import indexing
    w = env.workspace(("public", "guide.txt", GUIDE), ("restricted", "pay.txt", PAY))
    other = env.workspace(("public", "other.txt", "Another company's handbook."))
    for ws in (w, other):
        indexing.schedule_index(ws)
        env.wait(ws)
    chunks = env.chunks(w)
    guide = sorted((i for i, (k, _) in chunks.items() if k == "public/guide.txt"))
    pay = next(i for i, (k, _) in chunks.items() if k == "restricted/pay.txt")
    staff, admin = env.login(w, "staff"), env.login(w)
    get = lambda cid, headers: env.client.get(f"/api/passages/{cid}", headers=headers)  # noqa: E731

    r = get(guide[0], staff)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "guide.txt" and body["provider"] == "Company documents"
    assert body["access"] == "public" and body["url"] is None and body["chunk_id"] == guide[0]
    assert body["text"] and body["text"] in GUIDE and body["heading_path"]
    # The text around it is the document's own, on either side of the passage.
    assert " ".join((body["before"] + body["text"] + body["after"]).split()) in " ".join(GUIDE.split())
    assert body["before"] or body["after"]

    # Restricted: HR / Admin only. A public asker can't tell it from a missing one.
    assert get(pay, staff).status_code == 404
    assert get(pay, {**admin, "X-Role": "public"}).status_code == 404
    assert get(pay, admin).json()["text"].startswith("Teachers are paid")
    assert get("0" * 32, staff).status_code == 404
    # Another workspace's members never reach this one's index.
    assert get(guide[0], env.login(other)).status_code == 404
    assert get("bad id!", staff).status_code in (404, 422)


# ---- evaluate.py's citation faithfulness check (runs only with a key) -------------
def test_citation_faithfulness_judges_each_cited_claim():
    import evaluate

    sources = [{"n": 1, "snippet": "Refunds are paid within 30 days."},
               {"n": 2, "snippet": "Support is open Monday to Friday."}]
    answer = ("Refunds take 30 days [S1]. Support is open on weekends [S2].\n\n"
              "A claim with no marker. And [S1][S2] both.")
    asked = []

    def judge_one(claim, passage):
        asked.append((claim, passage))
        return {"supported": claim.split()[0] in passage, "reason": "stub"}

    result = evaluate.citation_faithfulness(answer, sources, judge_one)
    assert [c for c, _ in asked] == ["Refunds take 30 days.", "Support is open on weekends.",
                                     "And both.", "And both."]
    assert result["claims"] == 4 and result["markers"] == 4
    assert result["supported"] == 2        # "Refunds" and "Support" are in their passages
