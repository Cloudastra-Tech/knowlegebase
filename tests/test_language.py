"""
Multilingual answers (pipeline.md P2-4): the question's language is detected
without a model (language.py); with the answer language on "auto" the model is
told it per question, in a message AFTER the fixed system prompt (which stays
the same for every question, for OpenAI's prompt cache), keeping the [S#]
citations; and with cross-lingual search on, a question in another language than
the documents' is also searched translated, by meaning only.
"""

import pytest
from langchain_core.messages import AIMessage, SystemMessage

import agent
import language
import rag
from agent import build_agent
from eval import harness
from test_assistant_settings import recording  # noqa: F401  (a fixture)
from test_citations import citing, corpus_index  # noqa: F401  (fixtures)
from test_indexing import env  # noqa: F401  (a fixture)


@pytest.mark.parametrize("text, code", [
    ("What is the refund policy?", "en"),
    ("¿Cuál es la política de reembolso?", "es"),
    ("Quelle est la politique de remboursement ?", "fr"),
    ("Wie viele Urlaubstage habe ich?", "de"),
    ("Qual é a política de reembolso?", "pt"),
    ("Hoeveel vakantiedagen heb ik?", "nl"),
    ("refund policy kya hai?", "hi-Latn"),
    ("मेरी छुट्टी कितने दिन की है?", "hi"),
    ("Какая политика возврата?", "ru"),
    ("ما هي سياسة الاسترداد؟", "ar"),
    ("返金ポリシーは何ですか？", "ja"),
    ("退款政策是什么？", "zh"),
    ("환불 정책은 무엇입니까?", "ko"),
])
def test_languages_are_detected(text, code):
    assert language.detect(text) == code


@pytest.mark.parametrize("text", ["refund policy?", "Pro plan 2000", "hi", "", "[S1] 42",
                                  "STARTER plan: AI queries?"])
def test_it_says_none_when_it_cannot_tell(text):
    """A wrong guess would answer in the wrong language: unsure is None, and nothing
    changes for the question."""
    assert language.detect(text) is None


def test_the_dominant_language_of_a_corpus():
    english = ["The refund window is 30 days and it is in the handbook.",
               "Support is open from 9 to 6, and you can call us."]
    assert language.dominant(english) == "en"
    assert language.dominant(english + ["La política de reembolso es de 30 días para el curso."]) \
        == "en"
    assert language.dominant(["", "42"]) is None
    assert language.same("hi-Latn", "hi") and not language.same("en", "es")


def test_the_note_follows_the_setting():
    hindi = language.detect("मेरी छुट्टी कितने दिन की है?")
    note = agent.language_note({"language": "auto"}, hindi, "en")
    assert "in Hindi" in note and "[S#]" in note
    assert agent.language_note({"language": "German"}, hindi, "en") is None    # fixed language
    assert agent.language_note({}, "en", "en") is None                         # as before
    assert agent.language_note({}, None, "en") is None                         # unsure
    assert "in English" in agent.language_note({}, "en", "es")                # English, Spanish docs
    assert "Hinglish" in agent.language_note({}, "hi-Latn", "en")


def test_the_language_goes_after_the_fixed_prompt_and_keeps_the_citations(recording):  # noqa: F811
    run = build_agent("public", harness.COLLECTION, outline=False, prefs={"language": "auto"})
    out = {}
    answer, _, sources = run("¿Cuál es la política de reembolso del curso?", with_sources=True,
                             out=out)
    spanish = recording["seen"][-1]
    run("What is the refund policy?")
    english = recording["seen"][-1]
    # The system prompt is the same whatever the question's language...
    assert isinstance(spanish[0], SystemMessage) and spanish[0].content == english[0].content
    assert "LANGUAGE:" not in spanish[0].content
    # ...and the language comes right after it, for this question only.
    assert isinstance(spanish[1], SystemMessage) and "Write the whole answer in Spanish" in \
        spanish[1].content and "[S#]" in spanish[1].content
    assert not any("LANGUAGE:" in str(m.content) for m in english)
    assert out["language"] == "es"
    assert "[S1]" in answer and sources                    # citations kept


class Translating:
    """The FAST model: translates search queries; allows everything else."""

    def __init__(self, asked):
        self.asked = asked

    def invoke(self, prompt):
        text = prompt if isinstance(prompt, str) else str(prompt)
        if text.startswith("Translate this search query"):
            self.asked.append(text)
            return AIMessage(content="What is the refund policy for a course?")
        return AIMessage(content="ALLOW")


@pytest.fixture
def translating(citing, monkeypatch):  # noqa: F811
    asked, searched = [], []
    monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: Translating(asked))
    real = agent.retrieve

    def retrieve(question, *args, **kwargs):
        searched.append(kwargs.get("also"))
        return real(question, *args, **kwargs)
    monkeypatch.setattr(agent, "retrieve", retrieve)
    return asked, searched


def test_cross_lingual_search_adds_a_translated_query(translating):
    asked, searched = translating
    run = build_agent("public", harness.COLLECTION, outline=False, prefs={"cross_lingual": True})
    answer, _, sources = run("¿Cuál es la política de reembolso del curso?", with_sources=True)
    assert len(asked) == 1 and "into English" in asked[0]
    assert searched[-1] == ["What is the refund policy for a course?"]
    assert any(s["doc_id"] for s in sources)
    # An English question about English documents: no translation.
    run("What is the refund policy?")
    assert len(asked) == 1 and searched[-1] is None


def test_cross_lingual_search_is_off_unless_turned_on(translating):
    asked, searched = translating
    build_agent("public", harness.COLLECTION, outline=False)(
        "¿Cuál es la política de reembolso del curso?")
    assert asked == [] and searched[-1] is None


def test_the_translation_reaches_only_the_meaning_search(monkeypatch):
    from test_assistant_settings import retriever, A
    monkeypatch.setattr(rag, "USE_HYDE", False)
    r = retriever([A], [A])
    rag.retrieve("política de reembolso", r, None, guard=lambda: (True, ""),
                 also=["refund policy"])
    assert r.dense.queries == ["política de reembolso", "refund policy"]
    assert r.bm25.queries == ["política de reembolso"]


def test_the_assistant_settings_api_takes_cross_lingual(env):  # noqa: F811
    w = env.workspace()
    admin = env.login(w)
    r = env.client.put("/api/admin/assistant", headers=admin, json={"cross_lingual": True})
    assert r.status_code == 200 and r.json()["cross_lingual"] is True
    assert env.client.get("/api/admin/assistant", headers=admin).json()["cross_lingual"] is True


def test_the_golden_set_has_non_english_questions_the_detector_agrees_with():
    rows = [r for r in harness.load_golden() if r.get("lang")]
    assert {r["lang"] for r in rows} >= {"hi", "hi-Latn", "es", "fr"}
    assert all(r["sources"] and r["expect"] == "answer" for r in rows)   # graded by retrieval
    assert [language.detect(r["question"]) for r in rows] == [r["lang"] for r in rows]

