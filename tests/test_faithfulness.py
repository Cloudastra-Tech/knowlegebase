"""
The deterministic faithfulness check (faithfulness.py, pipeline.md P1-6): the
numbers, dates and amounts in an answer must be in the passages each sentence
cites, and through the agent (agent.check_figures) an answer whose figure isn't
there gets a note, or only a log line, or nothing, as FAITHFULNESS_CHECK says.
"""

import pytest
from langchain_core.messages import ToolMessage

import faithfulness
from test_citations import PASSAGE, Allow, CitingModel, corpus_index  # noqa: F401

HANDBOOK = ("Customers can request a full refund within 30 days of purchase. "
            "The Pro plan costs 2,000 rupees per month. Support is open 9am to 6pm IST. "
            "The fellowship starts on 12 September 2026 and lasts six months.")
CSV = "Batch,Start date,Seats left\nB-27,2027-07-05,17"


@pytest.mark.parametrize("sentence", [
    "Refunds are given within 30 days [S1].",
    "The Pro plan is ₹2000 a month [S1].",
    "It costs Rs. 2,000.00 per month [S1].",
    "Support opens at 9 am [S1].",
    "The fellowship begins on September 12, 2026 [S1].",
    "It lasts 6 months [S1].",                              # "six" in the passage
    "Batch B-27 starts on 5 July 2027 with 17 seats left [S2].",
    "There are 2 plans to pick from [S1].",                 # a lone digit: the model's count
])
def test_figures_found_in_the_cited_passage_pass(sentence):
    assert faithfulness.unsupported(sentence, {1: HANDBOOK, 2: CSV}, [HANDBOOK, CSV]) == []


@pytest.mark.parametrize("sentence, missing", [
    ("Refunds are given within 60 days [S1].", ["n:60"]),
    ("The Pro plan costs 2500 rupees [S1].", ["n:2500"]),
    ("Support closes at 7pm [S1].", ["n:7"]),
    ("The fellowship starts on 12 October 2026 [S1].", ["month:10"]),
    ("Discounts are 15% [S1].", ["n:15"]),
    # A figure that is in the documents, but not in the passage this sentence cites.
    ("B-27 has 17 seats left [S1].", ["n:17", "n:27"]),
])
def test_figures_missing_from_the_cited_passage_are_reported(sentence, missing):
    ((_, got),) = faithfulness.unsupported(sentence, {1: HANDBOOK, 2: CSV}, [HANDBOOK, CSV])
    assert got == missing


def test_an_uncited_sentence_is_checked_against_everything_read():
    answer = "B-27 has 17 seats left. Refunds take 30 days [S1]."
    assert faithfulness.unsupported(answer, {1: HANDBOOK}, [HANDBOOK, CSV]) == []
    assert faithfulness.unsupported("It has 99 seats.", {}, [HANDBOOK, CSV]) == \
        [("It has 99 seats.", ["n:99"])]


def test_the_question_history_and_tool_results_count_as_sources():
    answer = "That is 24,000 rupees a year [S1]."            # the calculator's 2000*12
    assert faithfulness.unsupported(answer, {1: HANDBOOK}, [HANDBOOK]) != []
    assert faithfulness.unsupported(answer, {1: HANDBOOK}, [HANDBOOK], extra=["24000"]) == []


def test_markers_links_and_list_numbers_are_not_figures():
    answer = ("1. Refunds take 30 days [S12].\n2. See [the policy](https://x.example/p/4471).")
    assert faithfulness.unsupported(answer, {12: HANDBOOK}, [HANDBOOK]) == []


def test_the_note_names_what_wasnt_found():
    problems = [("a", ["n:60", "month:10"]), ("b", ["n:60", "n:7", "n:8"])]
    note = faithfulness.note(problems)
    assert "60, Oct, 7 and others" in note and note.startswith("\n\n_")
    assert faithfulness.note([]) == ""


@pytest.mark.parametrize("value, mode", [("", "flag"), ("log", "log"), ("OFF", "off"),
                                         ("bogus", "flag")])
def test_the_mode_comes_from_env(monkeypatch, value, mode):
    monkeypatch.setenv("FAITHFULNESS_CHECK", value)
    assert faithfulness.mode() == mode


# ---- Through the agent -------------------------------------------------------------
class RefundModel(CitingModel):
    """Writes script["reply"], with {n} the number of the passage holding the refund rule."""

    def _answer(self, messages):
        found = [(int(n), text) for m in messages
                 if isinstance(m, ToolMessage) and m.name == "search_docs"
                 for n, text in PASSAGE.findall(m.content)]
        n = next(n for n, text in found if "full refund within 30 days" in text)
        return self.script["reply"].format(n=n)


@pytest.fixture
def answer_with(monkeypatch, corpus_index):  # noqa: F811
    """build_agent's answer to the refund question, the model writing `text`."""
    import agent
    import rag
    import safety
    from eval import harness

    def ask(text, mode="flag"):
        monkeypatch.setenv("FAITHFULNESS_CHECK", mode)
        script = {"reply": text}
        monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: RefundModel(script=script))
        monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: Allow())
        monkeypatch.setattr(safety, "_get_intent_llm", lambda: Allow())
        monkeypatch.setattr(rag, "USE_HYDE", False)
        out = {}
        answer, _, _ = agent.build_agent("public", harness.COLLECTION, outline=False)(
            "What is the refund policy?", with_sources=True, out=out)
        return answer, out
    return ask


def test_an_answer_with_a_wrong_figure_is_flagged(answer_with):
    answer, out = answer_with("Customers get a full refund within 45 days [S{n}].")
    assert out["unsupported"] == ["n:45"]
    assert answer.startswith("Customers get a full refund within 45 days [S1].")
    assert "couldn't find 45 in the cited documents" in answer


def test_a_right_figure_passes_and_log_or_off_leave_the_answer(answer_with):
    answer, out = answer_with("Customers get a full refund within 30 days [S{n}].")
    assert out["unsupported"] == [] and "Check before" not in answer
    answer, out = answer_with("A refund within 45 days [S{n}].", mode="log")
    assert out["unsupported"] == ["n:45"] and "Check before" not in answer
    answer, out = answer_with("A refund within 45 days [S{n}].", mode="off")
    assert "unsupported" not in out and "Check before" not in answer
