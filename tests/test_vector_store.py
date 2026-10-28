"""
vector_store.py: the search index's one interface, Chroma or pgvector (pipeline.md
P2-1). The filter translation is checked on every leg; the pgvector store itself
on the Postgres leg (where CI also runs every retrieval and leak test with
VECTOR_STORE=pgvector).
"""

import os
import uuid

import pytest

import access
import vector_store
from eval.harness import HashEmbeddings


def sql(where):
    args = []
    return vector_store._where(where, args), args


def test_an_acl_filter_is_one_array_overlap():
    who = access.Viewer([access.user("pat@acme.example"), "group:g_finance"])
    text, args = sql(access.chroma_filter(who))
    assert text == "acl && ?::text[]"
    assert sorted(args[0]) == sorted(access.of(who))
    assert sql({"acl": {"$contains": "everyone"}}) == ("acl @> ?::text[]", [["everyone"]])


def test_field_filters():
    assert sql({"doc_key": "public/a.txt"}) == ("metadata @> ?::jsonb", ['{"doc_key": "public/a.txt"}'])
    text, args = sql({"doc_key": {"$in": ["a", "b"]}})
    assert text == "metadata -> ?::text IN (?::jsonb, ?::jsonb)" and args == ["doc_key", '"a"', '"b"']
    assert sql({"doc_key": {"$in": []}})[0] == "FALSE"
    text, _ = sql({"$and": [{"connector": "notion"}, {"page": {"$gte": 2}}]})
    assert text.startswith("(metadata @> ?::jsonb AND (jsonb_typeof")
    with pytest.raises(ValueError, match="isn't supported"):
        sql({"page": {"$regex": "x"}})


def test_a_collection_belongs_to_its_workspace():
    assert vector_store.workspace_of("langchain") == "default"
    assert vector_store.workspace_of("langchain__g3") == "default"
    assert vector_store.workspace_of("ws_acme") == "acme"
    assert vector_store.workspace_of("ws_acme-2__g12") == "acme-2"


pgvector = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                              reason="pgvector needs Postgres (the CI Postgres leg)")


@pgvector
def test_pgvector_keeps_chunks_filters_them_and_finds_the_nearest(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTOR_STORE", "pgvector")
    folder = str(tmp_path / "index")
    name = f"ws_vs-{uuid.uuid4().hex[:6]}"
    store = vector_store.store(name, HashEmbeddings(), folder)
    texts = ["refunds are paid within 14 days", "salaries are reviewed in March",
             "the office opens at nine"]
    metas = [{"doc_key": "public/refunds.txt", "acl": ["everyone"], "page": 1},
             {"doc_key": "restricted/pay.txt", "acl": ["group:hr"], "page": 2},
             {"doc_key": "public/office.txt", "acl": ["everyone"], "page": 3}]
    store._collection.upsert(["r", "s", "o"], store.embeddings.embed_documents(texts), texts,
                             metas)
    assert name in vector_store.collection_names(folder)
    assert store._collection.count() == 3
    got = store.get()
    assert got["ids"] == ["r", "s", "o"] and got["metadatas"][1]["acl"] == ["group:hr"]

    public = access.chroma_filter(access.Viewer())
    hits = store.similarity_search("when are salaries reviewed", k=3, filter=public)
    assert [d.id for d in hits] and "s" not in [d.id for d in hits]      # never the HR chunk
    hr = access.chroma_filter(access.for_role("hr"))
    assert store.similarity_search("when are salaries reviewed", k=1, filter=hr)[0].id == "s"

    # Tags are merged in, as Chroma's update does; the ACL column follows them.
    store._collection.update(ids=["s"], metadatas=[{"acl": ["everyone"], "access": "public"}])
    meta = store._collection.get(ids=["s"], include=["metadatas"])["metadatas"][0]
    assert meta["acl"] == ["everyone"] and meta["doc_key"] == "restricted/pay.txt"
    assert "s" in [d.id for d in store.similarity_search("salaries", k=3, filter=public)]

    store._collection.delete(where={"doc_key": {"$in": ["public/office.txt"]}})
    assert store._collection.get(include=[])["ids"] == ["r", "s"]
    # Another folder is another index: nothing of this one in it.
    assert vector_store.collection_names(str(tmp_path / "other")) == []
    vector_store.drop_collection(folder, name)
    assert name not in vector_store.collection_names(folder)
    with pytest.raises(vector_store.NotFoundError):
        vector_store.existing(folder, name)
