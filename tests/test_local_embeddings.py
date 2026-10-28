"""
Local embeddings (models.py's "local" provider): fastembed on the server's CPU, so
a server with only an Anthropic key can still search by meaning. The model is
FakeTextEmbedding here (no download); test_the_real_model_loads_and_embeds loads
the real one, only with LOCAL_EMBED_LIVE=1.

    the provider       embeddings only; lazy, one load per model, threads capped,
                       batched; cached under LOCAL_EMBED_CACHE
    the default        chat on Anthropic with no EMBED_PROVIDER: local, said once
    the index          switching to or from local rebuilds it (384 numbers, not 1536)
"""

import logging
import os
import time

import numpy as np
import pytest

import model_providers_store
import models
from tests.test_bedrock import client, fake_aws, indexed, login, new_workspace  # noqa: F401
from tests.test_model_providers import server_env  # noqa: F401


class FakeTextEmbedding:
    """fastembed.TextEmbedding's surface: embed / query_embed yield numpy vectors."""
    made = []

    def __init__(self, model_name, cache_dir=None, threads=None):
        FakeTextEmbedding.made.append({"model": model_name, "cache_dir": cache_dir,
                                       "threads": threads})
        self.batches = []

    def _vector(self, text):
        v = np.zeros(384, dtype=np.float32)
        for word in str(text).lower().split():
            v[hash(word) % 384] += 1.0
        return v / (np.linalg.norm(v) or 1.0)

    def embed(self, texts, batch_size=256):
        self.batches.append((len(texts), batch_size))
        return (self._vector(t) for t in texts)

    def query_embed(self, text):
        return iter([self._vector(text)])


@pytest.fixture
def fake_fastembed(monkeypatch, tmp_path):
    FakeTextEmbedding.made = []
    monkeypatch.setattr(models, "_fastembed", lambda: FakeTextEmbedding)
    monkeypatch.setattr(models, "_local_models", {})
    monkeypatch.setenv("LOCAL_EMBED_CACHE", str(tmp_path / "fastembed"))
    return FakeTextEmbedding.made


# ---- The provider ------------------------------------------------------------------------------------
def test_local_has_embeddings_and_no_chat():
    local = models.REGISTRY["local"]
    assert local.embeddings and not local.chat and local.api_key_env is None
    assert "local" in models.embedding_providers() and "local" not in models.chat_providers()
    with pytest.raises(model_providers_store.Invalid, match="chat provider"):
        model_providers_store.save(new_workspace(), {"chat_provider": "local"}, "admin")


def test_the_model_loads_once_on_first_use_with_capped_threads(server_env, fake_fastembed,
                                                                tmp_path):
    server_env.setenv("EMBED_PROVIDER", "local")
    on = models.current()
    assert on.embed_name == "local:BAAI/bge-small-en-v1.5:384"
    embeddings = on.embeddings()
    assert fake_fastembed == []                               # nothing loaded yet
    vectors = embeddings.embed_documents([f"chunk {i}" for i in range(70)])
    assert len(vectors) == 70 and len(vectors[0]) == 384
    assert isinstance(vectors[0][0], float)
    assert len(embeddings.embed_query("refunds")) == 384
    on.embeddings().embed_query("again")                      # another instance: same model
    assert fake_fastembed == [{"model": "BAAI/bge-small-en-v1.5",
                               "cache_dir": str(tmp_path / "fastembed"),
                               "threads": models.LOCAL_EMBED_THREADS}]
    loaded = next(iter(models._local_models.values()))
    assert loaded.batches == [(70, models.LOCAL_EMBED_BATCH)]


def test_its_model_threads_and_cache_are_settings(server_env, fake_fastembed, tmp_path):
    server_env.setenv("EMBED_PROVIDER", "local")
    server_env.setenv("LOCAL_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
    server_env.setenv("LOCAL_EMBED_THREADS", "1")
    server_env.setenv("LOCAL_EMBED_CACHE", str(tmp_path / "elsewhere"))
    on = models.current()
    assert on.embed_name == "local:BAAI/bge-base-en-v1.5:768"
    on.embeddings().embed_query("hi")
    assert fake_fastembed[-1] == {"model": "BAAI/bge-base-en-v1.5",
                                  "cache_dir": str(tmp_path / "elsewhere"), "threads": 1}
    assert os.path.isdir(tmp_path / "elsewhere")


def test_the_cache_defaults_to_the_data_directory_not_the_code(server_env, monkeypatch):
    import db
    server_env.delenv("LOCAL_EMBED_CACHE", raising=False)
    found = models._local_cache_dir()
    assert found == os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "models",
                                 "fastembed")


def test_a_model_that_cant_load_fails_closed(server_env, monkeypatch, tmp_path):
    def broken(**kwargs):
        raise OSError("no network to download the model")
    monkeypatch.setattr(models, "_fastembed", lambda: broken)
    monkeypatch.setattr(models, "_local_models", {})
    monkeypatch.setenv("LOCAL_EMBED_CACHE", str(tmp_path))
    server_env.setenv("EMBED_PROVIDER", "local")
    with pytest.raises(models.ProviderError, match="couldn't be loaded"):
        models.current().embeddings().embed_query("hi")


# ---- The default for a chat provider without embeddings ------------------------------------------------
def test_claude_with_no_embeddings_provider_gets_local_and_says_so_once(server_env, caplog,
                                                                        monkeypatch):
    monkeypatch.setattr(models, "_said", set())
    server_env.setenv("CHAT_PROVIDER", "anthropic")
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        assert models.server_providers() == ("anthropic", "local")
        assert models.server_providers() == ("anthropic", "local")
        models.log_server_providers()
    said = [r.getMessage() for r in caplog.records if "local embeddings" in r.getMessage()]
    assert len(said) == 1 and "Anthropic has no embeddings" in said[0]
    assert any("chat on anthropic, embeddings on local" in r.getMessage()
               for r in caplog.records)
    server_env.setenv("MODEL_PROVIDER", "anthropic")
    assert models.server_providers() == ("anthropic", "local")
    server_env.setenv("EMBED_PROVIDER", "openai")              # said out loud: that one
    assert models.server_providers() == ("anthropic", "openai")


# ---- Switching rebuilds the index ----------------------------------------------------------------------
def test_switching_to_and_from_local_rebuilds_the_index(client, indexed, fake_fastembed):
    from kb.services import indexing
    w = indexed.ws
    h = login(client, w)
    old = indexing.current(w)[0]
    r = client.put("/api/admin/assistant/models", headers=h, json={"embed_provider": "local"})
    assert r.status_code == 409 and "rebuilt" in r.json()["detail"]
    r = client.put("/api/admin/assistant/models", headers=h,
                   json={"embed_provider": "local", "confirm_reindex": True})
    assert r.status_code == 200 and r.json()["reindex"] is True
    assert indexed.wait()["status"] == "idle"
    local = indexing.current(w)[0]
    assert local != old
    assert indexing.embeddings_of(w) == "local:BAAI/bge-small-en-v1.5:384"
    assert fake_fastembed and models._local_models
    # Questions are searched with the same local model.
    on = models.for_workspace(w, index_embeddings=indexing.embeddings_of(w))
    assert isinstance(on.query_embeddings(), models.LocalEmbeddings)
    # And back to the server's OpenAI embeddings: asked first, rebuilt again.
    r = client.put("/api/admin/assistant/models", headers=h, json={"embed_provider": "default"})
    assert r.status_code == 409
    r = client.put("/api/admin/assistant/models", headers=h,
                   json={"embed_provider": "default", "confirm_reindex": True})
    assert r.status_code == 200 and r.json()["reindex"] is True
    assert indexed.wait()["status"] == "idle"
    assert indexing.embeddings_of(w) == "text-embedding-3-small"


# ---- The real model, once, on request ------------------------------------------------------------------
@pytest.mark.skipif(os.getenv("LOCAL_EMBED_LIVE") != "1",
                    reason="downloads the real model: set LOCAL_EMBED_LIVE=1")
def test_the_real_model_loads_and_embeds(server_env, monkeypatch, tmp_path):
    cache = os.getenv("LOCAL_EMBED_LIVE_CACHE") or str(tmp_path / "fastembed")
    monkeypatch.setenv("LOCAL_EMBED_CACHE", cache)
    monkeypatch.setattr(models, "_local_models", {})
    server_env.setenv("EMBED_PROVIDER", "local")
    embeddings = models.current().embeddings()
    started = time.time()
    embeddings.embed_query("warm up")                        # downloads and loads it
    loaded = time.time() - started
    chunks = [f"Chunk {i}: customers may ask for a refund within {i} days of purchase, "
              "and staff get twenty days of paid leave a year." for i in range(100)]
    started = time.time()
    vectors = embeddings.embed_documents(chunks)
    took = time.time() - started
    # The Hugging Face cache links snapshot files to blobs: count each file once.
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(cache) for f in fs
               if not os.path.islink(os.path.join(d, f)))
    print(f"\nlocal embeddings: model {size / 1e6:.1f} MB on disk, first load {loaded:.1f}s, "
          f"100 chunks in {took:.2f}s")
    assert len(vectors) == 100 and len(vectors[0]) == 384
    near = np.dot(vectors[0], embeddings.embed_query("How long do I have to ask for a refund?"))
    far = np.dot(vectors[0], embeddings.embed_query("What colour is the sky?"))
    assert near > far
