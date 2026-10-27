"""
pii.py  —  personal data in answers, in the question log and in documents
(platform.md PL-13): RULE PACKS by locale, and a POLICY per workspace.

ENTITY TYPES (each a regular expression, and a checksum where the number has one,
so an order number that merely looks like a card isn't masked):

    EMAIL        email addresses
    PHONE        phone numbers (10 digits, with an optional country code)
    CARD         payment cards: 13-19 digits that pass the Luhn check
    IBAN         bank accounts: a country, two check digits and up to 30 letters
                 and digits that pass the ISO 13616 mod-97 check
    US_SSN       US Social Security numbers (AAA-GG-SSSS, the ranges the SSA
                 never issues left out)
    UK_NI        UK National Insurance numbers (AB 12 34 56 C, the prefixes
                 HMRC never issues left out)
    IN_PAN       Indian PAN (ABCDE1234F: the fourth letter says who holds it)
    IN_AADHAAR   Aadhaar numbers (1234 5678 9012, as before)

PACKS group them: global (email, phone, card, IBAN), us, uk, in.

THE POLICY (assistant_store's pii_policy, JSON; NULL = DEFAULT_POLICY):

    answers            types masked in answers (every surface)
    logs               types masked in the question log's redacted mode
    ingest             types the ingest-time scan flags on the Knowledge screen
    allow_own_domains  emails at the workspace's verified domains stay in answers
                       ("who owns X?" -> "ask priya@acme.com"); on by default
    allowed_domains    more domains whose emails stay in answers
    dense_min          a document is flagged once it holds this many values

Masking replaces a value with [REDACTED_<LABEL>], the labels answers have always
used (EMAIL, PHONE, CARD, AADHAAR) and new ones for the new types.

PRESIDIO (optional, not installed by default): PII_ENGINE=presidio with
presidio-analyzer and a spaCy model installed (deploy.md, "Personal data") adds
Presidio's findings for the same entity types to the rules' own. Without it,
or if it fails, the rules alone apply.
"""

import json
import logging
import os
import re

log = logging.getLogger("uvicorn.error")

ENTITIES = {
    "EMAIL": ("email address", "email addresses"),
    "PHONE": ("phone number", "phone numbers"),
    "CARD": ("payment card number", "payment card numbers"),
    "IBAN": ("IBAN", "IBANs"),
    "US_SSN": ("US Social Security number", "US Social Security numbers"),
    "UK_NI": ("UK National Insurance number", "UK National Insurance numbers"),
    "IN_PAN": ("PAN", "PAN numbers"),
    "IN_AADHAAR": ("Aadhaar number", "Aadhaar numbers"),
}
PACKS = {"global": ("EMAIL", "PHONE", "CARD", "IBAN"), "us": ("US_SSN",), "uk": ("UK_NI",),
         "in": ("IN_PAN", "IN_AADHAAR")}
MASKS = {"EMAIL": "EMAIL", "PHONE": "PHONE", "CARD": "CARD", "IBAN": "IBAN", "US_SSN": "SSN",
         "UK_NI": "NI_NUMBER", "IN_PAN": "PAN", "IN_AADHAAR": "AADHAAR"}
ALL = tuple(ENTITIES)
DENSE_MIN = 10
MAX_DOMAINS = 20

DEFAULT_POLICY = {"answers": list(ALL), "logs": list(ALL), "ingest": list(ALL),
                  "allow_own_domains": True, "allowed_domains": [], "dense_min": DENSE_MIN}


# ---- Checksums ------------------------------------------------------------------------------
def luhn(digits):
    """The Luhn check every payment card number passes."""
    digits = [int(d) for d in re.sub(r"\D", "", digits)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


# Lengths per country (ISO 13616); a country not listed takes 15-34.
_IBAN_LENGTHS = {"AT": 20, "BE": 16, "CH": 21, "CZ": 24, "DE": 22, "DK": 18, "ES": 24,
                 "FI": 18, "FR": 27, "GB": 22, "IE": 22, "IT": 27, "LU": 20, "NL": 18,
                 "NO": 15, "PL": 28, "PT": 25, "SE": 24, "AE": 23, "SA": 24}


def iban_ok(value):
    """The ISO 13616 mod-97 check (and the country's length, when known)."""
    s = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    if len(s) != _IBAN_LENGTHS.get(s[:2], len(s)):
        return False
    number = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(number) % 97 == 1


def ssn_ok(value):
    area, group, serial = re.findall(r"\d+", value)
    return area not in ("000", "666") and area[0] != "9" and group != "00" and serial != "0000"


_NI_BAD_PREFIX = {"BG", "GB", "NK", "KN", "TN", "NT", "ZZ"}


def ni_ok(value):
    prefix = re.sub(r"\s", "", value)[:2].upper()
    return prefix not in _NI_BAD_PREFIX and prefix[0] not in "DFIQUV" \
        and prefix[1] not in "DFIQUVO"


# ---- The rules ------------------------------------------------------------------------------
# (type, pattern, check or None). Order matters: the longest, most specific first,
# so a card isn't half-eaten by the phone rule.
RULES = (
    ("EMAIL", re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b"), None),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"),
     iban_ok),
    # 4-4-4-4(-3), Amex's 4-6-5, or 13-19 digits in a row; one kind of separator.
    ("CARD", re.compile(r"\b(?:\d{4}([ -]?)\d{4}\1\d{4}\1\d{4}(?:\1\d{1,3})?"
                        r"|\d{4}([ -]?)\d{6}\2\d{4,5}|\d{13,19})\b"), luhn),
    ("IN_AADHAAR", re.compile(r"(?<!\d\s)\b\d{4}\s\d{4}\s\d{4}\b(?!\s\d)"), None),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), ssn_ok),
    ("UK_NI", re.compile(r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"), ni_ok),
    ("IN_PAN", re.compile(r"\b[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]\b"), None),
    ("PHONE", re.compile(r"(?<![\w+])(?:\+?\d{1,3}[\s-]?)?\d{10}\b"), None),
)


def _clean_domains(domains):
    out = []
    for d in domains or ():
        d = str(d or "").strip().lower().lstrip("@").rstrip(".")
        if d and d not in out:
            out.append(d)
    return out


def _allowed(email_domain, domains):
    email_domain = email_domain.lower()
    return any(email_domain == d or email_domain.endswith("." + d) for d in domains)


def _presidio_spans(text, types):
    """[(start, end, type)] Presidio finds (PII_ENGINE=presidio), or [] without it."""
    if (os.getenv("PII_ENGINE") or "rules").strip().lower() != "presidio":
        return []
    names = {"EMAIL": "EMAIL_ADDRESS", "PHONE": "PHONE_NUMBER", "CARD": "CREDIT_CARD",
             "IBAN": "IBAN_CODE", "US_SSN": "US_SSN", "UK_NI": "UK_NINO", "IN_PAN": "IN_PAN",
             "IN_AADHAAR": "IN_AADHAAR"}
    try:
        engine = _presidio()
        back = {v: k for k, v in names.items()}
        found = engine.analyze(text=text, language="en",
                               entities=[names[t] for t in types if t in names])
        return [(r.start, r.end, back[r.entity_type]) for r in found
                if r.entity_type in back and r.score >= 0.5]
    except Exception as e:
        global _warned
        if not _warned:
            _warned = True
            log.warning("PII: Presidio isn't usable (%s); the rules alone apply",
                        type(e).__name__)
        return []


_engine = None
_warned = False


def _presidio():
    global _engine
    if _engine is None:
        from presidio_analyzer import AnalyzerEngine   # optional: deploy.md
        _engine = AnalyzerEngine()
    return _engine


def find(text, types=ALL):
    """[(start, end, type, value)] of the PII in `text` of the given types, without
    overlaps (an earlier rule wins)."""
    if not text:
        return []
    taken, out = [], []

    def free(start, end):
        return all(end <= s or start >= e for s, e in taken)

    for kind, pattern, check in RULES:
        if kind not in types:
            continue
        for m in pattern.finditer(text):
            if free(m.start(), m.end()) and (check is None or check(m.group())):
                taken.append((m.start(), m.end()))
                out.append((m.start(), m.end(), kind, m.group()))
    for start, end, kind in _presidio_spans(text, types):
        if free(start, end):
            taken.append((start, end))
            out.append((start, end, kind, text[start:end]))
    return sorted(out)


def redact(text, types=ALL, allow_domains=()):
    """`text` with each value of `types` masked; emails at `allow_domains` (and
    their subdomains) are kept. Never raises."""
    if not text:
        return text
    try:
        domains = _clean_domains(allow_domains)
        out, at = [], 0
        for start, end, kind, value in find(text, types):
            if kind == "EMAIL" and domains and _allowed(value.rpartition("@")[2], domains):
                continue
            out += [text[at:start], f"[REDACTED_{MASKS[kind]}]"]
            at = end
        return "".join(out) + text[at:]
    except Exception:
        log.exception("PII: masking failed; masking with the default rules")
        return redact(text) if (types, allow_domains) != (ALL, ()) else text


def count(text, types=ALL):
    """{type: distinct values} of `text` (an ingest-time scan: never the values)."""
    seen = {}
    for _, _, kind, value in find(text, types):
        seen.setdefault(kind, set()).add(re.sub(r"[\s-]", "", value).lower())
    return {kind: len(values) for kind, values in seen.items()}


def describe(counts):
    """{"PHONE": 40, "EMAIL": 1} -> "40 phone numbers, 1 email address"."""
    parts = []
    for kind, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        one, many = ENTITIES.get(kind, (kind, kind))
        parts.append(f"{n} {one if n == 1 else many}")
    return ", ".join(parts)


# ---- The policy -----------------------------------------------------------------------------
class Invalid(ValueError):
    pass


def clean_policy(policy):
    """A policy as sent by the admin screen, checked; missing keys take the defaults.
    Raises Invalid."""
    if policy is None:
        return dict(DEFAULT_POLICY)
    if not isinstance(policy, dict):
        raise Invalid("The personal data policy is an object.")
    unknown = set(policy) - set(DEFAULT_POLICY)
    if unknown:
        raise Invalid(f"Unknown personal data setting: {sorted(unknown)[0]}.")
    out = {}
    for key in ("answers", "logs", "ingest"):
        value = policy.get(key, DEFAULT_POLICY[key])
        if not isinstance(value, (list, tuple)) or any(v not in ENTITIES for v in value):
            raise Invalid(f"Choose the types to mask from: {', '.join(ALL)}.")
        out[key] = [t for t in ALL if t in value]
    out["allow_own_domains"] = bool(policy.get("allow_own_domains",
                                               DEFAULT_POLICY["allow_own_domains"]))
    domains = policy.get("allowed_domains") or []
    if not isinstance(domains, (list, tuple)):
        raise Invalid("Allowed domains are a list.")
    domains = _clean_domains(domains)
    if len(domains) > MAX_DOMAINS or any(
            not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", d) for d in domains):
        raise Invalid(f"Allowed domains look like example.com, {MAX_DOMAINS} at most.")
    out["allowed_domains"] = domains
    try:
        out["dense_min"] = max(1, min(10_000, int(policy.get("dense_min",
                                                             DEFAULT_POLICY["dense_min"]))))
    except (TypeError, ValueError):
        raise Invalid("Flag documents from a whole number of values.")
    return out


def load(raw):
    """A stored policy (JSON or None) -> a whole one; a damaged one is the default."""
    try:
        return clean_policy(json.loads(raw) if raw else None)
    except (ValueError, TypeError):
        log.warning("PII: a stored policy didn't parse; using the default")
        return dict(DEFAULT_POLICY)


class Policy:
    """A workspace's policy, ready to apply. own_domains: its verified domains."""

    def __init__(self, policy=None, own_domains=()):
        self.policy = clean_policy(policy)
        self.allowed = list(self.policy["allowed_domains"])
        if self.policy["allow_own_domains"]:
            self.allowed += _clean_domains(own_domains)

    def answer(self, text):
        """An answer as shown: the answers' types masked, the allowed domains kept."""
        return redact(text, self.policy["answers"], self.allowed)

    def log(self, text):
        """Question log text in redacted mode."""
        return redact(text, self.policy["logs"])

    def flagged(self, counts):
        """The counts the ingest scan flags under this policy ({} when below dense_min)."""
        kept = {k: n for k, n in (counts or {}).items() if k in self.policy["ingest"] and n}
        return kept if sum(kept.values()) >= self.policy["dense_min"] else {}
