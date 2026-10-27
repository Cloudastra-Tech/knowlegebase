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

Every chunk also carries what a citation needs (PASSAGE METADATA, see
load_and_split): chunk_id, doc_id, title, provider, url, access, modified,
char_start / char_end in the document's extracted text, page (PDFs, 0-based) and
heading_path (where the text has headings). None of it goes into the ids, so an
index made before a field existed gets it on its next update() by a re-tag, not
by embedding anything again (CHUNK_META).
"""

import hashlib
import json
import logging
import os
import threading
from collections import Counter
from datetime import datetime, timezone

from dotenv import load_dotenv

# LangChain pieces — each is one step of the pipeline
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

import access
import chunk_context
import chunking
import models
import vector_store

# Load the secret OPENAI_API_KEY from the .env file into the program.
load_dotenv()

log = logging.getLogger("uvicorn.error")

# Where your documents live, and where the database will be saved.
DOCS_FOLDER = "docs"
DB_FOLDER = "chroma_db"

EMBED_MODEL = "text-embedding-3-small"
# .txt and .pdf are read here; the rest are turned into text by connectors/extract.py
# (uploads; connectors write .txt or .pdf).
EXTENSIONS = (".txt", ".pdf", ".md", ".html", ".htm", ".docx", ".pptx", ".xlsx")
BATCH = 128               # chunks per embedding call


def _env_choice(name, choices):
    value = (os.getenv(name) or "").strip().lower()
    return value if value in choices else choices[0]


# CONTEXTUAL CHUNK HEADER (pipeline.md P1-3), the line every chunk starts with:
#   title      "[Leave policy]": the document's title (the default)
#   headings   "[Leave policy › Sick leave]": and the headings the chunk sits under
CHUNK_CONTEXT = _env_choice("RAG_CHUNK_CONTEXT", ("title", "headings"))
# RAG_CHUNK_SUMMARY=llm: also one model-written sentence placing the chunk in its
# document (chunk_context.py; one FAST model call per new chunk, cached). Off by default.
CHUNK_SUMMARY = _env_choice("RAG_CHUNK_SUMMARY", ("off", "llm"))


def fingerprint(on=None):
    """Everything that decides what a chunk's text and vector are, hashed. An
    index built with other settings can't be updated chunk by chunk: it is
    rebuilt (a shadow rebuild into a new collection, kb/services/indexing.py,
    started by the app when it sees an index with another FINGERPRINT).
    on: a workspace's models (models.Models) when they aren't OpenAI's: its
    embedding model and FAST model are part of it, so switching a workspace's
    provider or embedding model rebuilds its index. None: OpenAI's (FINGERPRINT,
    the same value as before there were providers)."""
    bedrock = on is not None and on.bedrock
    return hashlib.sha256(json.dumps({
        "model": on.embed_name if bedrock else EMBED_MODEL, "chunker": chunking.VERSION,
        "chunk_tokens": chunking.CHUNK_TOKENS, "min_tokens": chunking.MIN_TOKENS,
        "overlap_tokens": chunking.OVERLAP_TOKENS, "header": CHUNK_CONTEXT,
        "summary": f"llm:{on.fast_name if bedrock else models.fast_model()}:"
                   f"{chunk_context.PROMPT_VERSION}" if CHUNK_SUMMARY == "llm" else "off",
        "ids": 1}, sort_keys=True).encode()).hexdigest()[:16]


FINGERPRINT = fingerprint()
# The version of the per-chunk metadata load_and_split writes. It is not part of
# FINGERPRINT: an index whose chunks carry an older one is brought up to date by
# update(), document by document, re-splitting (the ids come out the same) and
# re-tagging, with no embedding call.
CHUNK_META = 3
UPLOADS = "Company documents"     # the provider of files uploaded on the Documents screen
# The sample documents a new workspace can try the assistant with (kb/services/onboarding.py)
SAMPLES = "Sample documents"
SAMPLE_FOLDER = "sample"


def _embeddings():
    """The embeddings of the workspace being indexed (models.scope), made when a
    chunk is first embedded: opening a store to read or drop it needs none."""
    return models.Lazy(lambda: models.current().embeddings(openai=OpenAIEmbeddings))


# Held while a Chroma store is opened. Chroma keeps one client per folder per
# process, and making the first one isn't safe from two threads at once (two
# workspaces indexing, or indexing and a question).
CHROMA_LOCK = threading.Lock()


def _store(collection):
    """The collection (made if it doesn't exist yet), in Chroma or pgvector
    (VECTOR_STORE, vector_store.py)."""
    with CHROMA_LOCK:
        return vector_store.store(collection or "langchain", _embeddings(), DB_FOLDER)


# Folders at the top of a docs folder that are never indexed: drafts the assistant
# wrote wait there for review (drafts_store.py) until they're published.
NOT_INDEXED = ("drafts",)


def doc_files(docs_folder):
    """Every document under docs_folder, as its key: the path inside it, with /
    ('public/handbook.txt', 'connectors/notion/page1.txt'). Hidden files and
    folders, and the NOT_INDEXED folders, are left out."""
    keys = []
    for root, dirs, files in os.walk(docs_folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and not (root == docs_folder and d in NOT_INDEXED)]
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


def _source_info(key, doc_info):
    """Files synced by a connector (docs/.../connectors/<provider>/) have a row in
    source_items (sync_store.doc_info) giving each file's real title and link —
    attach it so answers can cite "Q3 plan (Google Drive)" with a link instead
    of a file id."""
    info = (doc_info or {}).get(key) or {}
    if not info:
        return {}
    return {"title": info.get("title") or "", "url": info.get("url") or "",
            "provider": info.get("provider") or "", "modified": info.get("modified") or ""}


def doc_meta(docs_folder, key, connector_access, doc_info=None, doc_acls=None):
    """What every chunk of a document carries besides its own position.

    ACCESS (permission control): this is how enterprises secure RAG — each
    chunk carries metadata saying WHO may see it, its ACL (access.py): the
    principals that may read it. At query time we only fetch chunks the user
    holds a principal of — so confidential text never reaches the model for
    the wrong user. doc_acls: {doc key: ACL} where one is set (an upload's
    picked ACL, a synced file's under its connection's permission policy:
    kb/services/permissions.py). Otherwise the old levels decide: uploads in
    restricted/ are for the hr group, other uploads for everyone, and synced
    files take their connection's level (restricted unless opened to everyone).
    `access` (public / restricted) is kept beside it: public only when everyone
    may read the chunk."""
    provider = _connector_of(key)
    meta = {"doc_key": key, "connector": provider or ""}
    if provider:
        meta.update(_source_info(key, doc_info))
        level = (connector_access or {}).get(provider, "restricted")
    else:
        level = "restricted" if "/restricted/" in "/" + key else "public"
        meta.update(title=os.path.basename(key),
                    provider=SAMPLES if key.startswith(SAMPLE_FOLDER + "/") else UPLOADS)
    acl = (doc_acls or {}).get(key)
    meta["acl"] = access.clean_acl(acl) if acl else access.level_acl(level)
    meta["access"] = access.level_of_acl(meta["acl"])
    return meta


# The tags doc_meta() gives: when one of them changes, a document's chunks are re-tagged.
DOC_FIELDS = ("doc_key", "connector", "access", "acl", "title", "url", "provider")


def doc_id(namespace, key):
    """Stable id of a document in a workspace (what its chunks' citations point at)."""
    return hashlib.sha256(f"{namespace or ''}\0{key}".encode()).hexdigest()[:24]


def _header_title(meta, key):
    """The title every chunk's text starts with (see load_and_split): a synced
    file's real title, else the file name without its extension."""
    return (meta.get("title") if meta.get("connector") else "") \
        or os.path.splitext(os.path.basename(key))[0]


def _modified(path, meta):
    """When the document last changed: the connector's time, else the file's (ISO, UTC)."""
    if meta.get("modified"):
        return meta["modified"]
    return datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc).isoformat(
        timespec="seconds")


def _file_stat(path):
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}"


def _doc_hash(path, meta):
    """The document's content as indexed: its bytes, and a synced file's title,
    which every chunk starts with."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    title = meta.get("title") if meta.get("connector") else ""
    h.update(b"\0" + (title or "").encode("utf-8"))
    return h.hexdigest()


def chunk_id(namespace, key, n, text):
    """Stable id of a document's n-th chunk: the same text there gets the same id."""
    raw = "\0".join((namespace or "", key, str(n), hashlib.sha256(text.encode()).hexdigest()))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _clean(meta):
    """Chroma takes only str / int / float / bool values, and non-empty lists of
    strings (a chunk's ACL)."""
    return {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))
            or (isinstance(v, list) and v and all(isinstance(x, str) for x in v))}


# Headings ("## Refunds", "Refund policy:") and the path of them a position sits
# under: see chunking.py.
HEADING_SEPARATOR = chunking.HEADING_SEPARATOR
_headings = chunking.headings
heading_path = chunking.heading_path


def load_and_split(docs_folder, key, meta, namespace):
    """One document's chunks: [(id, text, metadata)].

    1. LOAD — a "Document" in LangChain = the text + some metadata (like the
       filename). PDFs come one Document per page.
    2. SPLIT — cut the text into chunks. WHY chunk? The AI can only read a
       limited amount of text at once, and search is more accurate on focused
       pieces than on a whole book. chunking.split follows the document's
       STRUCTURE: a chunk starts at a heading, a table is cut between rows with
       its header row repeated, a code block stays whole, and sizes are tokens.
       A PDF is split page by page (every chunk knows its page), with the
       headings of earlier pages carried over."""
    path = os.path.join(docs_folder, *key.split("/"))
    if key.endswith(".pdf"):
        documents = PyPDFLoader(path).load()
    elif not key.endswith(".txt"):
        # Word, PowerPoint, Excel, Markdown, HTML: one Document of its text.
        from connectors import extract
        with open(path, "rb") as f:
            text = extract.extract(f.read(), name=key)
        documents = [Document(page_content=text, metadata={"source": path})]
    else:
        # UTF-8 first (Windows would otherwise guess cp1252 and garble text),
        # detect the encoding only if that fails.
        documents = TextLoader(path, encoding="utf-8", autodetect_encoding=True).load()
    # CONTEXTUAL CHUNK HEADER: start every chunk with its document's title (and,
    # with RAG_CHUNK_CONTEXT=headings, the headings it is under). A chunk in the
    # middle of "Bible Navigator — Technical Audit" often never says "Bible
    # Navigator"; without the title, a search for it misses those chunks and the
    # model can't tell what they are about.
    title = _header_title(meta, key)
    summary_llm = whole = None
    if CHUNK_SUMMARY == "llm":
        summary_llm = chunk_context._llm()
        whole = "\n\n".join(d.page_content for d in documents)
    out = []
    stack = []                        # the headings a PDF page starts under
    for source in documents:
        page_text = source.page_content
        found = _headings(page_text)
        for piece in chunking.split(page_text):
            body = page_text[piece.start:piece.end]
            if not body.strip():
                continue
            # The headings its text (after any it opens with) is under; before
            # the first one, its own first.
            path = heading_path(found, piece.at, stack) or heading_path(
                found, next((at for at, _, _ in found if piece.start <= at < piece.end), -1),
                stack)
            label = (f"{title}{HEADING_SEPARATOR}{path}"
                     if CHUNK_CONTEXT == "headings" and title and path else title)
            header = [f"[{label}]"] if label and not body.startswith(label) else []
            context = chunk_context.context_for(whole, body, summary_llm) if summary_llm else ""
            if context:               # for search only: never shown as the source's text
                header.append(context)
            if piece.prefix:          # a table's header row, repeated
                header.append(piece.prefix)
            text = "\n".join(header + [body])
            cid = chunk_id(namespace, key, len(out), text)
            # PASSAGE METADATA: where the chunk sits, for citations and "open at passage".
            passage = {"chunk_id": cid, "doc_id": doc_id(namespace, key), "chunk_meta": CHUNK_META,
                       "start_index": piece.start, "char_start": piece.start,
                       "char_end": piece.end, "block": piece.kind}
            if path:
                passage["heading_path"] = path
            if piece.rows:
                passage["rows"] = piece.rows
            if context:
                passage["chunk_context"] = context
            out.append((cid, text, _clean({**source.metadata, **meta, **passage})))
        stack = chunking.heading_stack(found, len(page_text), stack)
    return out


def _embed_and_store(store, items, report):
    """3 & 4. EMBED + STORE — turn chunks into vectors and save them.
    EMBED: the embedding model (OpenAI's, or the workspace's Bedrock one, models.py)
           reads each chunk and returns a list of ~1,000 numbers. Chunks
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
         workspace=None, doc_info=None, skip=None, doc_acls=None):
    """Build a search index from a docs folder, from scratch. Returns a summary dict.
    progress(step, total, message) — optional callback, e.g. for a UI progress bar.
    docs_folder / collection — which workspace (defaults = the original setup).
    connector_access — {provider: "public" | "restricted"} for synced files, from
    each connection's setting (integrations_store.chunk_access_levels); a provider
    missing from it is restricted.
    workspace — goes into the chunk ids (default: the collection's name).
    doc_info — {doc key: {title, url, provider}} for synced files (sync_store.doc_info).
    skip — skip(doc key, its tags) -> True leaves a document out: a source rule
    blocks it (kb/services/assistant.py).
    doc_acls — {doc key: ACL} for documents with one set (see doc_meta)."""
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
    items, failed, blocked = [], [], 0
    report(2, "Splitting into chunks")
    for key in keys:
        path = os.path.join(docs_folder, *key.split("/"))
        try:
            meta = doc_meta(docs_folder, key, connector_access, doc_info, doc_acls)
            if skip and skip(key, meta):
                blocked += 1
                continue
            meta.update(doc_stat=_file_stat(path), doc_hash=_doc_hash(path, meta),
                        modified=_modified(path, meta))
            items += load_and_split(docs_folder, key, meta, namespace)
        except Exception:
            log.exception("Index: couldn't read %s", key)
            failed.append(key)
    _embed_and_store(store, items, report)
    report(4, "Done")
    n_restricted = sum(1 for _, _, m in items if m["access"] == "restricted")
    return {"documents": len(keys) - len(failed) - blocked, "chunks": len(items),
            "restricted": n_restricted, "embedded": len(items), "failed": len(failed),
            "blocked": blocked}


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
    values = {tuple(v) if isinstance(v, list) else v for v in (m.get(field) for m in metas)}
    value = values.pop() if len(values) == 1 else None
    return list(value) if isinstance(value, tuple) else value


def update(progress=None, docs_folder=DOCS_FOLDER, collection=None, connector_access=None,
           workspace=None, only=None, doc_info=None, skip=None, doc_acls=None):
    """Bring an existing collection in line with the docs folder, document by
    document. Returns a summary dict (with what was embedded, re-tagged, removed).

    For every document (or only the keys in `only`):
      unchanged (same size, time and tags)       nothing to do
      same content, other tags (e.g. its ACL)    its chunks' metadata is updated:
                                                 no embedding call
      same content, older CHUNK_META             re-split: the same ids come out,
                                                 so only their metadata is updated
      new or edited                              re-split; chunks whose id is new
                                                 are embedded, gone ones deleted
      deleted                                    its chunks are deleted
      blocked by a source rule (skip, see main)  its chunks are deleted, as if it
                                                 were; unblocked, it is added again
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

    failed = []
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
        meta = doc_meta(docs_folder, key, connector_access, doc_info, doc_acls)
        if skip and skip(key, meta):
            if have:
                deletes += have["ids"]
            stats["blocked"] += 1
            continue
        stat = _file_stat(path)
        current = bool(have) and _same(have["metas"], "chunk_meta") == CHUNK_META
        if current and _same(have["metas"], "doc_stat") == stat and all(
                _same(have["metas"], k) == meta.get(k) for k in DOC_FIELDS):
            continue                                        # unchanged
        try:
            meta["doc_stat"] = stat
            meta["doc_hash"] = _doc_hash(path, meta)
            meta["modified"] = _modified(path, meta)
            if current and _same(have["metas"], "doc_hash") == meta["doc_hash"]:
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
            "retagged": stats["retagged"], "removed": stats["removed"], "failed": len(failed),
            "blocked": stats["blocked"]}


def retag(collection, connector, level, acls=None):
    """Give every chunk synced by `connector` its document's ACL (acls: {doc key:
    ACL}), or the access level `level`'s for a document without one, without
    embedding anything: only `acl` and `access` change. Returns how many chunks
    changed."""
    store = _store(collection)
    got = store._collection.get(where={"connector": connector}, include=["metadatas"])
    updates = []
    for cid, m in zip(got["ids"], got["metadatas"]):
        acl = (acls or {}).get(m.get("doc_key"))
        acl = access.clean_acl(acl) if acl else access.level_acl(level)
        tags = {"acl": acl, "access": access.level_of_acl(acl)}
        if m.get("acl") != acl or m.get("access") != tags["access"]:
            updates.append((cid, tags))
    _update_tags(store, updates)
    return len(updates)


def _update_tags(store, updates):
    """[(chunk id, metadata to merge into its own)], in batches."""
    for start in range(0, len(updates), 1000):
        batch = updates[start:start + 1000]
        store._collection.update(ids=[i for i, _ in batch], metadatas=[t for _, t in batch])


def backfill_acls(store):
    """Chunks indexed before ACLs get their access level's (access.level_acl), in
    place, with no embedding. Retrieval filters on `acl`, so this runs before a
    collection is searched (rag.load_chain). Returns how many chunks changed."""
    got = store._collection.get(include=["metadatas"])
    updates = [(cid, {"acl": access.level_acl((m or {}).get("access", "public"))})
               for cid, m in zip(got["ids"], got["metadatas"])
               if not isinstance((m or {}).get("acl"), list)]
    _update_tags(store, updates)
    if updates:
        log.info("Index: gave %d chunks from before ACLs the ACL of their access level",
                 len(updates))
    return len(updates)


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
