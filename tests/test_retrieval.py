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
import rerankers
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


@pytest.mark.parametrize("question, vague", [
    ("refund policy?", True),                                      # short
    ("leave", True),
    ("can you tell me more about the policy please", True),       # names nothing
    ("How many days of paid annual leave do employees get?", False),
    ("Which VPN do we use when working from a cafe?", False),
])
def test_hyde_runs_only_for_short_or_vague_questions_by_default(monkeypatch, no_guards,
                                                                question, vague):
    assert rag.USE_HYDE == "auto"                                 # the default (RAG_HYDE unset)
    dense, keyword = _Recorder(), _Recorder()
    llm = FakeLLM(hyde="A guess.")
    rag.retrieve(question, rag.HybridRetriever(dense, keyword), llm, role="hr")
    assert rag.is_vague(question) is vague
    assert dense.queries == [question + "\nA guess." if vague else question]
    assert len(llm.prompts) == (1 if vague else 0)                # no call when it's specific
    assert keyword.queries == [question]


class _Variants(FakeLLM):
    def invoke(self, prompt):
        if "different search queries" in prompt:
            self.prompts.append(prompt)
            return AIMessage(content="1. annual leave days\n- paid holidays per year\n"
                                     "How many paid leaves do employees get per year?")
        return super().invoke(prompt)


def test_multi_query_adds_rewordings_to_the_meaning_search_only(monkeypatch, no_guards):
    monkeypatch.setattr(rag, "MULTI_QUERY", True)
    monkeypatch.setattr(rag, "USE_HYDE", False)
    dense, keyword = _Recorder(), _Recorder()
    question = "How many paid leaves do employees get per year?"
    rag.retrieve(question, rag.HybridRetriever(dense, keyword), _Variants(), role="hr")
    assert keyword.queries == [question]
    # The question itself, then each rewording once (the repeat of the question dropped).
    assert dense.queries == [question, "annual leave days", "paid holidays per year"]


def test_multi_query_failing_still_searches(monkeypatch, no_guards):
    class Broken(FakeLLM):
        def invoke(self, prompt):
            if "different search queries" in prompt:
                raise TimeoutError()
            return super().invoke(prompt)

    monkeypatch.setattr(rag, "MULTI_QUERY", True)
    monkeypatch.setattr(rag, "USE_HYDE", True)
    dense, keyword = _Recorder(), _Recorder()
    rag.retrieve("leave", rag.HybridRetriever(dense, keyword), Broken(hyde="G."), role="hr")
    assert dense.queries == ["leave\nG."] and keyword.queries == ["leave"]


def test_multi_query_fuses_every_meaning_search(index):
    retriever = rag.load_chain("public", harness.COLLECTION)[0]
    docs = retriever.invoke("refund", dense_queries=["refund", "money back after purchase",
                                                     "account credit"])
    assert docs and all(d.metadata["rank"] == n for n, d in enumerate(docs, 1))
    assert any("full refund within 30 days" in d.page_content for d in docs)


def test_the_model_calls_of_the_search_count_toward_the_question(monkeypatch, no_guards):
    """HyDE and multi-query run in a thread pool; their tokens still reach the
    question's counter (kb/services/question_log.counting, a context variable)."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from kb.services import question_log

    monkeypatch.setattr(rag, "MULTI_QUERY", True)
    monkeypatch.setattr(rag, "USE_HYDE", True)
    llm = FakeListChatModel(responses=["a guess", "other words"] * 2)
    with question_log.counting() as counter:
        rag.retrieve("leave", rag.HybridRetriever(_Recorder(), _Recorder()), llm, role="hr")
    assert counter.calls == 2


# ---- Freshness (P1-7) -------------------------------------------------------------
def _doc(text, modified):
    from langchain_core.documents import Document
    return Document(page_content=text, metadata={"modified": modified, "chunk_id": text})


class _Fixed:
    def __init__(self, docs):
        self.docs = docs

    def invoke(self, query):
        return list(self.docs)


def test_a_newer_document_wins_a_near_tie():
    old = _doc("Travel allowance is 150 euros a night (2024 policy).", "2024-01-10T00:00:00+00:00")
    new = _doc("Travel allowance is 180 euros a night (2026 policy).", "2026-09-01T00:00:00+00:00")
    # Both searches put the old policy one place ahead: a near tie.
    retriever = rag.HybridRetriever(_Fixed([old, new]), _Fixed([old, new]), top_k=2)
    assert [d.page_content for d in retriever.invoke("travel allowance")] == \
        [new.page_content, old.page_content]


def test_freshness_never_beats_a_clearly_better_match():
    old = _doc("Travel allowance is 150 euros a night.", "2020-01-10T00:00:00+00:00")
    middle = _doc("Travel is booked by the desk.", "2020-01-10T00:00:00+00:00")
    new = _doc("Unrelated, but changed yesterday.", "2026-09-01T00:00:00+00:00")
    # The old chunk is first in both searches, the new one two places below it.
    retriever = rag.HybridRetriever(_Fixed([old, middle, new]), _Fixed([old, middle, new]),
                                    top_k=3)
    assert [d.page_content for d in retriever.invoke("travel allowance")] == \
        [old.page_content, new.page_content, middle.page_content]


def test_freshness_is_mild_and_can_be_turned_off(monkeypatch):
    keys = ["a", "b", "c", "d"]
    metas = {"a": {"modified": "2023-01-01"}, "b": {"modified": "2023-01-01"},
             "c": {"modified": "2023-01-01"}, "d": {"modified": "2026-01-01"}}
    assert rag.prefer_recent(keys, metas) == ["a", "b", "d", "c"]          # one place up
    assert rag.prefer_recent(keys, metas, boost=0) == keys
    same = {k: {"modified": "2026-01-01"} for k in keys}
    assert rag.prefer_recent(keys, same) == keys                         # same dates: no change
    assert rag.prefer_recent(keys, {}) == keys                           # no dates: no change


def test_evidence_blocks_carry_the_updated_date_and_the_prompt_says_to_prefer_it():
    import agent
    block = agent._evidence_block(1, _doc("Allowance is 180 euros.", "2026-09-01T10:00:00+00:00"))
    assert 'updated="2026-09-01"' in block
    assert "later `updated` date" in rag.FRESHNESS_RULE
    assert rag.FRESHNESS_RULE in rag.PROMPT.messages[0].prompt.template


# ---- Model routing and prompt caching (P1-5) ----------------------------------------
def test_each_job_gets_its_model_and_the_answer_prompt_starts_with_what_never_changes(
        monkeypatch, tmp_path):
    import agent
    import safety
    from langchain_core.messages import SystemMessage
    from test_chat import Fakes, FakeAgentModel, FakeClassifier, FakeRagModel, FakeRetriever

    real_classifier = safety._get_intent_llm
    monkeypatch.setenv("ANSWER_MODEL", "gpt-answer")
    monkeypatch.setenv("FAST_MODEL", "gpt-fast")
    made, prompts = [], []
    f = Fakes()
    f.delays = {k: 0 for k in f.delays}

    class Recording(FakeAgentModel):
        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages)
            yield from super()._stream(messages, stop, run_manager, **kwargs)

    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kw: made.append(kw) or Recording(fakes=f))
    monkeypatch.setattr(agent, "load_chain", lambda role="public", collection=None: (
        FakeRetriever(f), FakeRagModel(f)))
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: FakeClassifier(f))
    run = agent.build_agent("public", "ws_acme", outline=False)
    run("What is the refund policy?")
    run("And for interns?", history=[{"role": "user", "content": "What is the refund policy?"},
                                     {"role": "assistant", "content": "30 days."}])
    (kwargs,) = made
    assert kwargs["model"] == "gpt-answer"
    assert kwargs["model_kwargs"] == {"prompt_cache_key": "kb:ws_acme:public"}
    # The same rules come first every time; the conversation and passages after them.
    first, second = prompts[0], prompts[-1]
    assert isinstance(first[0], SystemMessage) and first[0] == second[0]
    assert second[1].content == "What is the refund policy?"

    seen = {}
    monkeypatch.setattr(rag, "ChatOpenAI", lambda **kw: seen.setdefault("rag", kw))
    monkeypatch.setattr(safety, "ChatOpenAI", lambda **kw: seen.setdefault("guards", kw))
    monkeypatch.setattr(safety, "_intent_llm", None)
    monkeypatch.setattr(rag, "OpenAIEmbeddings", lambda **kw: harness.HashEmbeddings())
    monkeypatch.setattr(rag, "DB_FOLDER", str(tmp_path))
    rag.load_chain("public", "kb-routing-test")
    real_classifier()
    assert seen["rag"]["model"] == seen["guards"]["model"] == "gpt-fast"


def test_no_llm_rerank_one_generation_call(monkeypatch, no_guards, index):
    monkeypatch.setattr(rag, "USE_HYDE", False)
    retriever = rag.load_chain("public", harness.COLLECTION)[0]
    llm = FakeLLM()
    answer, docs = rag.answer_question("What is the refund policy?", retriever, llm)
    assert answer == "An answer."
    assert len(llm.prompts) == 1                       # the answer, nothing else
    assert any("full refund within 30 days" in d.page_content for d in docs)


def test_a_plugged_in_reranker_chooses_from_a_larger_pool(monkeypatch, no_guards, index):
    monkeypatch.setattr(rag, "USE_HYDE", False)
    seen = []

    def backwards(question, docs):            # a fake reranker: the fused order reversed
        seen.append((question, [d.page_content for d in docs]))
        return docs[::-1]

    monkeypatch.setattr(rag, "RERANKER", backwards)
    retriever = rag.load_chain("public", harness.COLLECTION)[0]
    assert retriever.dense.search_kwargs["k"] == retriever.bm25.k == rerankers.CANDIDATES
    _, docs = rag.answer_question("refund policy", retriever, FakeLLM())
    ((question, pool),) = seen
    assert question == "refund policy" and len(pool) > rag.TOP_K
    ranked = [d for d in docs if d.metadata.get("rank")]
    # The model reads the reranker's best KEEP, not the fusion's top 4.
    assert [d.page_content for d in ranked] == pool[::-1][:rerankers.KEEP]
    assert [d.metadata["fused_rank"] for d in ranked] == \
        list(range(len(pool), len(pool) - rerankers.KEEP, -1))
    # The leak suite holds with a reranker too: only what the role may see is a candidate.
    assert harness.leak_report(harness.load_golden()) == []


class _Scores(rerankers.Reranker):
    def __init__(self, scores=None, fail=False):
        self.given, self.fail, self.by_text = [], fail, scores or {}

    def scores(self, query, texts):
        self.given.append(texts)
        if self.fail:
            raise TimeoutError("rerank API down")
        return [self.by_text.get(t, 0.0) for t in texts]


def test_a_reranker_orders_by_score_drops_below_the_floor_and_survives_failure(monkeypatch):
    from langchain_core.documents import Document
    docs = [Document(page_content=t, metadata={"chunk_id": t}) for t in ("a", "b", "c")]
    ranked = _Scores({"a": 0.1, "b": 0.9, "c": 0.5})("q", docs)
    assert [d.page_content for d in ranked] == ["b", "c", "a"]
    assert [d.metadata["rerank_score"] for d in ranked] == [0.9, 0.5, 0.1]
    monkeypatch.setenv("RERANK_MIN_SCORE", "0.4")
    assert [d.page_content for d in _Scores({"a": 0.1, "b": 0.9, "c": 0.5})("q", docs)] == \
        ["b", "c"]
    monkeypatch.setenv("RERANK_MIN_SCORE", "0.95")          # the best one always stays
    assert [d.page_content for d in _Scores({"b": 0.9})("q", docs)] == ["b"]
    # A failing reranker (timeout, API down) leaves the fused order: the question goes on.
    assert _Scores(fail=True)("q", docs) == docs


@pytest.mark.parametrize("env, kind", [
    ({}, None), ({"RERANKER": "none"}, None),
    ({"RERANKER": "cohere"}, None),                         # no key: off, not an error
    ({"RERANKER": "cohere", "COHERE_API_KEY": "k"}, rerankers.CohereReranker),
    ({"RERANKER": "voyage", "VOYAGE_API_KEY": "k"}, rerankers.VoyageReranker),
    ({"RERANKER": "local"}, rerankers.LocalCrossEncoder),
    ({"RERANKER": "magic"}, None),
])
def test_the_reranker_comes_from_env_and_api_ones_need_their_key(monkeypatch, env, kind):
    for name in ("RERANKER", "COHERE_API_KEY", "VOYAGE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    got = rerankers.from_env()
    assert (got is None) if kind is None else isinstance(got, kind)


def test_the_api_rerankers_send_the_candidates_and_read_scores_back(monkeypatch):
    sent = []

    class Reply:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    def post(url, json, headers, timeout):
        sent.append((url, json, headers["Authorization"]))
        if "cohere" in url:
            return Reply({"results": [{"index": 1, "relevance_score": 0.8},
                                      {"index": 0, "relevance_score": 0.2}]})
        return Reply({"data": [{"index": 0, "relevance_score": 0.7},
                               {"index": 1, "relevance_score": 0.3}]})

    monkeypatch.setattr(rerankers.requests, "post", post)
    assert rerankers.CohereReranker("ck").scores("q", ["x", "y"]) == [0.2, 0.8]
    assert rerankers.VoyageReranker("vk").scores("q", ["x", "y"]) == [0.7, 0.3]
    assert [(s[1]["query"], s[1]["documents"], s[2]) for s in sent] == [
        ("q", ["x", "y"], "Bearer ck"), ("q", ["x", "y"], "Bearer vk")]


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
