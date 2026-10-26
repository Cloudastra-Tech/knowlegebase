"""
Retrieval: the BM25 tokenizer, HyDE feeding only the meaning search, no LLM
rerank, the golden-set retrieval gate, and the LEAK SUITE — a public user's
searches must never reach a restricted document's chunks.

Everything runs on eval/corpus/ indexed by the real ingest.main and searched by
the real rag.load_chain, with a deterministic stand-in embedding (no OpenAI).
"""

import json

import pytest
from langchain_core.messages import AIMessage

import rag
from eval import harness


@pytest.fixture(scope="module")
def index():
    with harness.fixture_index():
        yield


@pytest.fixture(scope="module")
def golden():
    return harness.load_golden()


class FakeLLM:
    """Records every prompt. HyDE prompts get `hyde` back, anything else a
    fixed answer."""

    def __init__(self, hyde="a guess"):
        self.hyde = hyde
        self.prompts = []

    def invoke(self, prompt):
        text = prompt if isinstance(prompt, str) else "\n".join(m.content for m in prompt)
        self.prompts.append(text)
        return AIMessage(content=self.hyde if "Hypothetical answer:" in text else "An answer.")


@pytest.fixture
def no_guards(monkeypatch):
    # The public salary guard asks OpenAI; these tests are about retrieval, so
    # the guards pass everything (the leak tests then prove filtering ALONE
    # keeps restricted text out).
    monkeypatch.setattr(rag, "check_question", lambda question, allow: (True, ""))
    monkeypatch.setattr(rag, "check_answer", lambda answer, llm, allow: answer)


# ---- Tokenizer --------------------------------------------------------------
@pytest.mark.parametrize("text, words", [
    ("What is the refund policy?", ["what", "is", "the", "refund", "policy"]),
    ("Refund REFUND refund.", ["refund", "refund", "refund"]),
    ("ＰＲＯ plan", ["pro", "plan"]),                       # full-width -> ASCII
    ("Straße", ["strasse"]),                                # case-folded
    ("रिफंड नीति क्या है?", ["रिफंड", "नीति", "क्या", "है"]),   # vowel signs stay in the word
    ("Costs 2,000 rupees", ["costs", "2000", "rupees"]),
    ("PE-101: plan 2 a", ["pe", "101", "plan", "2"]),       # 1-letter words dropped, digits kept
    ("", []),
])
def test_tokenize(text, words):
    assert rag.tokenize(text) == words


def test_keyword_search_ignores_case_and_punctuation(index):
    retriever = rag.load_chain("public", harness.COLLECTION)[0]
    for query in ("WIREGUARD?", "wireguard", "WireGuard."):
        top = retriever.bm25.invoke(query)[0]
        assert "WireGuard" in top.page_content, query


# ---- HyDE and rerank --------------------------------------------------------
class _Recorder:
    def __init__(self):
        self.queries = []

    def invoke(self, query):
        self.queries.append(query)
        return []


def test_hyde_feeds_only_the_meaning_search(monkeypatch, no_guards):
    monkeypatch.setattr(rag, "USE_HYDE", True)
    dense, keyword = _Recorder(), _Recorder()
    rag.answer_question("refund policy?", rag.HybridRetriever(dense, keyword),
                        FakeLLM(hyde="Refunds take 30 days."), role="hr")
    assert keyword.queries == ["refund policy?"]
    assert dense.queries == ["refund policy?\nRefunds take 30 days."]


def test_no_llm_rerank_one_generation_call(monkeypatch, no_guards, index):
    monkeypatch.setattr(rag, "USE_HYDE", False)
    retriever = rag.load_chain("public", harness.COLLECTION)[0]
    llm = FakeLLM()
    answer, docs = rag.answer_question("What is the refund policy?", retriever, llm)
    assert answer == "An answer."
    assert len(llm.prompts) == 1                       # the answer, nothing else
    assert any("full refund within 30 days" in d.page_content for d in docs)


def test_a_plugged_in_reranker_orders_the_context(monkeypatch, no_guards, index):
    monkeypatch.setattr(rag, "USE_HYDE", False)
    seen = []
    monkeypatch.setattr(rag, "RERANKER", lambda q, docs: seen.append(q) or docs[::-1])
    retriever = rag.load_chain("public", harness.COLLECTION)[0]
    fused = retriever.invoke("refund policy")
    _, docs = rag.answer_question("refund policy", retriever, FakeLLM())
    assert seen == ["refund policy"]
    assert docs[:len(fused)] == fused[::-1]


# ---- Golden set and retrieval gate -----------------------------------------
def test_golden_rows_point_at_real_evidence(golden):
    for row in golden:
        for source in row["sources"]:
            with open(f"{harness.CORPUS}/{source}", encoding="utf-8") as f:
                text = harness._norm(f.read())
            assert any(harness._norm(e) in text for e in row.get("evidence", [])), row["id"]


def test_retrieval_meets_the_baseline(index, golden):
    report = harness.retrieval_report(golden)
    with open(harness.BASELINE, encoding="utf-8") as f:
        baseline = json.load(f)
    assert harness.gate(report["summary"], [], baseline) == []


# ---- Leak suite (a hard CI gate) -------------------------------------------
def test_public_never_retrieves_restricted_chunks(index, golden):
    assert harness.leak_report(golden) == []


def test_the_leak_suite_catches_a_broken_filter(monkeypatch, index, golden):
    # Guard against a vacuous suite: let "public" see everything, and it must
    # report leaks from every restricted document.
    monkeypatch.setitem(rag.ROLE_ACCESS, "public", ["public", "restricted"])
    leaks = harness.leak_report(golden)
    assert {leak["source"] for leak in leaks} == set(harness.restricted_lines())


def test_restricted_text_never_reaches_a_public_prompt(monkeypatch, no_guards, index):
    # Worst case: the question quotes a restricted document and HyDE "guesses"
    # its full text. Neither may pull its chunks into the answer prompt.
    monkeypatch.setattr(rag, "USE_HYDE", True)
    retriever = rag.load_chain("public", harness.COLLECTION)[0]
    restricted = harness.restricted_lines()
    for source, lines in restricted.items():
        with open(f"{harness.CORPUS}/{source}", encoding="utf-8") as f:
            full_text = f.read()
        llm = FakeLLM(hyde=full_text)
        for line in lines:
            _, docs = rag.answer_question(line, retriever, llm)
            assert harness.leaked(docs, restricted) == [], line
        contexts = [harness._norm(p.split("Context:\n", 1)[1].rsplit("\n\nQuestion:", 1)[0])
                    for p in llm.prompts if "Hypothetical answer:" not in p]
        assert contexts and not any(line in c for c in contexts for line in lines), source


def test_hr_can_retrieve_restricted_chunks(index):
    # The other half of the access rule: HR does reach them.
    retriever = rag.load_chain("hr", harness.COLLECTION)[0]
    docs = retriever.invoke("When does the Brightlearn contract end?")
    assert "restricted/vendor_contracts.txt" in [harness.source_of(d) for d in docs]


# ---- evaluate.py (the keyed eval never runs in CI; keep it from rotting) -----
def test_evaluate_scores_a_run_through_the_agent_interface(monkeypatch, index, golden):
    import evaluate

    monkeypatch.setattr(evaluate, "judge", lambda row, answer, context: {
        "correctness": 9, "groundedness": 10 if context else 0, "behaviour_ok": True,
        "reason": "stub"})
    retriever = rag.load_chain("public", harness.COLLECTION)[0]

    def agent(question, with_sources=False):           # build_agent(...).run's interface
        retriever.invoke(question)                     # the search the probe records
        return "A full refund within 30 days.", [], [{"title": "handbook.txt",
                                                      "provider": "Company documents"}]

    row = next(r for r in golden if r["id"] == "refund-window")
    with evaluate._Probe().installed() as probe:
        item = evaluate.evaluate_row(row, agent, probe)
    assert item["retrieval"]["hit"] and item["citation_hit"] and item["must_include_ok"]
    assert item["leaks"] == [] and item["forbidden_found"] == []
    assert item["judge"]["groundedness"] == 10              # the probe captured the chunks
    summary = evaluate.summarize([item])
    assert summary["retrieval_hit_rate"] == 1.0 and summary["leaks"] == 0
