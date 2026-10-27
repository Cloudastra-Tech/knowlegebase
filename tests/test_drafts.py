"""
Drafts written by the assistant (platform.md PL-31; kb/services/drafts.py): a
source's or folder's summary (map-reduce, cited) and a doc for a content gap
(from passages a real search finds), through the API. A draft is never in the
search index, not even after a full rebuild, until HR publishes it; it can't be
published to a wider audience than its sources; publishing indexes it.

The index is real (Chroma, ingest, the hybrid search) with a stand-in embedding;
the models are fakes (summaries.llm, and test_citations' for the agent).
"""

import os
import re
import time

import pytest

import agent
import digests_store
import drafts_store
import rag
import safety
from kb.services import drafts, indexing, insights, summaries
from test_citations import Allow, CitingModel
from test_indexing import env  # noqa: F401  (a fixture)

LEAVE = ("# Leave policy\n\nEvery employee gets twenty days of annual leave a year. "
         "Unused leave carries over up to five days.")
PARENTAL = ("# Family leave\n\nParental leave is sixteen weeks at full pay for either "
            "parent, taken within a year of the birth or adoption.")
PAY = "# Pay bands\n\nTeachers are paid sixty thousand rupees a month."


@pytest.fixture
def setup(env, monkeypatch):  # noqa: F811
    """A workspace with two public documents and a restricted one, indexed. The
    model's replies: a summary per document, an overview citing [1] and [2] (and a
    [9] that points at nothing), a gap draft citing [1]. Every prompt is kept."""
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: CitingModel(script={"calls": 0}))
    monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: Allow())
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Allow())
    monkeypatch.setattr(rag, "USE_HYDE", False)
    prompts = []

    def llm(prompt, max_tokens=400):
        prompts.append(prompt)
        if prompt.startswith("You summarise one"):
            title = re.search(r'title="([^"]*)"', prompt).group(1)
            return f"About {title}."
        if prompt.startswith("You write an overview"):
            return "Overview\nThe documents cover leave [1] and pay [2] [9]."
        return "Parental leave\nParental leave is sixteen weeks [1].\nTO CONFIRM: who approves it."
    monkeypatch.setattr(summaries, "llm", llm)
    w = env.workspace(("public", "leave.txt", LEAVE), ("public", "parental.txt", PARENTAL),
                      ("restricted", "pay.txt", PAY))
    indexing.schedule_index(w)
    env.wait(w)
    env.ws, env.prompts = w, prompts
    env.admin, env.staff = env.login(w), env.login(w, "staff")
    return env


def ready(s, draft_id):
    deadline = time.time() + 20
    while time.time() < deadline:
        d = s.client.get(f"/api/drafts/{draft_id}", headers=s.admin).json()
        if d["status"] != "generating":
            return d
        time.sleep(0.05)
    raise AssertionError("the draft wasn't written")


def summarise(s, **body):
    r = s.client.post("/api/drafts/summarise", headers=s.admin, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "generating"
    return ready(s, r.json()["id"])


def indexed_keys(s):
    return {key for key, _ in s.chunks(s.ws).values()}


# ---- Summaries ------------------------------------------------------------------------------
def test_a_source_is_summarised_as_a_cited_draft_that_isnt_indexed(setup):
    d = summarise(setup, provider="uploads")
    assert d["status"] == "draft" and d["kind"] == "summary"
    assert {s["doc_key"] for s in d["sources"]} == {"public/leave.txt", "public/parental.txt",
                                                    "restricted/pay.txt"}
    assert d["max_access"] == "restricted"
    text = d["content"]
    assert "leave [1] and pay [2]" in text and "[9]" not in text   # a made-up citation is dropped
    assert re.search(r"(?m)^Sources$", text) and "[1] " in text.split("Sources", 1)[1]
    # The map step: each document summarised from its own text, as data.
    maps = [p for p in setup.prompts if p.startswith("You summarise one")]
    assert len(maps) == 3 and all("<untrusted_data" in p for p in maps)
    # The draft is a file under drafts/, and no search index has it, even rebuilt.
    assert os.path.isfile(drafts_store.path(setup.ws, d["id"]))
    indexing.schedule_index(setup.ws, full=True)
    setup.wait(setup.ws)
    assert not any(k.startswith("drafts/") for k in indexed_keys(setup))
    # Nor is anything published by itself.
    assert setup.client.get("/api/docs", headers=setup.admin).json()["docs"].__len__() == 3


def test_a_folder_summary_covers_only_that_folder(setup):
    d = summarise(setup, provider="uploads", folder="public")
    assert {s["doc_key"] for s in d["sources"]} == {"public/leave.txt", "public/parental.txt"}
    assert d["max_access"] == "public"
    # The map step's summaries are the digests' too: cached per version, not made again.
    before = len(setup.prompts)
    summarise(setup, provider="uploads", folder="public")
    assert [p[:20] for p in setup.prompts[before:]] == ["You write an overvie"]


def test_only_hr_drafts_and_sees_drafts(setup):
    assert setup.client.post("/api/drafts/summarise", headers=setup.staff,
                             json={"provider": "uploads"}).status_code == 403
    assert setup.client.get("/api/drafts", headers=setup.staff).status_code == 403
    assert setup.client.post("/api/drafts/summarise", headers=setup.admin,
                             json={"provider": "jira"}).status_code == 404
    assert setup.client.post("/api/drafts/summarise", headers=setup.admin,
                             json={"provider": "uploads", "folder": "nope"}).status_code == 400


# ---- Publishing ------------------------------------------------------------------------------
def test_publishing_indexes_it_at_no_lower_level_than_its_sources(setup):
    d = summarise(setup, provider="uploads")
    publish = lambda **b: setup.client.post(f"/api/drafts/{d['id']}/publish",  # noqa: E731
                                            headers=setup.admin, json=b)
    r = publish(acl=["everyone"], name="Overview")
    assert r.status_code == 400 and "aren't for" in r.json()["detail"]
    # An edit first (on the Documents screen), that drops the sources list by mistake.
    r = setup.client.patch(f"/api/drafts/{d['id']}", headers=setup.admin,
                           json={"content": "Overview\nEdited by HR [1].", "title": "Overview"})
    assert r.status_code == 200
    r = publish(acl=["group:hr"], name="Overview")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"
    assert r.json()["published_as"] == "restricted/Overview.txt"
    assert not os.path.exists(drafts_store.path(setup.ws, d["id"]))
    with open(os.path.join("data", "workspaces", setup.ws, "docs", "restricted",
                           "Overview.txt")) as f:
        published = f.read()
    assert published.startswith("Overview\nEdited by HR [1].")
    assert "\nSources\n[1] " in published                 # put back: drafts list their sources
    setup.wait(setup.ws)
    assert "restricted/Overview.txt" in indexed_keys(setup)
    assert [c["change"] for c in digests_store.changes(setup.ws, 0)
            if c["doc_key"] == "restricted/Overview.txt"] == ["added"]
    # Published once: it's no longer a draft to edit or publish.
    assert publish(acl=["group:hr"], name="Again").status_code == 409
    assert setup.client.get("/api/drafts", headers=setup.admin).json()["drafts"] == []


def test_a_public_folder_summary_can_be_published_to_everyone(setup):
    d = summarise(setup, provider="uploads", folder="public")
    r = setup.client.post(f"/api/drafts/{d['id']}/publish", headers=setup.admin,
                          json={"acl": ["everyone"], "name": "leave-overview.txt"})
    assert r.status_code == 200, r.text
    setup.wait(setup.ws)
    assert "public/leave-overview.txt" in indexed_keys(setup)


def test_a_draft_from_a_group_document_stays_with_that_group(setup):
    """Written from a document shared with one group (and an HR-only one), the
    draft is for people in both only: HR outside the group can't open it, and it
    can be published to neither the group alone nor HR alone, only to someone
    who may read every source."""
    import auth
    import groups_store
    import members_store
    ws = setup.ws
    members_store.set_password(f"hr2@{ws}.example", "Other HR", auth.hash_password("correct-horse"),
                               ws, "hr")
    g = groups_store.create(ws, "Board", "", "admin")
    groups_store.set_members(ws, g["id"], [f"admin@{ws}.example", f"staff@{ws}.example"],
                             "admin")
    board = f"group:{g['id']}"
    r = setup.client.post("/api/docs", headers=setup.admin,
                          json={"name": "board.txt", "access": "restricted", "acl": [board],
                                "content": "# Board\n\nThe board meets monthly."})
    assert r.status_code == 200, r.text
    setup.wait(ws)
    d = summarise(setup, provider="uploads", folder="restricted")
    assert {s["doc_key"] for s in d["sources"]} == {"restricted/board.txt", "restricted/pay.txt"}
    assert sorted(d["audience"]) == sorted([[board], ["group:hr"]])
    other = setup.login(ws, "hr2")
    other["X-Role"] = "hr"
    assert d["id"] not in [x["id"] for x in setup.client.get("/api/drafts", headers=other)
                           .json()["drafts"]]
    assert setup.client.get(f"/api/drafts/{d['id']}", headers=other).status_code == 404
    publish = lambda acl: setup.client.post(f"/api/drafts/{d['id']}/publish",  # noqa: E731
                                            headers=setup.admin,
                                            json={"acl": acl, "name": "board-summary.txt"})
    assert publish([board]).status_code == 400            # the group can't read pay.txt
    assert publish(["group:hr"]).status_code == 400        # HR can't all read board.txt
    assert publish([f"user:staff@{ws}.example"]).status_code == 400   # not HR
    r = publish([f"user:admin@{ws}.example"])               # in the group, and HR
    assert r.status_code == 200, r.text
    assert groups_store.doc_acl(ws, "restricted/board-summary.txt") == [
        f"user:admin@{ws}.example"]


def test_a_deleted_draft_is_gone(setup):
    d = summarise(setup, provider="uploads", folder="public")
    assert setup.client.delete(f"/api/drafts/{d['id']}", headers=setup.admin).status_code == 200
    assert not os.path.exists(drafts_store.path(setup.ws, d["id"]))
    assert setup.client.get(f"/api/drafts/{d['id']}", headers=setup.admin).status_code == 404


# ---- Content gaps -----------------------------------------------------------------------------
def test_a_gap_is_drafted_from_passages_the_search_finds(setup, monkeypatch):
    gap = {"id": 7, "label": "Parental leave", "hidden": False, "size": 4,
           "samples": ["How long is parental leave?", "Is adoption leave paid?"]}
    monkeypatch.setattr(insights, "gaps_for", lambda ws, role, mode: [
        gap, {**gap, "id": 8, "hidden": True, "label": None}])
    r = setup.client.post("/api/drafts/from-gap", headers=setup.admin, json={"gap_id": 7})
    assert r.status_code == 200, r.text
    d = ready(setup, r.json()["id"])
    assert d["status"] == "draft" and d["kind"] == "gap"
    assert d["title"] == "Draft - Parental leave"
    assert "public/parental.txt" in {s["doc_key"] for s in d["sources"]}
    (prompt,) = [p for p in setup.prompts if p.startswith("You draft a new")]
    assert "sixteen weeks" in prompt and "How long is parental leave?" in prompt
    assert "TO CONFIRM: who approves it." in d["content"]
    assert "\nSources\n[1] " in d["content"]
    # A gap hidden from the role, or gone, can't be drafted.
    assert setup.client.post("/api/drafts/from-gap", headers=setup.admin,
                             json={"gap_id": 8}).status_code == 404


def test_a_failed_draft_says_why(setup, monkeypatch):
    monkeypatch.setattr(drafts, "scope_docs", lambda *a, **k: [{"doc_key": "public/x.txt",
                                                                "title": "x.txt"}])
    monkeypatch.setattr(summaries, "summarise_doc", lambda *a, **k: None)
    r = setup.client.post("/api/drafts/summarise", headers=setup.admin,
                          json={"provider": "uploads"})
    d = ready(setup, r.json()["id"])
    assert d["status"] == "failed" and "could be read" in d["error"]
