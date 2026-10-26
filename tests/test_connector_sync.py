"""
The connector framework (kb/services/sync.py, sync_store.py) with a fake
connector: cursors and incremental runs, skip-if-unchanged, exact documents
re-indexed, the mass-delete guard, failures that keep the cursor and delete
nothing, typed errors shown in plain words, sync history, the item limit, and
the one-time import of manifest.json files into source_items.

The provider-specific code (Drive, Notion, Outline, website) is tested against
recorded HTTP in test_connector_providers.py.
"""

import json
import os
import shutil
import time
import uuid

import pytest

from connectors import base

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "connectors")


class Fake(base.Connector):
    """A source held in memory. A full listing (no cursor) yields every document
    and deletes the rest; an incremental run yields `feed` and moves the cursor."""
    PROVIDER = "fake"
    LABEL = "Fake source"
    AUTH = "token"
    UNIT = "documents"

    def __init__(self):
        self.docs = {}            # id -> (title, version, text)
        self.feed = []            # events of the next incremental run
        self.fetched = []
        self.cursors = []
        self.fail_after = None    # raise this after yielding the feed

    def add(self, sid, title, version, text):
        self.docs[sid] = (title, version, text)

    def item(self, sid):
        title, version, _ = self.docs[sid]
        return base.Item(source_id=sid, title=title, url=f"https://fake.example/{sid}",
                         version=version, kind="page", mime="text/plain")

    def changes(self, ctx, cursor):
        self.cursors.append(cursor)
        if cursor is None:
            for sid in self.docs:
                yield base.Upsert(self.item(sid))
            yield from ctx.missing(self.docs)
            yield base.Cursor("1", full=True)
            return
        yield from self.feed
        if self.fail_after:
            raise self.fail_after
        yield base.Cursor(str(int(cursor) + 1))

    def fetch(self, ctx, item):
        self.fetched.append(item.source_id)
        return base.Fetched(self.docs[item.source_id][2], "text/plain")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """A workspace with the fake source connected; schedule_index records the
    documents it's asked to re-index instead of indexing them."""
    import integrations_store
    from connectors import registry
    from kb.services import sync
    monkeypatch.chdir(tmp_path)
    connector = Fake()
    monkeypatch.setitem(registry.CONNECTORS, "fake", connector)
    reindexed = []
    monkeypatch.setattr(sync.indexing, "schedule_index",
                        lambda ws, docs=None, full=False: reindexed.append(sorted(docs or [])))
    ws = f"w-{uuid.uuid4().hex[:8]}"
    integrations_store.connect(ws, "fake", "Fake account", {"token": "t"}, "admin")
    connector.ws, connector.reindexed = ws, reindexed
    return connector


def files(ws, provider="fake"):
    import workspaces
    folder = workspaces.connector_dir(ws, provider)
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def test_first_sync_lists_everything_then_only_what_changed(fake):
    import sync_store
    from kb.services import sync
    fake.add("a", "Alpha", "v1", "Alpha text")
    fake.add("b", "Beta", "v1", "Beta text")
    summary = sync.sync(fake.ws, "fake")
    assert (summary["mode"], summary["upserted"]) == ("full", 2)
    assert fake.cursors == [None] and sorted(fake.fetched) == ["a", "b"]
    assert files(fake.ws) == ["a.txt", "b.txt"]
    assert fake.reindexed == [["connectors/fake/a.txt", "connectors/fake/b.txt"]]
    with open(f"data/workspaces/{fake.ws}/docs/connectors/fake/a.txt") as f:
        assert f.read() == "Alpha\n\nAlpha text"          # a title line, then the text
    assert sync_store.state(fake.ws, "fake")["cursor"] == "1"

    # Next run: from the cursor; an unchanged document isn't fetched, an edited one is.
    fake.fetched.clear()
    fake.docs["b"] = ("Beta", "v2", "Beta, edited")
    fake.feed = [base.Upsert(fake.item("a")), base.Upsert(fake.item("b"))]
    summary = sync.sync(fake.ws, "fake")
    assert fake.cursors[-1] == "1" and summary["mode"] == "incremental"
    assert fake.fetched == ["b"]
    assert (summary["upserted"], summary["unchanged"]) == (1, 1)
    assert fake.reindexed[-1] == ["connectors/fake/b.txt"]       # exactly what changed
    assert sync_store.state(fake.ws, "fake")["cursor"] == "2"


def test_a_new_version_with_the_same_text_isnt_reindexed(fake):
    from kb.services import sync
    fake.add("a", "Alpha", "v1", "Same text")
    sync.sync(fake.ws, "fake")
    fake.docs["a"] = ("Alpha", "v2", "Same text")
    fake.feed = [base.Upsert(fake.item("a"))]
    fake.reindexed.clear()
    assert sync.sync(fake.ws, "fake")["unchanged"] == 1
    assert fake.reindexed == []


def test_a_rename_is_recorded_and_reindexed_without_fetching(fake):
    import sync_store
    from kb.services import sync
    fake.add("a", "Alpha", "v1", "Alpha text")
    sync.sync(fake.ws, "fake")
    fake.fetched.clear()
    fake.docs["a"] = ("Alpha (renamed)", "v1", "Alpha text")
    fake.feed = [base.Upsert(fake.item("a"))]
    sync.sync(fake.ws, "fake")
    assert fake.fetched == []
    assert sync_store.item(fake.ws, "fake", "a")["title"] == "Alpha (renamed)"
    assert fake.reindexed[-1] == ["connectors/fake/a.txt"]       # citations show the new name


def test_permission_changes_are_kept_without_reindexing(fake):
    import sync_store
    from kb.services import sync
    fake.add("a", "Alpha", "v1", "Alpha text")
    sync.sync(fake.ws, "fake")
    fake.reindexed.clear()
    fake.feed = [base.Permissions("a", {"users": ["ann@acme.com"]})]
    sync.sync(fake.ws, "fake")
    assert sync_store.item(fake.ws, "fake", "a")["acl"] == {"users": ["ann@acme.com"]}
    assert fake.reindexed == []


def test_a_mass_delete_is_held_until_an_admin_confirms(fake):
    import sync_store
    from kb.services import sync
    for n in range(20):
        fake.add(f"d{n}", f"Doc {n}", "v1", f"Text {n}")
    sync.sync(fake.ws, "fake")
    fake.reindexed.clear()

    # The source suddenly lists half as much (an outage, an unshared folder).
    fake.feed = [base.Delete(f"d{n}") for n in range(10)]
    summary = sync.sync(fake.ws, "fake")
    assert summary["held_deletes"] == 10 and summary["deleted"] == 0
    assert len(files(fake.ws)) == 20 and fake.reindexed == []
    state = sync_store.state(fake.ws, "fake")
    assert state["cursor"] == "1" and state["pending_deletes"] == 10   # same place next time
    assert sync_store.last_run(fake.ws, "fake")["status"] == "held"

    # Asked again without confirming: still held. Confirmed: applied.
    assert sync.sync(fake.ws, "fake")["held_deletes"] == 10
    summary = sync.sync(fake.ws, "fake", confirm_deletes=True)
    assert summary["deleted"] == 10 and len(files(fake.ws)) == 10
    assert fake.reindexed[-1] == sorted(f"connectors/fake/d{n}.txt" for n in range(10))
    assert sync_store.state(fake.ws, "fake")["pending_deletes"] is None


def test_a_few_deletes_go_through(fake):
    from kb.services import sync
    for n in range(20):
        fake.add(f"d{n}", f"Doc {n}", "v1", f"Text {n}")
    sync.sync(fake.ws, "fake")
    fake.feed = [base.Delete("d1"), base.Delete("d2"), base.Delete("not-ours")]
    assert sync.sync(fake.ws, "fake")["deleted"] == 2
    assert "d1.txt" not in files(fake.ws) and len(files(fake.ws)) == 18


def test_the_mass_delete_limits_come_from_the_environment(fake, monkeypatch):
    from kb.services import sync
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_MIN", "2")
    monkeypatch.setenv("CONNECTOR_MASS_DELETE_PERCENT", "50")
    for n in range(4):
        fake.add(f"d{n}", f"Doc {n}", "v1", f"Text {n}")
    sync.sync(fake.ws, "fake")
    fake.feed = [base.Delete("d0"), base.Delete("d1"), base.Delete("d2")]
    assert sync.sync(fake.ws, "fake")["held_deletes"] == 3


def test_a_failed_run_keeps_its_cursor_deletes_nothing_and_indexes_what_it_wrote(fake):
    import integrations_store
    import sync_store
    from kb.services import sync
    fake.add("a", "Alpha", "v1", "Alpha text")
    fake.add("b", "Beta", "v1", "Beta text")
    sync.sync(fake.ws, "fake")
    fake.reindexed.clear()
    fake.docs["c"] = ("Gamma", "v1", "Gamma text")
    fake.feed = [base.Upsert(fake.item("c")), base.Delete("a")]
    fake.fail_after = base.Transient("GET https://fake.example/changes: HTTP 503")
    with pytest.raises(base.Transient):
        sync.sync(fake.ws, "fake")
    assert files(fake.ws) == ["a.txt", "b.txt", "c.txt"]          # nothing deleted
    assert fake.reindexed == [["connectors/fake/c.txt"]]           # what it wrote is searchable
    state = sync_store.state(fake.ws, "fake")
    assert state["cursor"] == "1" and state["consecutive_failures"] == 1
    row = integrations_store.get(fake.ws, "fake")
    assert (row["status"], row["last_error"]) == ("error", "transient")
    run = sync_store.last_run(fake.ws, "fake")
    assert (run["status"], run["error_code"], run["upserted"]) == ("failed", "transient", 1)


@pytest.fixture
def admin(fake):
    from fastapi.testclient import TestClient
    import api
    import auth
    import members_store
    members_store.set_password(f"admin@{fake.ws}.example", "Admin",
                               auth.hash_password("correct-horse"), fake.ws, "admin")
    client = TestClient(api.app)
    r = client.post("/api/auth/login", json={"username": f"admin@{fake.ws}.example",
                                             "password": "correct-horse"})
    return client, {"Authorization": f"Bearer {r.json()['token']}", "X-Role": "hr"}


def card(client, headers, provider="fake"):
    items = client.get("/api/integrations", headers=headers).json()["integrations"]
    return next(i for i in items if i["id"] == provider)


@pytest.mark.parametrize("error, code", [
    (base.AuthExpired("token secret-detail-123 refused"), "auth_expired"),
    (base.RateLimited("HTTP 429 secret-detail-123"), "rate_limited"),
    (base.ConfigError("collection secret-detail-123 is gone"), "config"),
    (KeyError("secret-detail-123"), "unknown"),
])
def test_errors_show_in_plain_words_never_raw(fake, admin, monkeypatch, error, code):
    from connectors import registry
    from kb.services import sync
    monkeypatch.setitem(registry.CONNECTORS, "fake", fake)
    fake.add("a", "Alpha", "v1", "Alpha text")
    sync.sync(fake.ws, "fake")
    fake.fail_after = error
    with pytest.raises(type(error)):
        sync.sync(fake.ws, "fake")
    client, headers = admin
    got = card(client, headers)
    assert got["status"] == "error"
    assert got["error"] == {"code": code, "message": base.ERRORS[code]}
    assert got["last_run"]["error"]["code"] == code
    runs = client.get("/api/integrations/fake/runs", headers=headers).json()["runs"]
    assert "secret-detail" not in json.dumps(runs) + json.dumps(got)


def test_raw_error_text_from_before_codes_is_never_shown(fake, admin):
    import integrations_store
    integrations_store.update(fake.ws, "fake", status="error",
                              last_error="HTTPError: 500 for url https://x/?token=abc")
    client, headers = admin
    assert card(client, headers)["error"] == {"code": "unknown",
                                              "message": base.ERRORS["unknown"]}


def test_auth_expired_waits_for_a_reconnect(fake, jobs_paused):
    """No retries, and the periodic sync leaves it alone, until it's reconnected."""
    import integrations_store
    from kb import jobs
    from kb.services import sync
    fake.add("a", "Alpha", "v1", "Alpha text")
    sync.sync(fake.ws, "fake")
    fake.fail_after = base.AuthExpired("revoked")
    job_id = jobs.enqueue("sync", fake.ws, {"provider": "fake"}, lane=f"sync:{fake.ws}:fake")
    runner = jobs.Runner(handlers={"sync": jobs.HANDLERS["sync"]}, threads=0)
    while runner.run_once():
        pass
    job = jobs.get(job_id)
    assert (job["status"], job["attempts"]) == ("done", 1)
    assert json.loads(job["result"]) == {"error": "auth_expired"}
    queued = []
    sync_all_start = sync.start_sync
    try:
        sync.start_sync = lambda ws, provider, **kw: queued.append((ws, provider))
        sync.sync_all()
    finally:
        sync.start_sync = sync_all_start
    assert (fake.ws, "fake") not in queued
    integrations_store.update(fake.ws, "fake", status="connected", last_error=None)
    try:
        sync.start_sync = lambda ws, provider, **kw: queued.append((ws, provider))
        sync.sync_all()
    finally:
        sync.start_sync = sync_all_start
    assert (fake.ws, "fake") in queued


def test_a_transient_failure_is_retried(fake, jobs_paused):
    from kb import jobs
    from kb.services import sync
    fake.add("a", "Alpha", "v1", "Alpha text")
    sync.sync(fake.ws, "fake")
    fake.fail_after = base.Transient("503")
    job_id = jobs.enqueue("sync", fake.ws, {"provider": "fake"}, lane=f"sync:{fake.ws}:fake")
    jobs.Runner(handlers={"sync": jobs.HANDLERS["sync"]}, threads=0).run_once()
    job = jobs.get(job_id)
    assert job["status"] == "queued" and "transient" in job["error"]


class Limited(Fake):
    def changes(self, ctx, cursor):
        for n, sid in enumerate(self.docs):
            if ctx.max_items and n >= ctx.max_items:
                yield base.LimitReached(ctx.max_items)
                yield base.Cursor("1")
                return
            yield base.Upsert(self.item(sid))
        yield base.Cursor("1", full=True)


def test_the_item_limit_is_explicit_and_shown(fake, admin, monkeypatch):
    from connectors import registry
    from kb.services import sync
    limited = Limited()
    monkeypatch.setitem(registry.CONNECTORS, "fake", limited)
    monkeypatch.setenv("CONNECTOR_MAX_ITEMS", "3")
    for n in range(5):
        limited.add(f"d{n}", f"Doc {n}", "v1", "text")
    assert sync.sync(fake.ws, "fake")["limit_reached"] == 3
    assert len(files(fake.ws)) == 3
    client, headers = admin
    assert card(client, headers)["last_run"]["limit_reached"] == 3


class Skipping(Fake):
    def fetch(self, ctx, item):
        if item.source_id == "big":
            return None
        if item.source_id == "gone":
            raise base.NotFound("404")
        return super().fetch(ctx, item)


def test_skipped_items_are_listed_with_their_reason(fake, admin, monkeypatch):
    from connectors import registry
    from kb.services import sync
    skipping = Skipping()
    monkeypatch.setitem(registry.CONNECTORS, "fake", skipping)
    skipping.add("ok", "Fine", "v1", "text")
    skipping.add("big", "Huge video", "v1", "")
    skipping.add("gone", "Deleted meanwhile", "v1", "")
    skipping.add("blank", "Blank", "v1", "")
    skipping.docs["blank"] = ("Blank", "v1", b"\x00\x01")
    summary = sync.sync(fake.ws, "fake")
    assert (summary["upserted"], summary["skipped"]) == (2, 2)
    client, headers = admin
    got = client.get("/api/integrations/fake/runs", headers=headers).json()
    assert {s["title"]: s["reason"] for s in got["skipped"]} == {
        "Huge video": "unsupported", "Deleted meanwhile": "not_found"}
    assert card(client, headers)["skipped_count"] == 2
    assert got["runs"][0]["upserted"] == 2


def test_sync_history_keeps_the_last_runs(fake, monkeypatch):
    import sync_store
    from kb.services import sync
    monkeypatch.setattr(sync_store, "KEEP_RUNS", 3)
    fake.add("a", "Alpha", "v1", "Alpha text")
    for _ in range(5):
        sync.sync(fake.ws, "fake")
    runs = sync_store.runs(fake.ws, "fake")
    assert len(runs) == 3 and runs[0]["id"] > runs[1]["id"] > runs[2]["id"]
    assert [r["kind"] for r in runs] == ["incremental"] * 3


# ---- manifest.json -> source_items ---------------------------------------------------
def _copy_manifest_folder(ws, provider):
    """A connector folder as an older release left it: the synced files and their
    manifest.json (tests/fixtures/connectors/manifests/<provider>/, real shapes)."""
    import workspaces
    src = os.path.join(FIXTURES, "manifests", provider)
    dst = workspaces.connector_dir(ws, provider)
    shutil.copytree(src, dst)
    with open(os.path.join(dst, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def test_existing_manifests_are_imported_into_source_items_once(fake):
    import sync_store
    drive = _copy_manifest_folder(fake.ws, "google_drive")
    notion = _copy_manifest_folder(fake.ws, "notion")
    info = sync_store.doc_info(fake.ws)
    assert info == {
        **{f"connectors/google_drive/{name}": {"title": e["title"], "url": e["url"],
                                               "provider": "Google Drive"}
           for name, e in drive.items()},
        **{f"connectors/notion/{name}": {"title": e["title"], "url": e["url"],
                                         "provider": "Notion"}
           for name, e in notion.items()}}
    items = sync_store.items(fake.ws, "google_drive")
    for name, entry in drive.items():
        row = items[entry["id"]]
        assert (row["file_name"], row["version"], row["title"]) == (name, entry["modified"],
                                                                    entry["title"])
    # Once: a second look adds nothing, and the old file is left where it was.
    assert sync_store.import_manifests(fake.ws) == 0
    assert os.path.isfile(f"data/workspaces/{fake.ws}/docs/connectors/notion/manifest.json")
    run = sync_store.last_run(fake.ws, "notion")
    assert (run["kind"], run["upserted"]) == ("imported", len(notion))


def test_after_the_import_an_unchanged_source_isnt_downloaded_again(fake, monkeypatch):
    """The first sync after upgrading knows every file from the manifest: nothing
    is fetched or re-indexed when the source hasn't changed."""
    import integrations_store
    from connectors import registry
    from kb.services import sync
    notion = _copy_manifest_folder(fake.ws, "notion")
    source = Fake()
    for name, e in notion.items():
        source.add(e["id"], e["title"], e["modified"], "unused")
    source.items_url = {e["id"]: e["url"] for e in notion.values()}
    source.item = lambda sid, s=source: base.Item(
        source_id=sid, title=s.docs[sid][0], url=s.items_url[sid], version=s.docs[sid][1],
        kind="page")
    monkeypatch.setitem(registry.CONNECTORS, "notion", source)
    integrations_store.connect(fake.ws, "notion", "Acme", {"access_token": "x"}, "admin")
    summary = sync.sync(fake.ws, "notion")
    assert source.fetched == [] and summary["unchanged"] == len(notion)
    assert fake.reindexed == []


def test_disconnect_removes_files_items_history_and_their_index_entries(fake):
    import integrations_store
    import sync_store
    from kb.services import sync
    fake.add("a", "Alpha", "v1", "Alpha text")
    sync.sync(fake.ws, "fake")
    fake.reindexed.clear()
    sync.disconnect(fake.ws, "fake")
    assert files(fake.ws) == [] and sync_store.items(fake.ws, "fake") == {}
    assert sync_store.runs(fake.ws, "fake") == [] and not integrations_store.get(fake.ws, "fake")
    assert fake.reindexed == [["connectors/fake/a.txt"]]


# ---- end to end: synced text is searchable, cited by its title -------------------------
def test_synced_text_is_searchable_under_its_title(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from langchain_core.embeddings import DeterministicFakeEmbedding
    import api
    import auth
    import ingest
    import integrations_store
    import members_store
    import rag
    from connectors import registry
    from kb.services import indexing

    fake_embed = lambda **kwargs: DeterministicFakeEmbedding(size=32)   # noqa: E731
    for module in (ingest, rag):
        monkeypatch.setattr(module, "OpenAIEmbeddings", fake_embed)
        monkeypatch.setattr(module, "DB_FOLDER", str(tmp_path / "chroma"))
    monkeypatch.chdir(tmp_path)
    source = Fake()
    source.add("p1", "Launch plan", "v1", "The launch codename is BLUEFALCON.")
    monkeypatch.setitem(registry.CONNECTORS, "fake", source)
    ws = f"w-{uuid.uuid4().hex[:8]}"
    members_store.set_password(f"admin@{ws}.example", "Admin",
                               auth.hash_password("correct-horse"), ws, "admin")
    integrations_store.connect(ws, "fake", "Fake", {"token": "t"}, "admin")
    client = TestClient(api.app)
    token = client.post("/api/auth/login", json={"username": f"admin@{ws}.example",
                                                 "password": "correct-horse"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}", "X-Role": "hr"}
    assert client.post("/api/integrations/fake/sync", headers=headers).status_code == 200

    deadline = time.time() + 60
    while time.time() < deadline:
        state = indexing.index_status(ws)
        if state["status"] == "idle" and state["summary"] and state["summary"].get("chunks"):
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"not indexed: {indexing.index_status(ws)}")
    retriever, _ = rag.load_chain("hr", indexing.current(ws)[0])
    docs = retriever.invoke("What is the launch codename?")
    hit = next(d for d in docs if "BLUEFALCON" in d.page_content)
    assert hit.metadata["title"] == "Launch plan"
    assert hit.metadata["url"] == "https://fake.example/p1"
    assert hit.metadata["provider"] == "Fake source"
    assert hit.metadata["access"] == "restricted"      # the connection's level, as before
    knowledge = client.get("/api/knowledge", headers=headers).json()["items"]
    assert [(k["title"], k["source"]) for k in knowledge] == [("Launch plan", "Fake source")]
