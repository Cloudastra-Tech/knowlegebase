"""
faithfulness.py  —  a DETERMINISTIC check that the numbers, dates and amounts in
an answer come from what it cites (pipeline.md P1-6). No model call.

A wrong figure is the most damaging thing a cited answer can say ("refunds
within 60 days [S1]" when S1 says 30), and the easiest to check: every number,
amount, percentage, time and date in a sentence must appear in the passages
that sentence cites. A sentence that cites nothing is checked against
everything the answer was written from. Besides the passages, a figure may come
from the question itself, the conversation, and tools that compute or look
things up (calculator, currency rates, Wikipedia): those count as sources too,
so "2000 x 12 = 24,000" passes when the calculator said 24000.

What counts as the same value: "2,000", "2000" and "₹2000.00" are one number;
"9am" is 9; a month name matches its abbreviation and, in a numeric date
("2026-09-12", "12/09/2026"), its number; "thirty" is 30. Single digits on their
own ("2 options", list numbers) are left alone: too often the model's own count.

FAITHFULNESS_CHECK (.env) decides what happens when a value isn't found:
    flag   the default: the answer stays as written and ends with a note naming
           the values the documents don't show, so the reader checks them
    log    only logged (and kept in the run's report)
    off    not checked
The LLM judge that scores whole claims offline is evaluate.py's.
"""

import os
import re
import unicodedata

MODES = ("flag", "log", "off")
MAX_NOTED = 3                  # values named in the note, at most

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_WORD = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
               r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
               "eighty": 80, "ninety": 90, "hundred": 100})

_MARKER = re.compile(r"\[S(\d{1,3})\]")
_LINK = re.compile(r"\]\([^)]*\)|https?://\S+")
_DIGIT_GROUP = re.compile(r"(?<=\d)[,  ](?=\d{3}\b)")      # 2,000 / 2 000 / 2 000
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_SLASH_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b")
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(\s*%|\s*(?:am|pm)\b)?", re.IGNORECASE)
_MONEY = re.compile(r"(?:[$€£¥₹]|\b(?:rs|inr|usd|eur)\.?\s*)\s*$", re.IGNORECASE)
_DATE_MONTH = re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_WORD})\b"
                         rf"|\b({_MONTH_WORD})\.?\s+\d{{1,4}}\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[*_-])|\n+")
_LIST_NUMBER = re.compile(r"^\s*\d{1,2}[.)]\s", re.M)


def mode():
    value = (os.getenv("FAITHFULNESS_CHECK") or "").strip().lower()
    return value if value in MODES else MODES[0]


def _number(text):
    """'2000.00' -> '2000', '09' -> '9', '2.50' -> '2.5'."""
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.lstrip("0") or "0"


def _normal(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = _LINK.sub(" ", _MARKER.sub(" ", text))
    return _DIGIT_GROUP.sub("", text)


def values(text, claims=False):
    """The values in `text`: {"n:2000", "n:30", "month:9", ...}. claims=True:
    as checked in an answer (single digits on their own are skipped); False:
    everything a passage offers to match against (number words too)."""
    text = _normal(text)
    if claims:
        text = _LIST_NUMBER.sub(" ", text)
    out = set()
    for m in _ISO_DATE.finditer(text):
        out |= {f"n:{_number(m.group(1))}", f"month:{int(m.group(2))}", f"n:{_number(m.group(3))}"}
    for m in _SLASH_DATE.finditer(text):
        out |= {f"n:{_number(m.group(1))}", f"month:{int(m.group(2))}", f"n:{_number(m.group(3))}"}
        if not claims:                    # 09/12 can be read either way round
            out |= {f"month:{int(m.group(1))}", f"n:{_number(m.group(2))}"}
    stripped = _SLASH_DATE.sub(" ", _ISO_DATE.sub(" ", text))
    for m in _NUMBER.finditer(stripped):
        digits, unit = m.group(1), m.group(2)
        money = bool(_MONEY.search(stripped[max(0, m.start() - 6):m.start()]))
        if claims and len(digits.split(".")[0]) < 2 and not (unit or money or "." in digits):
            continue
        out.add(f"n:{_number(digits)}")
    for m in _DATE_MONTH.finditer(text):
        word = (m.group(1) or m.group(2)).lower()[:3]
        out.add(f"month:{_MONTHS.index(word) + 1}")
    if not claims:
        lowered = text.lower()
        for word in re.findall(r"[a-z]+", lowered):
            if word in _WORDS:
                out.add(f"n:{_WORDS[word]}")
            elif word[:3] in _MONTHS and re.fullmatch(_MONTH_WORD, word):
                out.add(f"month:{_MONTHS.index(word[:3]) + 1}")
    return out


def _shown(value):
    kind, v = value.split(":", 1)
    return _MONTHS[int(v) - 1].capitalize() if kind == "month" else v


def sentences(answer):
    return [s for s in _SENTENCE.split(answer or "") if s.strip()]


def unsupported(answer, cited, everything, extra=()):
    """[(sentence, [values])] for each sentence whose values aren't found.
    cited: {marker number: passage text} for the answer's [S#] markers (as
    renumbered by agent.cite); everything: the text of every passage read;
    extra: the question, the conversation, other tools' outputs."""
    base = set()
    for text in extra:
        base |= values(text)
    pool_all = base | {v for text in everything for v in values(text)}
    found = []
    for sentence in sentences(answer):
        claims = values(sentence, claims=True)
        if not claims:
            continue
        refs = [int(n) for n in _MARKER.findall(sentence)]
        if refs and all(n in cited for n in refs):
            pool = base | {v for n in refs for v in values(cited[n])}
        else:
            pool = pool_all
        missing = sorted(claims - pool)
        if missing:
            found.append((sentence.strip(), missing))
    return found


def note(problems):
    """The line added under an answer (flag mode) naming what wasn't found."""
    shown = list(dict.fromkeys(_shown(v) for _, missing in problems for v in missing))
    if not shown:
        return ""
    listed = ", ".join(shown[:MAX_NOTED]) + (" and others" if len(shown) > MAX_NOTED else "")
    return (f"\n\n_Check before relying on it: I couldn't find {listed} in the cited "
            f"documents._")
