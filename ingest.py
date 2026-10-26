"""
ingest.py  —  STEP 1 of RAG: put your documents INTO the vector database.

Think of this as "studying for an exam":
the AI reads your documents ONCE, converts them into numbers (embeddings),
and stores them so it can look things up FAST later.

Run this file ONCE (or whenever your documents change):
    python ingest.py

The 4 things that happen here (this IS the RAG ingestion pipeline):
    1. LOAD    — read raw text out of your files
    2. SPLIT   — cut the text into small "chunks"
    3. EMBED   — turn each chunk into a list of numbers (a vector)
    4. STORE   — save those vectors in Chroma (the vector database)

Two ways to run it:
    main()     build a collection from scratch (a full rebuild)
    update()   bring an existing collection in line with the files, touching
               only what changed: a new or edited file embeds its own chunks,
               a deleted file loses its chunks, the rest isn't embedded again
The app runs them as background jobs (kb/services/indexing.py), which also
decides which collection a workspace's questions are answered from.

Every chunk gets a STABLE id, from (workspace, document, chunk number, chunk
text): the same chunk of the same file always has the same id, so a re-run
knows what it already has.
"""

import hashlib
import json
import logging
import os
import threading
from collections import Counter

from dotenv import load_dotenv

# LangChain pieces — each is one step of the pipeline
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma

# Load the secret OPENAI_API_KEY from the .env file into the program.
load_dotenv()

log = logging.getLogger("uvicorn.error")

# Where your documents live, and where the database will be saved.
DOCS_FOLDER = "docs"
DB_FOLDER = "chroma_db"

EMBED_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 500          # characters (about 110-125 tokens)
CHUNK_OVERLAP = 100
EXTENSIONS = (".txt", ".pdf")
BATCH = 128               # chunks per embedding call
# Everything that decides what a chunk's text and vector are. An index built
# with other settings can't be updated chunk by chunk: it is rebuilt.
FINGERPRINT = hashlib.sha256(json.dumps({
    "model": EMBED_MODEL, "chunk_size": CHUNK_SIZE, "overlap": CHUNK_OVERLAP,
    "header": "[title]", "ids": 1}, sort_keys=True).encode()).hexdigest()[:16]


def _embeddings():
    return OpenAIEmbeddings(model=EMBED_MODEL)


# Held while a Chroma store is opened. Chroma keeps one client per folder per
# process, and making the first one isn't safe from two threads at once (two
# workspaces indexing, or indexing and a question).
CHROMA_LOCK = threading.Lock()


def _store(collection):
    """The collection (made if it doesn't exist yet)."""
    with CHROMA_LOCK:
        return Chroma(persist_directory=DB_FOLDER, embedding_function=_embeddings(),
                      collection_name=collection or "langchain")


def doc_files(docs_folder):
    """Every document under docs_folder, as its key: the path inside it, with /
    ('public/handbook.txt', 'connectors/notion/page1.txt'). Hidden files and
    folders are left out."""
    keys = []
    for root, dirs, files in os.walk(docs_folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.endswith(EXTENSIONS) and not name.startswith("."):
                rel = os.path.relpath(os.path.join(root, name), docs_folder)
                keys.append(rel.replace(os.sep, "/"))
    # .txt before .pdf, then by path: the order the old loader read them in.
    return sorted(keys, key=lambda k: (not k.endswith(".txt"), k))


def _connector_of(key):
    """The provider whose sync wrote this file (connectors/<provider>/...), or None."""
    parts = key.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "connectors" else None


def _manifest_info(docs_folder, key, manifests):
    """Files synced by a connector (docs/.../connectors/<provider>/) come with a
    manifest.json giving each file's real title and link — attach it so answers
    can cite "Q3 plan (Google Drive)" with a link instead of a file id."""
    folder = os.path.join(docs_folder, os.path.dirname(key))
    if folder not in manifests:
        try:
            with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
                manifests[folder] = json.load(f)
        except (OSError, ValueError):
            manifests[folder] = {}
    info = manifests[folder].get(os.path.basename(key)) or {}
    if not info:
        return {}
    return {"title": info.get("title") or "", "url": info.get("url") or "",
            "provider": info.get("provider") or ""}


def doc_meta(docs_folder, key, connector_access, manifests):
    """What every chunk of a document carries besides its own position.

    ACCESS LEVEL (permission control): this is how enterprises secure RAG —
    each chunk carries metadata saying WHO may see it. Chunks from restricted/
    are "restricted"; other uploads are "public". Files synced by a connector
    take the level its admin chose for that connection (restricted unless
    opened to everyone). At query time we only fetch chunks the user is allowed
    to see — so confidential text never reaches the model for the wrong user."""
    provider = _connector_of(key)
    meta = {"doc_key": key, "connector": provider or ""}
    if provider:
        meta.update(_manifest_info(docs_folder, key, manifests))
        meta["access"] = (connector_access or {}).get(provider, "restricted")
    else:
        meta["access"] = "restricted" if "/restricted/" in "/" + key else "public"
    return meta


# The tags doc_meta() gives: when one of them changes, a document's chunks are re-tagged.
DOC_FIELDS = ("doc_key", "connector", "access", "title", "url", "provider")


def _file_stat(path):
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}"


def _doc_hash(path, title):
    """The document's content as indexed: its bytes, and the title every chunk starts with."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    h.update(b"\0" + (title or "").encode("utf-8"))
    return h.hexdigest()


def chunk_id(namespace, key, n, text):
    """Stable id of a document's n-th chunk: the same text there gets the same id."""
    raw = "\0".join((namespace or "", key, str(n), hashlib.sha256(text.encode()).hexdigest()))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _clean(meta):
    """Chroma takes only str / int / float / bool values."""
    return {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))}


def load_and_split(docs_folder, key, meta, namespace):
    """One document's chunks: [(id, text, metadata)].

    1. LOAD — a "Document" in LangChain = the text + some metadata (like the
       filename). PDFs come one Document per page.
    2. SPLIT — cut big text into small overlapping chunks. WHY chunk? The AI
       can only read a limited amount of text at once, and search is more
       accurate on small focused pieces than on a whole book. chunk_overlap
       repeats a little text between pieces so a sentence cut in half keeps
       its meaning."""
    path = os.path.join(docs_folder, *key.split("/"))
    if key.endswith(".pdf"):
        documents = PyPDFLoader(path).load()
    else:
        # UTF-8 first (Windows would otherwise guess cp1252 and garble text),
        # detect the encoding only if that fails.
        documents = TextLoader(path, encoding="utf-8", autodetect_encoding=True).load()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,   # remember where each chunk sits in its document
    )
    chunks = splitter.split_documents(documents)

    # CONTEXTUAL CHUNK HEADER: start every chunk with its document's title.
    # A chunk in the middle of "Bible Navigator — Technical Audit" often never
    # says "Bible Navigator"; without the title, a search for it misses those
    # chunks and the model can't tell what they are about.
    title = meta.get("title") or os.path.splitext(os.path.basename(key))[0]
    out = []
    for n, chunk in enumerate(chunks):
        text = chunk.page_content
        if title and not text.startswith(title):
            text = f"[{title}]\n{text}"
        out.append((chunk_id(namespace, key, n, text), text,
                    _clean({**chunk.metadata, **meta})))
    return out


def _embed_and_store(store, items, report):
    """3 & 4. EMBED + STORE — turn chunks into vectors and save them.
    EMBED: OpenAI reads each chunk and returns a list of ~1536 numbers. Chunks
           with similar MEANING get similar numbers. That's the magic that lets
           us search by meaning instead of by exact keywords.
    STORE: Chroma saves these vectors (and each chunk's tags) on disk.
    In batches, so a big document is many small calls, not one huge one."""
    embeddings = store.embeddings
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        report(3, f"Embedding {len(items)} chunks" if len(items) <= BATCH
               else f"Embedding chunks {start + 1}-{start + len(batch)} of {len(items)}")
        vectors = embeddings.embed_documents([text for _, text, _ in batch])
        store._collection.upsert(ids=[i for i, _, _ in batch], embeddings=vectors,
                                 documents=[text for _, text, _ in batch],
                                 metadatas=[meta for _, _, meta in batch])


def summary(collection):
    """What a collection holds: documents, chunks, and how many chunks are restricted."""
    metas = _store(collection)._collection.get(include=["metadatas"])["metadatas"]
    return {"documents": len({m.get("doc_key") or m.get("source") for m in metas}),
            "chunks": len(metas),
            "restricted": sum(1 for m in metas if m.get("access") == "restricted")}


def _reporter(progress):
    def report(step, message):
        if progress:
            progress(step, 4, message)
    return report


def main(progress=None, docs_folder=DOCS_FOLDER, collection=None, connector_access=None,
         workspace=None):
    """Build a search index from a docs folder, from scratch. Returns a summary dict.
    progress(step, total, message) — optional callback, e.g. for a UI progress bar.
    docs_folder / collection — which workspace (defaults = the original setup).
    connector_access — {provider: "public" | "restricted"} for synced files, from
    each connection's setting (integrations_store.chunk_access_levels); a provider
    missing from it is restricted.
    workspace — goes into the chunk ids (default: the collection's name)."""
    report = _reporter(progress)
    collection = collection or "langchain"
    namespace = workspace or collection

    # WHY start empty: Chroma ADDS to an existing collection, it doesn't replace
    # it. The app builds a full rebuild into a NEW collection and switches to it
    # when it's done (kb/services/indexing.py), so this only empties a
    # collection nobody is answering from.
    report(1, "Reading documents")
    _store(collection).delete_collection()
    store = _store(collection)

    keys = doc_files(docs_folder) if os.path.isdir(docs_folder) else []
    manifests, items, failed = {}, [], []
    report(2, "Splitting into chunks")
    for key in keys:
        path = os.path.join(docs_folder, *key.split("/"))
        try:
            meta = doc_meta(docs_folder, key, connector_access, manifests)
            meta.update(doc_stat=_file_stat(path), doc_hash=_doc_hash(path, meta.get("title")))
            items += load_and_split(docs_folder, key, meta, namespace)
        except Exception:
            log.exception("Index: couldn't read %s", key)
            failed.append(key)
    _embed_and_store(store, items, report)
    report(4, "Done")
    n_restricted = sum(1 for _, _, m in items if m["access"] == "restricted")
    return {"documents": len(keys) - len(failed), "chunks": len(items),
            "restricted": n_restricted, "embedded": len(items), "failed": len(failed)}


def _stored_docs(store, keys=None):
    """{doc key: {"ids": [...], "metas": [...]}} for the chunks a collection holds
    (only those of `keys`, if given)."""
    where = None if keys is None else {"doc_key": {"$in": list(keys)}}
    if keys is not None and not keys:
        return {}
    got = store._collection.get(where=where, include=["metadatas"])
    out = {}
    for cid, meta in zip(got["ids"], got["metadatas"]):
        d = out.setdefault(meta.get("doc_key"), {"ids": [], "metas": []})
        d["ids"].append(cid)
        d["metas"].append(meta)
    return out


def _same(metas, field):
    """The one value every chunk has for `field`, or None if they differ."""
    values = {m.get(field) for m in metas}
    return values.pop() if len(values) == 1 else None


def update(progress=None, docs_folder=DOCS_FOLDER, collection=None, connector_access=None,
           workspace=None, only=None):
    """Bring an existing collection in line with the docs folder, document by
    document. Returns a summary dict (with what was embedded, re-tagged, removed).

    For every document (or only the keys in `only`):
      unchanged (same size, time and tags)       nothing to do
      same content, other tags (e.g. access)     its chunks' metadata is updated:
                                                 no embedding call
      new or edited                              re-split; chunks whose id is new
                                                 are embedded, gone ones deleted
      deleted                                    its chunks are deleted
    Readers see each document's old or new chunks while this runs, never an
    empty index. A run cut short is finished by the next one: a document
    whose chunks don't all agree counts as changed."""
    report = _reporter(progress)
    collection = collection or "langchain"
    namespace = workspace or collection
    store = _store(collection)

    report(1, "Checking documents")
    on_disk = set(doc_files(docs_folder)) if os.path.isdir(docs_folder) else set()
    if only is None:
        stored = _stored_docs(store)
        stored.pop(None, None)          # chunks without a key: not ours to judge
        keys = sorted(on_disk | set(stored))
    else:
        keys = sorted(set(only))
        stored = _stored_docs(store, keys)

    manifests, failed = {}, []
    stats = Counter()
    adds, retags, deletes = [], [], []        # (id, text, meta) / (id, meta) / id
    report(2, "Reading changed documents")
    for key in keys:
        have = stored.get(key)
        path = os.path.join(docs_folder, *key.split("/"))
        if key not in on_disk:
            if have:
                deletes += have["ids"]
                stats["removed"] += 1
            continue
        meta = doc_meta(docs_folder, key, connector_access, manifests)
        stat = _file_stat(path)
        if have and _same(have["metas"], "doc_stat") == stat and all(
                _same(have["metas"], k) == meta.get(k) for k in DOC_FIELDS):
            continue                                        # unchanged
        try:
            meta["doc_stat"] = stat
            meta["doc_hash"] = _doc_hash(path, meta.get("title"))
            if have and _same(have["metas"], "doc_hash") == meta["doc_hash"]:
                # Same text, other tags (access level, link, file time): no embedding.
                retags += [(cid, meta) for cid in have["ids"]]
                stats["retagged"] += 1
                continue
            chunks = load_and_split(docs_folder, key, meta, namespace)
        except Exception:
            log.exception("Index: couldn't read %s", key)
            failed.append(key)
            continue
        old = set(have["ids"]) if have else set()
        new = {cid for cid, _, _ in chunks}
        adds += [c for c in chunks if c[0] not in old]
        retags += [(cid, m) for cid, _, m in chunks if cid in old]
        deletes += sorted(old - new)
        stats["changed" if have else "added"] += 1

    # New chunks first, then tags, then deletes: a run cut short in between
    # leaves extra chunks for the next run to remove, never missing ones.
    _embed_and_store(store, adds, report)
    for start in range(0, len(retags), 1000):
        batch = retags[start:start + 1000]
        store._collection.update(ids=[i for i, _ in batch], metadatas=[m for _, m in batch])
    for start in range(0, len(deletes), 1000):
        store._collection.delete(ids=deletes[start:start + 1000])
    report(4, "Done")
    return {**summary(collection), "embedded": len(adds), "deleted_chunks": len(deletes),
            "added": stats["added"], "changed": stats["changed"],
            "retagged": stats["retagged"], "removed": stats["removed"], "failed": len(failed)}


def retag(collection, connector, access):
    """Give every chunk synced by `connector` the access level `access`, without
    embedding anything. Returns how many chunks changed."""
    store = _store(collection)
    got = store._collection.get(where={"connector": connector}, include=["metadatas"])
    ids = [cid for cid, m in zip(got["ids"], got["metadatas"]) if m.get("access") != access]
    for start in range(0, len(ids), 1000):
        batch = ids[start:start + 1000]
        store._collection.update(ids=batch, metadatas=[{"access": access}] * len(batch))
    return len(ids)


def drop(collection):
    _store(collection).delete_collection()


if __name__ == "__main__":
    # Rebuild the default workspace's index now, the way the app's "rebuild
    # everything" does: into a new collection, switched to when it's done.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # The old collection is dropped by the app's job runner later; this
    # short-lived process only queues that, it doesn't run jobs itself.
    os.environ["JOBS_IN_PROCESS"] = "off"
    import workspaces
    from kb.services import indexing
    print("Building the search index of the default workspace (docs/)...")
    result = indexing.rebuild_now(workspaces.DEFAULT,
                                  progress=lambda step, total, message: print(f"{step}) {message}"))
    print(f"Done: {result['documents']} documents, {result['chunks']} chunks "
          f"({result['restricted']} restricted) in collection '{result['collection']}'.")
    print("\nNext step: run  ->  uvicorn api:app --reload --port 8010")
