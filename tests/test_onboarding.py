"""
A new workspace's first steps (kb/services/onboarding.py, kb/routers/onboarding.py;
platform.md PL-17), with BILLING off as it is by default: the checklist worked
out from the data, the first-run suggestions, and the sample documents, which
once removed never reach an answer again.

The sample documents' test uses the real index, retriever and job code with a
hash stand-in for the embedding model (tests/test_indexing.py's env).
"""

import os
import time
import uuid

import pytest

import agent
import auth
import db
import members_store
import question_log_store
import rag
import safety
from kb import state
from test_citations import Allow, CitingModel
from test_indexing import env  # noqa: F401  (a fixture)

PASSWORD = "correct-horse"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app, follow_redirects=False)


@pytest.fixture(autouse=True)
def in_tmp(monkeypatch, tmp_path):
    """Workspace documents live under data/workspaces/, here in a temporary folder."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def nothing_indexed(monkeypatch):
    from kb.services import indexing
    queued = []
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, **kw: queued.append((ws, kw)))
    return queued


def new_workspace():
    """Made at sign-up: a verified domain and its founder, an admin."""
    tag = uuid.uuid4().hex[:8]
    domain = f"onb-{tag}.example"
    ws = members_store.create_workspace(f"Onb {tag}", domain, f"founder@{domain}", "Fran")
    import billing_store
    billing_store.add_owner(ws, f"founder@{domain}")
    members_store.set_password(f"founder@{domain}", "Fran", auth.hash_password(PASSWORD), ws)
    return ws, domain


def login(client, email):
    r = client.post("/api/auth/login", json={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def steps(view):
    return {s["id"]: s["done"] for s in view["checklist"]["steps"]}


def test_the_checklist_is_worked_out_from_the_data_and_stays_done(client, nothing_indexed):
    import workspaces
    ws, domain = new_workspace()
    h = login(client, f"founder@{domain}")
    view = client.get("/api/onboarding", headers=h).json()
    assert steps(view) == {"domain": True, "source": False, "index": False, "answer": False,
                           "colleagues": False, "idp": False, "slack": False}
    assert view["checklist"]["complete"] is False and view["checklist"]["dismissed"] is False

    # Three uploads count as a source; the first index; an answer citing one of them.
    root = workspaces.docs_root(ws)
    os.makedirs(os.path.join(root, "public"), exist_ok=True)
    for n in range(3):
        with open(os.path.join(root, "public", f"doc{n}.txt"), "w") as f:
            f.write(f"Document {n}.")
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO search_indexes (workspace, collection, fingerprint, generation, "
                     "version, updated_at) VALUES (?, ?, 'x', 1, 1, ?)",
                     (ws, f"ws_{ws}", time.time()))
    conn.close()
    # An answer from the sample documents only isn't the workspace's own.
    question_log_store.add(ws, f"founder@{domain}", "web", "answered", text_mode="metadata",
                           sources=[{"doc_key": "sample/Leave_and_holidays.txt"}])
    assert steps(client.get("/api/onboarding", headers=h).json())["answer"] is False
    question_log_store.add(ws, f"founder@{domain}", "web", "answered", text_mode="metadata",
                           sources=[{"doc_key": "public/doc0.txt"}])
    # Three colleagues sign in (the founder, the billing owner, isn't one).
    for name in ("ana", "ben", "cy"):
        members_store.set_password(f"{name}@{domain}", name, auth.hash_password(PASSWORD), ws)
    for name in ("ana", "ben"):
        login(client, f"{name}@{domain}")
    assert steps(client.get("/api/onboarding", headers=h).json())["colleagues"] is False
    login(client, f"cy@{domain}")
    view = client.get("/api/onboarding", headers=h).json()
    assert steps(view) == {"domain": True, "source": True, "index": True, "answer": True,
                           "colleagues": True, "idp": False, "slack": False}
    assert view["checklist"]["complete"] is True           # the optional ones aren't needed

    # A step stays done though its data goes (the uploads deleted, say).
    for n in range(3):
        os.remove(os.path.join(root, "public", f"doc{n}.txt"))
    assert steps(client.get("/api/onboarding", headers=h).json())["source"] is True


def test_the_checklist_is_put_away_for_the_whole_workspace(client, nothing_indexed):
    ws, domain = new_workspace()
    members_store.set_password(f"other@{domain}", "Other", auth.hash_password(PASSWORD), ws,
                               "admin")
    founder, other = login(client, f"founder@{domain}"), login(client, f"other@{domain}")
    r = client.put("/api/onboarding/dismissed", headers=founder, json={"dismissed": True})
    assert r.status_code == 200 and r.json()["dismissed"] is True
    assert client.get("/api/onboarding", headers=other).json()["checklist"]["dismissed"] is True
    # Members get their first run, never the checklist; they can't put it away.
    members_store.set_password(f"pat@{domain}", "Pat", auth.hash_password(PASSWORD), ws)
    pat = login(client, f"pat@{domain}")
    assert "checklist" not in client.get("/api/onboarding", headers=pat).json()
    assert client.put("/api/onboarding/dismissed", headers=pat,
                      json={"dismissed": False}).status_code == 403


def test_the_first_run_suggests_questions_until_the_first_one(client, nothing_indexed):
    ws, domain = new_workspace()
    h = login(client, f"founder@{domain}")
    view = client.get("/api/onboarding", headers=h).json()
    # Nothing indexed yet: general questions.
    assert view["first_run"] is True and view["suggestions"][0] == "What is our leave policy?"
    question_log_store.add(ws, f"founder@{domain}", "web", "answered", text_mode="metadata")
    view = client.get("/api/onboarding", headers=h).json()
    assert view["first_run"] is False and view["suggestions"] == []


def test_suggestions_come_from_the_titles_people_may_read():
    from kb.services import onboarding
    items = [{"title": "Travel_policy.pdf", "chunks": 4, "modified": 3},
             {"title": "Not indexed.txt", "chunks": 0, "modified": 9},
             {"title": "leave-guide.md", "chunks": 2, "modified": 5}]
    assert onboarding.suggestions(items) == ["What does “leave guide” say?",
                                             "What does “Travel policy” say?"]


# ---- Sample documents -------------------------------------------------------------------
@pytest.fixture
def kb(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: CitingModel(script={}))
    monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: Allow())
    monkeypatch.setattr(safety, "_get_intent_llm", lambda: Allow())
    monkeypatch.setattr(rag, "USE_HYDE", False)
    state.agents.clear()
    yield env
    state.agents.clear()


def test_sample_documents_are_removed_from_answers_at_once(kb, monkeypatch):
    from kb.services import indexing, retrieval
    w = kb.workspace(("public", "travel.txt", "Our own travel policy: trains only."))
    admin = kb.login(w)
    staff = kb.login(w, "staff")
    assert kb.client.post("/api/onboarding/sample", headers=staff).status_code == 403
    r = kb.client.post("/api/onboarding/sample", headers=admin)
    assert r.status_code == 200 and r.json()["on"] is True and len(r.json()["files"]) == 3
    kb.wait(w)
    keys = {k for k, _ in kb.chunks(w).values()}
    assert {"sample/Leave_and_holidays.txt", "public/travel.txt"} <= keys
    found = retrieval.search(w, "public", "How many weeks of parental leave do parents get?")
    assert any(f["doc_key"].startswith("sample/") for f in found)
    assert found[0]["provider"] == "Sample documents"
    listed = kb.client.get("/api/knowledge", headers=admin).json()["items"]
    assert {i["source"] for i in listed} == {"Uploaded", "Sample"}
    # Sample documents are no upload: the Documents screen doesn't list them.
    docs = kb.client.get("/api/docs", headers=admin).json()
    assert [d["name"] for d in docs.get("docs", docs)] == ["travel.txt"]

    # One click removes them: no index run is needed for them to stop being found.
    schedule_index = indexing.schedule_index
    monkeypatch.setattr(indexing, "schedule_index", lambda ws, **kw: None)
    r = kb.client.delete("/api/onboarding/sample", headers=admin)
    assert r.status_code == 200 and r.json() == {"on": False, "available": True, "files": []}
    assert {k for k, _ in kb.chunks(w).values()} == {"public/travel.txt"}
    for q in ("How many weeks of parental leave do parents get?", "hotel limit per night",
              "Northwind laptop IT help"):
        assert not any(f["doc_key"].startswith("sample/")
                       for f in retrieval.search(w, "public", q))
    assert {i["source"] for i in kb.client.get("/api/knowledge", headers=admin).json()[
        "items"]} == {"Uploaded"}
    # A run after it (or a full rebuild) doesn't bring them back.
    monkeypatch.setattr(indexing, "schedule_index", schedule_index)
    indexing.index(w, full=True)
    assert {k for k, _ in kb.chunks(w).values()} == {"public/travel.txt"}
    assert not os.path.exists(f"data/workspaces/{w}/docs/sample")


def test_sample_documents_count_for_no_limit_or_step(nothing_indexed):
    from kb.services import billing, onboarding
    ws, _ = new_workspace()
    onboarding.add_sample(ws)
    assert billing.usage(ws)["docs"] == 0 and billing.usage(ws)["storage_mb"] == 0
    assert onboarding.state(ws)["steps"][1]["done"] is False          # source
    assert nothing_indexed == [(ws, {"docs": [f"sample/{f}" for f in onboarding.sample_files()]})]
    onboarding.remove_sample(ws)
