"""
The question and answer guards (safety.py), with the classifier faked: the same
inputs are refused or let through, with the same messages, as when they ran on
Guardrails AI. And safety needs no Guardrails at all, nor does requirements.txt.
"""

import os
import re
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
import safety  # noqa: E402
from safety import MAX_QUESTION_CHARS, check_answer, check_question  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESTRICTED = ("Staff salary information is restricted to HR / Admin. "
              "Switch the role to HR / Admin to see it.")
CANT_HELP = "Sorry, I can't help with that request."
REFUSE_PAY = "Sorry, I can't share confidential staff salary information."


class Classifier:
    """safety._get_intent_llm: BLOCKs a question naming pay and an answer stating
    one; records what it was asked. `broken` raises, as a provider outage would."""

    def __init__(self, broken=False):
        self.asked, self.broken = [], broken

    def invoke(self, prompt):
        self.asked.append(prompt)
        if self.broken:
            raise RuntimeError("provider down")
        if prompt.startswith("You are an intent classifier"):
            text = prompt.split('Question: "', 1)[1].split('"', 1)[0]
            return AIMessage(content="BLOCK" if "earn" in text else "ALLOW")
        if prompt.startswith("You check whether text reveals"):
            return AIMessage(content="BLOCK" if "75000" in prompt else "ALLOW")
        return AIMessage(content="ALLOW")


class Rewriter:
    """The answer model, asked to rewrite a leaking answer."""

    def __init__(self, reply):
        self.reply, self.asked = reply, []

    def invoke(self, prompt):
        self.asked.append(prompt)
        return AIMessage(content=self.reply)


@pytest.fixture
def classifier(monkeypatch):
    fake = Classifier()
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: fake)
    return fake


# ---- check_question ---------------------------------------------------------

@pytest.mark.parametrize("question", ["", "   ", None])
def test_an_empty_question_is_asked_for(classifier, question):
    assert check_question(question) == (False, "Please type a question.")
    assert classifier.asked == []


def test_overlong_questions_are_refused_for_everyone(classifier):
    assert check_question("a" * MAX_QUESTION_CHARS, allow_restricted=True) == (True, "")
    for allow in (True, False):
        assert check_question("a" * (MAX_QUESTION_CHARS + 1), allow_restricted=allow) == (False, CANT_HELP)
    assert classifier.asked == []          # the length rule runs first, with no model call


def test_staff_pay_questions_are_refused_to_those_who_may_not_see_them(classifier):
    assert check_question("How much do teachers earn?") == (False, RESTRICTED)
    assert check_question("How many paid leaves do employees get?") == (True, "")
    assert len(classifier.asked) == 2


def test_hr_skips_the_salary_check(classifier):
    assert check_question("How much do teachers earn?", allow_restricted=True) == (True, "")
    assert classifier.asked == []


def test_a_failing_classifier_refuses_the_question(monkeypatch):
    """Fails closed, as the Guard did: its exception was caught as a refusal."""
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Classifier(broken=True))
    assert check_question("What is the refund policy?") == (False, RESTRICTED)


def test_the_classifier_sees_the_callers_context(monkeypatch):
    """The check runs in the caller's thread, so its model call counts toward the
    question (kb/services/question_log.counting reads a context variable)."""
    import contextvars
    marker = contextvars.ContextVar("marker", default=None)
    seen = []

    class Seeing(Classifier):
        def invoke(self, prompt):
            seen.append(marker.get())
            return super().invoke(prompt)

    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Seeing())
    marker.set("the caller")
    check_question("What is the refund policy?")
    check_answer("The refund window is 30 days.")
    assert seen == ["the caller", "the caller"]


# ---- check_answer -----------------------------------------------------------

@pytest.mark.parametrize("answer", ["", "  ", None])
def test_an_empty_answer_falls_back(classifier, answer):
    assert check_answer(answer) == "I don't know based on the documents."


def test_hr_gets_the_answer_unfiltered(classifier):
    assert check_answer("  Teachers are paid 75000 a month.  ", allow_restricted=True) == \
        "Teachers are paid 75000 a month."
    assert classifier.asked == []


def test_a_clean_answer_comes_back_stripped(classifier):
    assert check_answer("  The refund window is 30 days.\n") == "The refund window is 30 days."


def test_a_leaking_answer_with_no_model_is_refused(classifier):
    assert check_answer("Teachers are paid 75000 a month.") == REFUSE_PAY


def test_a_leaking_answer_is_rewritten_and_rechecked(classifier):
    model = Rewriter("  Teachers are paid according to the pay scale.  ")
    assert check_answer("Teachers are paid 75000 a month.", llm=model) == \
        "Teachers are paid according to the pay scale."
    # The rewrite prompt carries the answer spotlighted, as data.
    assert "75000" in model.asked[0] and "answer_to_rewrite" in model.asked[0]
    assert len(classifier.asked) == 2      # the answer, then the rewrite


def test_a_rewrite_that_still_leaks_is_refused(classifier):
    assert check_answer("Teachers are paid 75000 a month.",
                        llm=Rewriter("Still 75000 a month.")) == REFUSE_PAY


def test_a_failing_classifier_refuses_the_answer(monkeypatch):
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Classifier(broken=True))
    assert check_answer("The refund window is 30 days.") == REFUSE_PAY


# ---- toxicity: there is none, and it says so --------------------------------

def test_no_toxicity_check_is_reported(monkeypatch):
    monkeypatch.delenv("REQUIRE_TOXICITY_GUARD", raising=False)
    assert safety.toxicity_guard_active is False
    assert safety.guards_status() == {"toxicity": "missing", "toxicity_required": False}
    safety.require_guards()                 # not required: starts


def test_requiring_toxicity_refuses_to_start(monkeypatch):
    monkeypatch.setenv("REQUIRE_TOXICITY_GUARD", "1")
    assert safety.guards_status() == {"toxicity": "missing", "toxicity_required": True}
    with pytest.raises(RuntimeError, match="REQUIRE_TOXICITY_GUARD"):
        safety.require_guards()


# ---- no Guardrails ----------------------------------------------------------

def _requirements():
    with open(os.path.join(ROOT, "requirements.txt")) as f:
        lines = [line.split("#", 1)[0].strip() for line in f]
    return {re.split(r"[=<>!~\[ ]", line, maxsplit=1)[0].lower(): line for line in lines if line}


def test_requirements_have_no_guardrails_and_a_fixed_filelock():
    reqs = _requirements()
    assert "guardrails-ai" not in reqs and "litellm" not in reqs
    # filelock 4 is where its 2026-10 advisories are fixed; litellm held it below 4.
    major = int(re.search(r"==\s*(\d+)\.", reqs["filelock"]).group(1))
    assert major >= 4


def test_safety_imports_without_guardrails():
    """In a fresh interpreter where `import guardrails` fails, safety still imports
    and its rules still run."""
    code = (
        "import sys; sys.modules['guardrails'] = None; sys.modules['litellm'] = None\n"
        "import dotenv; dotenv.load_dotenv = lambda *a, **k: False\n"
        "import safety\n"
        "assert safety.check_question('a' * 501, allow_restricted=True)[0] is False\n"
        "assert safety.check_question('hi', allow_restricted=True) == (True, '')\n"
        "assert 'guardrails' not in [m.split('.')[0] for m in sys.modules if sys.modules[m]]\n"
    )
    env = dict(os.environ, OPENAI_API_KEY="sk-test", PYTHONPATH=ROOT)
    run = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
