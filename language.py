"""
language.py  —  which language a question is in (pipeline.md P2-4). Pure Python,
no model call, no dependency: the answer's language and the cross-lingual search
only need a few dozen languages told apart reliably, and a question is short.

    detect(text)        an ISO 639-1 code ("en", "hi", "es", ...), "hi-Latn" for
                        Hindi written in Latin letters (Hinglish), or None when it
                        can't tell (too short, numbers only, a tie)
    dominant(texts)     the language most of a corpus is in, or None
    name(code)          how the prompt names it ("Hindi", "Hinglish (...)")

HOW. First the SCRIPT: most scripts belong to one language here (Devanagari ->
Hindi, Hangul -> Korean, kana -> Japanese, Thai, Greek, ...). A Latin, Cyrillic
or Arabic text is then told apart by its most common short words (articles,
pronouns, question words): a language needs at least MIN_HITS of them and more
than any other, else the answer is None and nothing changes. A model-based
detector does better on a paragraph but is unreliable on "refund policy?", and
a wrong guess would make the assistant answer in the wrong language: None is
the safe answer here.
"""

import re
import unicodedata

MIN_HITS = 2               # Latin / Cyrillic / Arabic: function words a language needs
MIN_LETTERS = 3            # fewer letters than this: no language

# Script (a word in the character's Unicode name) -> language.
_SCRIPTS = (
    ("DEVANAGARI", "hi"), ("BENGALI", "bn"), ("GURMUKHI", "pa"), ("GUJARATI", "gu"),
    ("TAMIL", "ta"), ("TELUGU", "te"), ("KANNADA", "kn"), ("MALAYALAM", "ml"),
    ("ORIYA", "or"), ("THAI", "th"), ("HANGUL", "ko"), ("HIRAGANA", "ja"),
    ("KATAKANA", "ja"), ("CJK", "zh"), ("GREEK", "el"), ("HEBREW", "he"),
    ("ARABIC", "ar"), ("CYRILLIC", "ru"), ("ARMENIAN", "hy"), ("GEORGIAN", "ka"),
    ("ETHIOPIC", "am"), ("SINHALA", "si"), ("KHMER", "km"), ("LATIN", "latin"),
)

# Common short words per language (lower case, accents kept). Chosen to be rare
# in the other languages listed; a word in two lists counts for both.
_WORDS = {
    "en": "the is are was what how when where who why which do does can could should would "
          "of to for and with my your our their this that there have has please about i you we",
    "es": "el la los las es son qué que cómo cuándo dónde quién por para con mi mis tu "
          "nuestro una unos está están hay puedo cuál cuánto cuántos del al lo se",
    "fr": "le la les est sont quel quelle quels comment quand où qui pour avec mon ma mes "
          "notre votre une des du au je nous vous combien il elle ils sur dans pas ce cette "
          "puis peut",
    "de": "der die das ist sind was wie wann wo wer warum für mit mein meine unser ein eine "
          "nicht und ich wir sie gibt kann können wieviel welche",
    "pt": "o a os as é são qual quais como quando onde quem porque por para com meu minha "
          "nosso uma um não está estão posso quanto quantos da do",
    "it": "il lo la gli le è sono che cosa come quando dove chi perché per con mio mia "
          "nostro una uno non c'è posso quanto quanti della del",
    "nl": "de het een is zijn wat hoe wanneer waar wie waarom voor met mijn ons onze "
          "niet en ik wij kan hoeveel welke",
    "hi-Latn": "kya hai hain kaise kab kahan kaun kyun kyon mera meri hamara hamari ka ki ke "
               "ko se mein me nahi nahin kitna kitne kitni batao bataiye karna karte chahiye "
               "milta milti sakta sakte",
    "ru": "что как когда где кто почему для это мой наш и не в на с по сколько можно ли есть",
    "uk": "що як коли де хто чому для це мій наш і не в на з скільки можна є",
    "ar": "ما ماذا كيف متى أين من لماذا في على هل هذا هذه عن مع",
    "ur": "کیا ہے ہیں کیسے کب کہاں کون کیوں میرا ہمارا کا کی کے کو سے میں نہیں",
    "fa": "چه چطور کی کجا چرا این است برای با از در را",
}
_WORD_SETS = {lang: set(words.split()) for lang, words in _WORDS.items()}
_SHARED = {}                      # word -> how many lists have it
for _words in _WORD_SETS.values():
    for _w in _words:
        _SHARED[_w] = _SHARED.get(_w, 0) + 1
# Which word lists can decide a text in each script.
_BY_SCRIPT = {"latin": ("en", "es", "fr", "de", "pt", "it", "nl", "hi-Latn"),
              "ru": ("ru", "uk"), "ar": ("ar", "ur", "fa")}
_DEFAULT = {"ru": "ru", "ar": "ar"}      # a script whose words didn't decide
_UK_LETTERS = set("іїєґ")

NAMES = {"en": "English", "hi": "Hindi", "hi-Latn": "Hinglish (Hindi written in Latin "
         "letters, as the user wrote it)", "es": "Spanish", "fr": "French", "de": "German",
         "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "ru": "Russian",
         "uk": "Ukrainian", "ar": "Arabic", "ur": "Urdu", "fa": "Persian", "bn": "Bengali",
         "pa": "Punjabi", "gu": "Gujarati", "ta": "Tamil", "te": "Telugu", "kn": "Kannada",
         "ml": "Malayalam", "or": "Odia", "th": "Thai", "ko": "Korean", "ja": "Japanese",
         "zh": "Chinese", "el": "Greek", "he": "Hebrew", "hy": "Armenian", "ka": "Georgian",
         "am": "Amharic", "si": "Sinhala", "km": "Khmer"}
# The assistant settings' fixed choices (assistant_store.LANGUAGES) by code.
CODES = {name: code for code, name in NAMES.items() if code != "hi-Latn"}

_TOKEN = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)
_MARKER = re.compile(r"\[S\d+\]|https?://\S+|\S+@\S+")


def name(code):
    return NAMES.get(code, code)


def _script(ch):
    try:
        uname = unicodedata.name(ch)
    except ValueError:
        return None
    for word, lang in _SCRIPTS:
        if uname.startswith(word) or f" {word} " in f" {uname} ":
            return lang
    return None


def detect(text):
    """The language of `text` (see the top of this file), or None."""
    text = _MARKER.sub(" ", unicodedata.normalize("NFKC", text or ""))
    counts = {}
    for ch in text:
        # Letters, and the vowel signs of Indic scripts (combining marks).
        if ch.isalpha() or unicodedata.category(ch).startswith("M"):
            script = _script(ch)
            if script:
                counts[script] = counts.get(script, 0) + 1
    letters = sum(counts.values())
    if letters < MIN_LETTERS:
        return None
    # Japanese is written with kanji (CJK) and kana together: any kana says Japanese.
    if counts.get("ja"):
        return "ja"
    script = max(counts, key=counts.get)
    # Code-switching: "Pro plan की कीमत क्या है?" is Hindi with an English name in it.
    other = max((s for s in counts if s != "latin"), key=counts.get, default=None)
    if script == "latin" and other and counts[other] * 3 >= letters:
        script = other
    if counts[script] * 2 < letters and script == "latin":   # no script is most of it
        return None
    if script not in _BY_SCRIPT:
        return script
    words = [part.casefold().strip("'’") for w in _TOKEN.findall(text)
             for part in re.split(r"-", w) if part]
    if script == "ru" and _UK_LETTERS & set("".join(words)):
        return "uk"
    # A word in several lists counts as a fraction for each: "le" says French or
    # Italian, "est" says French.
    hits = {lang: sum(1 / _SHARED[w] for w in words if w in _WORD_SETS[lang])
            for lang in _BY_SCRIPT[script]}
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    best, n = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    seen = sum(w in _WORD_SETS[best] for w in words)
    if seen >= MIN_HITS and n > runner_up:
        return best
    return _DEFAULT.get(script)


def dominant(texts, share=0.6):
    """The language at least `share` of the texts that have one are in, or None."""
    found = [lang for lang in (detect(t) for t in texts or []) if lang]
    if not found:
        return None
    best = max(set(found), key=found.count)
    return best if found.count(best) >= share * len(found) else None


def same(a, b):
    """Two detected languages that a reader would call the same ("hi-Latn" is Hindi)."""
    return (a or "").split("-")[0] == (b or "").split("-")[0]
