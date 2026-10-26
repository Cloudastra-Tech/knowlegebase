"""
Re-indexing as jobs (kb/services/indexing.py, ingest.update / ingest.main):
workspaces index side by side, a changed or deleted file touches only its own
chunks, a job cut short is finished later, questions are answered from the old
index while a full rebuild builds the new one (and if it fails), and an access
change re-tags chunks without embedding them again.

Only the embedding model is faked (eval.harness.HashEmbeddings, counted); the
job runner, Chroma, the API and the retriever are the real code.
"""

import json
import os
import threading
import time
import uuid

import pytest

from eval.harness import HashEmbeddings

LONG = " ".join(f"Paragraph {n} of the travel policy says trips need approval number {n}."
                for n in range(40))


class Counting(HashEmbeddings):
    """HashEmbeddings that counts what it embeds; `hook` runs before each batch."""

    def __init__(self, calls, hook):
        super().__init__(dim=64)
        self.calls, self.hook = calls, hook

    def embed_documents(self, texts):
        self.hook(texts)
        self.calls.append(len(texts))
        return super().embed_documents(texts)


@pytest.fixture
def env(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import api
    import auth
    import ingest
    import members_store
    import rag

    calls, hooks = [], []
    fake = lambda **kwargs: Counting(calls, lambda texts: [h(texts) for h in hooks])  # noqa: E731
    for module in (ingest, rag):
        monkeypatch.setattr(module, "OpenAIEmbeddings", fake)
        monkeypatch.setattr(module, "DB_FOLDER", str(tmp_path / "chroma"))
    monkeypatch.chdir(tmp_path)            # workspace documents live under data/workspaces/

    class Env:
        client = TestClient(api.app)
        embedded = calls                  # chunks per embedding call
        before_embed = hooks              # functions(texts) run before each call

        def workspace(self, *docs):
            """A new workspace with an admin, and these (access, name, text) uploads."""
            import workspaces
            w = f"acme-{uuid.uuid4().hex[:8]}"
            members_store.set_password(f"admin@{w}.example", "Admin",
                                       auth.hash_password("correct-horse"), w, "admin")
            members_store.set_password(f"staff@{w}.example", "Staff",
                                       auth.hash_password("correct-horse"), w, "public")
            for access, name, text in docs:
                os.makedirs(os.path.join(workspaces.docs_root(w), access), exist_ok=True)
                with open(os.path.join(workspaces.docs_root(w), access, name), "w") as f:
                    f.write(text)
            return w

        def login(self, w, name="admin"):
            r = self.client.post("/api/auth/login", json={"username": f"{name}@{w}.example",
                                                          "password": "correct-horse"})
            assert r.status_code == 200, r.text
            return {"Authorization": f"Bearer {r.json()['token']}",
                    "X-Role": "hr" if name == "admin" else "public"}

        def wait(self, w, expect="idle"):
            from kb.services import indexing
            deadline = time.time() + 30
            while time.time() < deadline:
                state = indexing.index_status(w)
                if state["status"] != "indexing":
                    assert state["status"] == expect, state
                    return state
                time.sleep(0.05)
            raise AssertionError("indexing didn't finish")

        def chunks(self, w):
            """{chunk id: (doc key, access)} in the collection the workspace answers from."""
            import ingest
            from kb.services import indexing
            got = ingest._store(indexing.current(w)[0])._collection.get(include=["metadatas"])
            return {i: (m["doc_key"], m["access"]) for i, m in zip(got["ids"], got["metadatas"])}

    yield Env()
    hooks.clear()


def ids_of(chunks, key):
    return {i for i, (k, _) in chunks.items() if k == key}


def test_two_workspaces_index_at_the_same_time(env):
    """Each waits inside its embedding call for the other to arrive: with one
    lock across workspaces (as before) the second never would."""
    from kb.services import indexing
    a = env.workspace(("public", "a.txt", "Alpha handbook."))
    b = env.workspace(("public", "b.txt", "Beta handbook."))
    both = threading.Barrier(2, timeout=10)
    env.before_embed.append(lambda texts: both.wait())
    indexing.schedule_index(a)
    indexing.schedule_index(b)
    assert env.wait(a)["summary"]["chunks"] == 1
    assert env.wait(b)["summary"]["chunks"] == 1


def test_a_changed_or_deleted_file_touches_only_its_own_chunks(env):
    from kb.services import indexing
    w = env.workspace(("public", "travel.txt", LONG), ("public", "leave.txt", "Leave: 20 days."),
                      ("restricted", "pay.txt", "Salaries are secret."))
    admin = env.login(w)
    indexing.schedule_index(w)
    first = env.wait(w)["summary"]
    before = env.chunks(w)
    travel = ids_of(before, "public/travel.txt")
    assert first["documents"] == 3 and len(travel) > 3
    assert sum(env.embedded) == len(before) == first["chunks"]
    assert before[next(iter(ids_of(before, "restricted/pay.txt")))][1] == "restricted"

    # Edit the end of a long document: only its last chunks are embedded again.
    env.embedded.clear()
    r = env.client.put("/api/docs/public/travel.txt", headers=admin,
                       json={"content": LONG + " Taxis need a receipt."})
    assert r.status_code == 200
    summary = env.wait(w)["summary"]
    after = env.chunks(w)
    assert 0 < sum(env.embedded) == summary["embedded"] < len(travel)
    assert summary["changed"] == 1 and summary["added"] == summary["removed"] == 0
    assert len(travel & ids_of(after, "public/travel.txt")) >= len(travel) - 2
    for key in ("public/leave.txt", "restricted/pay.txt"):
        assert ids_of(after, key) == ids_of(before, key)

    # Delete a document: its chunks go, nothing is embedded.
    env.embedded.clear()
    assert env.client.delete("/api/docs/public/leave.txt", headers=admin).status_code == 200
    summary = env.wait(w)["summary"]
    assert env.embedded == [] and summary["removed"] == 1
    gone = env.chunks(w)
    assert not ids_of(gone, "public/leave.txt")
    assert {i: v for i, v in after.items() if v[0] != "public/leave.txt"} == gone

    # A new document embeds just its own chunks.
    r = env.client.post("/api/docs", headers=admin,
                        json={"name": "faq.txt", "access": "public", "content": "Wifi: guest."})
    assert r.status_code == 200
    assert env.wait(w)["summary"]["added"] == 1 and env.embedded == [1]

    # Re-index by hand with nothing changed: nothing is embedded.
    env.embedded.clear()
    assert env.client.post("/api/reindex", headers=admin).status_code == 200
    assert env.wait(w)["summary"]["embedded"] == 0 and env.embedded == []


def test_a_run_cut_short_is_finished_by_the_next(env, monkeypatch):
    """New chunks are written before old ones are deleted, and a document whose
    chunks don't agree counts as changed: the next run cleans up."""
    import ingest
    from kb.services import indexing
    w = env.workspace(("public", "travel.txt", LONG))
    indexing.schedule_index(w)
    env.wait(w)
    with open(f"data/workspaces/{w}/docs/public/travel.txt", "w") as f:
        f.write("Travel is now booked by the office manager only.")

    real_store = ingest._store

    def store_that_dies_on_delete(collection):
        store = real_store(collection)
        store._collection.delete = lambda **kwargs: (_ for _ in ()).throw(OSError("killed"))
        return store

    monkeypatch.setattr(ingest, "_store", store_that_dies_on_delete)
    with pytest.raises(OSError):
        indexing.index(w)
    assert len(ids_of(env.chunks(w), "public/travel.txt")) > 1       # old and new, for now
    monkeypatch.setattr(ingest, "_store", real_store)
    env.embedded.clear()
    summary = indexing.index(w)
    assert env.embedded == [] and summary["chunks"] == 1             # already embedded


def test_a_crashed_index_job_is_claimed_again_after_its_lease(env, jobs_paused):
    from kb import jobs
    from kb.services import indexing
    w = env.workspace(("public", "a.txt", "Alpha handbook."))
    indexing.schedule_index(w)
    dead = jobs.claim("dead-worker", ["index"], lease=0.3)          # dies before running it
    assert dead.workspace == w
    assert indexing.index_status(w)["status"] == "indexing"
    runner = jobs.Runner(handlers={"index": jobs.HANDLERS["index"]}, threads=0)
    while runner.run_once():           # anything else queued (none of it this workspace's)
        pass
    assert jobs.get(dead.id)["status"] == "running"                   # not before its lease ends
    time.sleep(0.4)
    while jobs.get(dead.id)["status"] != "done" and runner.run_once():
        pass
    row = jobs.get(dead.id)
    assert (row["status"], row["attempts"]) == ("done", 2)
    state = indexing.index_status(w)
    assert state["status"] == "idle" and state["summary"]["chunks"] == 1


@pytest.fixture
def fake_agent(monkeypatch):
    """An agent whose answer is the text of the chunks it retrieved."""
    import rag
    from kb.services import agents

    def build(role, collection, outline):
        retriever, _ = rag.load_chain(role, collection)

        def run(question, with_sources=False, **kwargs):
            return " | ".join(d.page_content for d in retriever.invoke(question)), [], []
        return run
    monkeypatch.setattr(agents, "build_agent", build)


def ask(env, headers):
    r = env.client.post("/api/chat", headers=headers, json={"question": "Which vendor?"})
    assert r.status_code == 200, r.text            # never a 503 while indexing
    return r.json()["answer"]


def test_questions_use_the_old_index_while_a_full_rebuild_builds(env, fake_agent, monkeypatch):
    from kb.services import indexing
    monkeypatch.setattr(indexing, "DROP_AFTER", 0)
    w = env.workspace(("public", "vendor.txt", "The vendor is Oldco."))
    admin = env.login(w)
    indexing.schedule_index(w)
    env.wait(w)
    old = indexing.current(w)[0]
    assert "Oldco" in ask(env, admin)

    with open(f"data/workspaces/{w}/docs/public/vendor.txt", "w") as f:
        f.write("The vendor is Newco.")
    building, release = threading.Event(), threading.Event()
    env.before_embed.append(lambda texts: (building.set(), release.wait(10)))
    assert env.client.post("/api/reindex?full=true", headers=admin).status_code == 200
    assert building.wait(10)
    assert indexing.index_status(w)["status"] == "indexing"
    assert indexing.current(w)[0] == old and "Oldco" in ask(env, admin)
    release.set()

    assert env.wait(w)["summary"]["chunks"] == 1
    new = indexing.current(w)[0]
    assert new != old and new.startswith(f"ws_{w}__g")
    assert "Newco" in ask(env, admin)              # the agent noticed the switch
    # ...and the old collection is dropped (here at once: DROP_AFTER = 0).
    import chromadb
    import ingest
    deadline = time.time() + 10
    while old in {c.name for c in chromadb.PersistentClient(path=ingest.DB_FOLDER)
                  .list_collections()} and time.time() < deadline:
        time.sleep(0.05)
    assert time.time() < deadline


def test_a_failed_rebuild_leaves_the_old_index_answering(env, fake_agent, monkeypatch):
    import chromadb
    import ingest
    from kb import jobs
    from kb.services import indexing
    w = env.workspace(("public", "vendor.txt", "The vendor is Oldco."))
    admin = env.login(w)
    indexing.schedule_index(w)
    env.wait(w)
    old = indexing.current(w)[0]

    monkeypatch.setitem(jobs.HANDLERS, "index", jobs.HANDLERS["index"]._replace(max_attempts=1))

    def broken(texts):
        raise ConnectionError("embedding service down")
    env.before_embed.append(broken)
    assert env.client.post("/api/reindex?full=true", headers=admin).status_code == 200
    state = env.wait(w, expect="error")
    assert state["error"] == "ConnectionError: embedding service down"
    assert indexing.current(w)[0] == old
    names = {c.name for c in chromadb.PersistentClient(path=ingest.DB_FOLDER).list_collections()}
    assert old in names and not any(n.startswith(f"ws_{w}__g2") for n in names)
    assert "Oldco" in ask(env, admin)


def test_an_index_from_before_stable_ids_is_rebuilt_beside_it(env):
    """No row in search_indexes (an old deploy): the first run builds a new
    collection while the old one answers, then queues the old one's drop."""
    import ingest
    import workspaces
    from kb import jobs
    from kb.services import indexing
    w = env.workspace(("public", "a.txt", "Alpha handbook."))
    ingest.main(docs_folder=workspaces.docs_root(w), collection=workspaces.collection(w))
    assert indexing.current(w) == (f"ws_{w}", 0)
    indexing.schedule_index(w, docs=["public/a.txt"])
    assert env.wait(w)["summary"]["chunks"] == 1
    assert indexing.current(w)[0] == f"ws_{w}__g1"
    [drop] = [r for r in jobs.lane_jobs(indexing.lane(w), ["drop_collection"])
              if r["status"] == "queued"]
    assert json.loads(drop["payload"]) == {"collection": f"ws_{w}"}
    assert drop["run_after"] > time.time() + indexing.DROP_AFTER - 60


def test_new_chunking_settings_rebuild_the_index(env, monkeypatch):
    import ingest
    from kb.services import indexing
    w = env.workspace(("public", "a.txt", "Alpha handbook."))
    indexing.schedule_index(w)
    env.wait(w)
    monkeypatch.setattr(ingest, "FINGERPRINT", "other-settings")
    indexing.schedule_index(w, docs=[])
    env.wait(w)
    assert indexing.current(w)[0] == f"ws_{w}__g2"


def test_an_access_change_re_tags_without_embedding(env):
    """Opening a connection to everyone (or closing it again) only re-tags its
    chunks; uploads keep theirs."""
    import integrations_store
    import workspaces
    from kb.services import indexing
    w = env.workspace(("restricted", "pay.txt", "Salaries are secret."))
    integrations_store.connect(w, "notion", "Acme", {"access_token": "x"}, "admin")
    folder = workspaces.connector_dir(w, "notion")
    os.makedirs(folder)
    with open(os.path.join(folder, "page1.txt"), "w") as f:
        f.write("Launch plan\n\n" + LONG)
    with open(os.path.join(folder, "manifest.json"), "w") as f:
        json.dump({"page1.txt": {"id": "page1", "title": "Launch plan", "provider": "Notion"}}, f)
    admin = env.login(w)
    indexing.schedule_index(w)
    env.wait(w)
    embedded = sum(env.embedded)

    def access():
        levels = {}
        for key, level in env.chunks(w).values():
            levels.setdefault(key, set()).add(level)
        return levels

    assert access() == {"connectors/notion/page1.txt": {"restricted"},
                        "restricted/pay.txt": {"restricted"}}
    for level, tag in (("everyone", "public"), ("restricted", "restricted")):
        r = env.client.put("/api/integrations/notion/access", json={"access": level},
                           headers=admin)
        assert r.status_code == 200, r.text
        env.wait(w)
        assert access() == {"connectors/notion/page1.txt": {tag},
                            "restricted/pay.txt": {"restricted"}}
    assert sum(env.embedded) == embedded
    # An incremental run agrees: nothing to re-tag or embed.
    assert indexing.index(w)["retagged"] == 0 and sum(env.embedded) == embedded
