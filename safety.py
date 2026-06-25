"""
safety.py  —  the SAFETY LAYER, using the open-source Guardrails AI library.
            https://github.com/guardrails-ai/guardrails

WHY this file is called safety.py and NOT guardrails.py:
    The library is imported as `import guardrails`. A local guardrails.py would
    shadow the real library. So we name our file safety.py.

ONE-TIME SETUP (run in your terminal once):
    1. pip install guardrails-ai
    2. guardrails configure   -> paste a FREE token from
                                 https://hub.guardrailsai.com/keys
    3. guardrails hub install hub://guardrails/valid_length
    4. guardrails hub install hub://guardrails/toxic_language
    (the custom intent rule below needs NO hub install — it's our own code.)

------------------------------------------------------------------------------
THE 3 PIECES OF GUARDRAILS:
    - Validator = one rule (ValidLength, ToxicLanguage, or OUR OWN BlockStaffSalary)
    - Guard     = a bundle of validators
    - guard.validate(text) runs them all; on_fail="exception" -> raises if one fails
------------------------------------------------------------------------------
"""

from dotenv import load_dotenv
from guardrails import Guard
from guardrails.validator_base import (
    Validator,
    register_validator,
    PassResult,
    FailResult,
)
from langchain_openai import ChatOpenAI

# Load OPENAI_API_KEY from .env NOW, before we ever build an OpenAI client.
load_dotenv()


# ----------------------------------------------------------------------------
# A small, cheap model used ONLY to classify INTENT (not to answer questions).
# We build it LAZILY (on first use) so importing this file never needs the key.
# ----------------------------------------------------------------------------
_intent_llm = None


def _get_intent_llm():
    global _intent_llm
    if _intent_llm is None:
        _intent_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return _intent_llm


def _is_staff_salary_intent(question):
    """
    INTENT check (meaning, not keywords).
    Returns True if the question is asking about TEACHER / STAFF pay.
    This catches 'how much do staff get paid' even with NO banned words.
    """
    prompt = f"""You are an intent classifier. Reply with ONE word only.

Question: "{question}"

Is this asking about a TEACHER's or STAFF/EMPLOYEE's salary or pay?
Reply "BLOCK" if yes.
Reply "ALLOW" for anything else (student info, student stipend, general questions)."""
    decision = _get_intent_llm().invoke(prompt).content.strip().upper()
    return decision.startswith("BLOCK")


# ----------------------------------------------------------------------------
# OUR CUSTOM VALIDATOR  —  this is the "proper Guardrails way" to add a rule.
# @register_validator makes it usable inside a Guard like the built-in ones.
# ----------------------------------------------------------------------------
@register_validator(name="block-staff-salary", data_type="string")
class BlockStaffSalary(Validator):
    def validate(self, value, metadata):
        # value = the user's question. We return Pass or Fail.
        if _is_staff_salary_intent(value):
            return FailResult(
                error_message="Teacher/staff salary information is restricted."
            )
        return PassResult()


def _reveals_salary(text):
    """
    OUTPUT intent check (meaning, not keywords).
    Returns True if the ANSWER reveals a teacher/staff PAY AMOUNT.
    Catches 'they get paid 60k' even with no word like 'salary'.
    """
    prompt = f"""You check whether text reveals confidential STAFF PAY. Reply ONE word.

Text to check: "{text}"

BLOCK only if it states how much a TEACHER, STAFF member, ADMINISTRATOR, or
EMPLOYEE is PAID (their salary / wage).

ALLOW everything else — IMPORTANT, these are all ALLOW:
- Student stipends, scholarships, student payments (NOT staff pay)
- Course prices, plan prices, refund amounts
- Mentioning that staff/teachers exist, with NO pay amount

Examples:
- "Teachers are paid 60000 rupees a month" -> BLOCK
- "Students receive a stipend of 3000 rupees" -> ALLOW
- "The Pro plan costs 2000 rupees" -> ALLOW
- "We employ teachers and senior staff" -> ALLOW

Reply BLOCK or ALLOW:"""
    decision = _get_intent_llm().invoke(prompt).content.strip().upper()
    return decision.startswith("BLOCK")


# OUTPUT validator — same pattern, but it guards the ANSWER instead of the question.
@register_validator(name="block-salary-leak", data_type="string")
class BlockSalaryLeak(Validator):
    def validate(self, value, metadata):
        # value = the AI's answer. Block if it leaks staff pay.
        if _reveals_salary(value):
            return FailResult(
                error_message="Answer reveals confidential staff salary."
            )
        return PassResult()


# ----------------------------------------------------------------------------
# Build TWO kinds of guard:
#   - GENERIC guard  : length + toxicity. Applies to EVERYONE.
#   - SALARY guards  : the confidentiality rules. Apply ONLY to users who are
#                      NOT allowed to see restricted info (i.e. public users).
# This is the key principle: guardrails must respect permissions — never block
# a user who is authorized to see the data.
# ----------------------------------------------------------------------------
generic_validators = []
try:
    from guardrails.hub import ValidLength, ToxicLanguage
    generic_validators.append(ValidLength(min=1, max=500, on_fail="exception"))
    generic_validators.append(ToxicLanguage(threshold=0.5, validation_method="sentence",
                                            on_fail="exception"))
except ImportError:
    print("[safety] Hub validators not installed — generic checks skipped. "
          "Run: guardrails hub install hub://guardrails/toxic_language")

generic_guard = Guard()
for _v in generic_validators:
    generic_guard = generic_guard.use(_v)

# Salary-specific guards (used only for NON-authorized users).
salary_input_guard = Guard().use(BlockStaffSalary(on_fail="exception"))
output_guard = Guard().use(BlockSalaryLeak(on_fail="exception"))


# ----------------------------------------------------------------------------
# The functions rag.py calls.
# `allow_restricted` = True means this user (e.g. HR) MAY see confidential info,
# so we skip the salary guards for them.
# ----------------------------------------------------------------------------
def check_question(question, allow_restricted=False):
    """INPUT guardrail. Returns (ok, message)."""
    if not question or not question.strip():
        return False, "Please type a question."

    # Generic checks (length, toxicity) apply to everyone.
    try:
        generic_guard.validate(question)
    except Exception:
        return False, "Sorry, I can't help with that request."

    # Salary confidentiality check — only for users NOT allowed to see it.
    if not allow_restricted:
        try:
            salary_input_guard.validate(question)
        except Exception:
            return False, "Sorry, I can't share teacher/staff salary information."

    return True, ""


def check_answer(answer, llm=None, allow_restricted=False):
    """
    OUTPUT guardrail with REASK.
        - empty answer -> safe fallback message
        - authorized user (allow_restricted) -> return as-is (no filtering)
        - leaks staff pay + we have an llm -> ask the model to REWRITE it cleanly
        - leaks staff pay + no llm (or rewrite still leaks) -> refuse
    """
    if not answer or not answer.strip():
        return "I don't know based on the documents."

    # Authorized users (e.g. HR) are allowed to see salary — don't filter them.
    if allow_restricted:
        return answer.strip()

    # First pass: does the answer leak staff pay?
    try:
        output_guard.validate(answer)
        return answer.strip()          # clean -> return as-is
    except Exception:
        pass                           # leak detected -> try to fix it below

    # ---- REASK: ask the model to rewrite the answer WITHOUT the pay info ----
    if llm is not None:
        reask_prompt = f"""Rewrite the answer below so it does NOT reveal any
teacher's or staff member's salary, wage, or pay AMOUNT. Keep every other fact.
If removing the pay leaves nothing useful, say so politely.

Answer to rewrite: "{answer}"

Rewritten answer:"""
        rewritten = llm.invoke(reask_prompt).content.strip()

        # Check the rewrite. If it's now clean, use it; if it STILL leaks, refuse.
        try:
            output_guard.validate(rewritten)
            return rewritten
        except Exception:
            pass

    # No llm to reask with, or the rewrite still leaked -> hard refuse.
    return "Sorry, I can't share confidential staff salary information."
