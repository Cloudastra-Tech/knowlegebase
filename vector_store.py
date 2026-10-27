"""
vector_store.py  —  where the search index lives (pipeline.md P2-1, platform.md PL-04).

    VECTOR_STORE=chroma     (the default) an embedded Chroma folder, DB_FOLDER
    VECTOR_STORE=pgvector   the app's Postgres database (DATABASE_URL), in one
                            table, kb_chunks, with row-level security on its
                            workspace like every other tenant table

ingest.py and rag.py open a collection with store(); everything else about the
index goes through the functions below, so neither knows which one it is. What
they use of a store, both have:

    store.embeddings                  the embedding function
    store.similarity_search(q, k, filter=where)   nearest chunks (L2, as Chroma's
                                      default), only those `where` keeps
    store.as_retriever(search_kwargs={"k", "filter"})
    store.get()                       every chunk: {"ids", "documents", "metadatas"}
    store.delete_collection()
    store._collection.get(ids=, where=, include=)    raw reads
    store._collection.upsert / add(ids, embeddings, documents, metadatas)
    store._collection.update(ids, metadatas)   merged into each chunk's own
    store._collection.delete(ids= | where=), count()

`where` is Chroma's filter language; PgVectorStore translates the part the app
uses: field equality, $eq, $ne, $in, $nin, $gt/$gte/$lt/$lte, $and, $or, and
$contains on a list. A chunk's ACL (access.chroma_filter: an $or of
{"acl": {"$contains": principal}}) becomes `acl && $principals` on a text[]
column with a GIN index.

On pgvector, `folder` (DB_FOLDER) is a namespace inside the table, as a Chroma
folder is a separate index: the app has one ("chroma_db"), and a throwaway one
(the eval harness, a test) never sees another's chunks.
"""

import contextlib
import json
import os
import threading
import time
import uuid

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore

TABLE = "kb_chunks"
COLLECTIONS = "kb_collections"
HNSW_MAX_DIMS = 2000                     # pgvector's HNSW limit for `vector`
_ready = set()                           # databases with the table, this process
_iterative = {}                          # database -> has hnsw.iterative_scan (pgvector 0.8+)
_ready_lock = threading.Lock()


class NotFoundError(Exception):
    """No such collection (as chromadb's NotFoundError)."""


def backend():
    name = os.getenv("VECTOR_STORE", "chroma").strip().lower() or "chroma"
    if name not in ("chroma", "pgvector"):
        raise ValueError(f"VECTOR_STORE must be chroma or pgvector, not {name!r}")
    return name


def store(collection, embeddings, folder):
    """The collection (made if it doesn't exist yet), as a LangChain vector store."""
    if backend() == "pgvector":
        return PgVectorStore(collection, embeddings, folder)
    from langchain_chroma import Chroma
    return Chroma(persist_directory=folder, embedding_function=embeddings,
                  collection_name=collection)


def collection_names(folder):
    """The names of every collection in the index."""
    if backend() == "pgvector":
        with _connect() as conn:
            return [r[0] for r in conn.execute(
                f"SELECT name FROM {COLLECTIONS} WHERE namespace = ? ORDER BY name",
                (_namespace(folder),))]
    if not os.path.isdir(folder):
        return []
    import chromadb
    return [getattr(c, "name", c) for c in chromadb.PersistentClient(path=folder).list_collections()]


def drop_collection(folder, name):
    if backend() == "pgvector":
        PgVectorStore(name, None, folder).delete_collection()
        return
    import chromadb
    chromadb.PersistentClient(path=folder).delete_collection(name)


def existing(folder, name):
    """The raw collection (with .get()), or NotFoundError if there's none."""
    if backend() == "pgvector":
        if name not in collection_names(folder):
            raise NotFoundError(name)
        return PgVectorStore(name, None, folder)._collection
    import chromadb
    return chromadb.PersistentClient(path=folder).get_collection(name)


def check(folder):
    """Raises if the index can't be read (health.py)."""
    if backend() == "pgvector":                  # read only: nothing is made here
        import db
        import db_pg
        conn = db_pg.database(db.DATABASE_URL).connect()
        try:
            if not conn.execute("SELECT to_regclass(?)", (TABLE,)).fetchone()[0]:
                raise NotFoundError("no search index yet (run ingest.py)")
            conn.execute(f"SELECT 1 FROM {TABLE} LIMIT 1").fetchall()
        finally:
            conn.close()
        return
    import chromadb
    chromadb.PersistentClient(path=folder).count_collections()


# ---- pgvector -----------------------------------------------------------------------
def workspace_of(collection):
    """The workspace a collection belongs to (workspaces.collection, plus a
    rebuild's __g<n>): what row-level security checks its chunks against."""
    import workspaces
    base = (collection or "langchain").split("__g", 1)[0]
    if base == workspaces.collection(workspaces.DEFAULT):
        return workspaces.DEFAULT
    return base[3:] if base.startswith("ws_") else base


def _namespace(folder):
    return os.path.normcase(os.path.abspath(folder or "chroma_db"))


@contextlib.contextmanager
def _connect():
    """A connection to the app's database (the request's or job's tenant's, as any
    other), committed when the block ends and closed."""
    import db
    if not db.is_postgres():
        raise RuntimeError("VECTOR_STORE=pgvector needs DATABASE_URL (a postgresql:// URL)")
    url = db.DATABASE_URL
    if url not in _ready:
        with _ready_lock:
            if url not in _ready:
                _make_tables()
                _ready.add(url)
                _iterative[url] = _has_iterative_scan()
    conn = db.connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _make_tables():
    """The tables and their indexes, once per database, as the platform role
    (making the vector extension may need more than a tenant has). Row-level
    security as on every tenant table (migrations/postgres.py)."""
    import db
    import db_pg
    from migrations import postgres
    with db.platform():
        conn = db.connect()
    try:
        conn.isolation_level = None
        conn.execute("BEGIN")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('kb.vector_store.tables'))")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {COLLECTIONS} (
            namespace   TEXT NOT NULL,
            name        TEXT NOT NULL,
            workspace   TEXT NOT NULL,
            created_at  DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (namespace, name))""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            namespace   TEXT NOT NULL,          -- the index (DB_FOLDER)
            collection  TEXT NOT NULL,
            id          TEXT NOT NULL,          -- the chunk id (ingest.chunk_id)
            workspace   TEXT NOT NULL,          -- whose: row-level security
            seq         BIGINT GENERATED BY DEFAULT AS IDENTITY,   -- the order added
            document    TEXT,
            metadata    JSONB NOT NULL DEFAULT '{{}}',
            acl         TEXT[] NOT NULL DEFAULT '{{}}',   -- metadata.acl, for acl && ...
            dims        INTEGER NOT NULL,
            embedding   vector NOT NULL,
            PRIMARY KEY (namespace, collection, id))""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_acl ON {TABLE} USING gin (acl)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_metadata ON {TABLE} "
                     "USING gin (metadata jsonb_path_ops)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_by_seq ON {TABLE} "
                     "(namespace, collection, seq)")
        role = db_pg.TENANT_ROLE
        if db.RLS and conn.execute("SELECT 1 FROM pg_roles WHERE rolname = ?",
                                   (role,)).fetchone():
            native = _Native(conn._raw_conn())
            for table in (TABLE, COLLECTIONS):
                postgres.secure_table(native, table, {"workspace"})
                native.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} '
                                       f'TO "{role}"')
            native.exec_driver_sql(f'GRANT USAGE, SELECT ON SEQUENCE {TABLE}_seq_seq TO "{role}"')
        conn.execute("COMMIT")
    finally:
        conn.close()


def _has_iterative_scan():
    """pgvector 0.8 or later: a filtered HNSW scan can keep going until it has k
    rows (hnsw.iterative_scan). Older ones refuse the setting."""
    import db
    with db.platform():
        conn = db.connect()
    try:
        version = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                               ).fetchone()
    finally:
        conn.close()
    try:
        return tuple(int(p) for p in version[0].split(".")[:2]) >= (0, 8)
    except (TypeError, ValueError):
        return False


class _Native:
    """A psycopg connection as migrations.postgres's helpers take one."""

    def __init__(self, raw):
        self.raw = raw

    def exec_driver_sql(self, sql, params=None):
        return self.raw.execute(sql, params)


def _vector(values):
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _acl(meta):
    acl = (meta or {}).get("acl")
    return [str(p) for p in acl] if isinstance(acl, (list, tuple)) else []


def _where(where, args):
    """Chroma's `where` as SQL (appending its parameters to args)."""
    if not where:
        return "TRUE"
    clauses = []
    for key, cond in where.items():
        if key in ("$and", "$or"):
            parts = list(cond)
            acl = [p["acl"]["$contains"] for p in parts if isinstance(p, dict) and
                   list(p) == ["acl"] and isinstance(p["acl"], dict) and
                   list(p["acl"]) == ["$contains"]]
            if key == "$or" and parts and len(acl) == len(parts):
                clauses.append("acl && ?::text[]")          # an ACL filter: any principal
                args.append(acl)
                continue
            joined = f" {key[1:].upper()} ".join(_where(p, args) for p in parts)
            clauses.append(f"({joined})" if parts else ("TRUE" if key == "$and" else "FALSE"))
            continue
        ops = cond if isinstance(cond, dict) else {"$eq": cond}
        for op, value in ops.items():
            if op == "$contains" and key == "acl":
                clauses.append("acl @> ?::text[]")
                args.append([str(value)])
            elif op == "$contains":
                clauses.append("metadata -> ?::text @> ?::jsonb")
                args += [key, json.dumps([value])]
            elif op in ("$eq", "$ne"):
                clauses.append(("" if op == "$eq" else "NOT ") + "metadata @> ?::jsonb")
                args.append(json.dumps({key: value}))
            elif op in ("$in", "$nin"):
                values = [json.dumps(v) for v in value]
                if not values:
                    clauses.append("FALSE" if op == "$in" else "TRUE")
                    continue
                test = f"metadata -> ?::text IN ({', '.join('?::jsonb' for _ in values)})"
                clauses.append(test if op == "$in" else f"NOT COALESCE({test}, FALSE)")
                args += [key, *values]
            elif op in ("$gt", "$gte", "$lt", "$lte"):
                sign = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}[op]
                clauses.append(f"(jsonb_typeof(metadata -> ?::text) = 'number' AND "
                               f"(metadata ->> ?::text)::double precision {sign} ?)")
                args += [key, key, float(value)]
            else:
                raise ValueError(f"vector_store: filter operator {op} isn't supported")
    return " AND ".join(clauses) if len(clauses) == 1 else "(" + " AND ".join(clauses) + ")"


class PgCollection:
    """The collection's raw API, as chromadb's Collection (the part the app uses)."""

    def __init__(self, owner):
        self._owner = owner
        self.name = owner.collection
        self.metadata = None

    def _scope(self):
        return "namespace = ? AND collection = ?", [self._owner.namespace, self.name]

    def get(self, ids=None, where=None, include=("metadatas", "documents"), limit=None,
            offset=None, where_document=None):
        if where_document:
            raise ValueError("vector_store: where_document isn't supported on pgvector")
        sql, args = self._scope()
        if ids is not None:
            ids = list(ids)
            if not ids:
                return {"ids": [], "documents": [], "metadatas": [], "embeddings": None}
            sql += f" AND id IN ({', '.join('?' for _ in ids)})"
            args += ids
        sql += " AND " + _where(where, args)
        sql = f"SELECT id, document, metadata FROM {TABLE} WHERE {sql} ORDER BY seq"
        if limit is not None:
            sql, args = sql + " LIMIT ?", args + [int(limit)]
        if offset:
            sql, args = sql + " OFFSET ?", args + [int(offset)]
        with _connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        include = set(include or ())
        return {"ids": [r[0] for r in rows],
                "documents": [r[1] for r in rows] if "documents" in include else None,
                "metadatas": [_meta(r[2]) for r in rows] if "metadatas" in include else None,
                "embeddings": None}

    def upsert(self, ids, embeddings=None, documents=None, metadatas=None):
        ids = list(ids)
        if not ids:
            return
        if embeddings is None:
            embeddings = self._owner.embeddings.embed_documents(documents)
        documents = documents or [None] * len(ids)
        metadatas = metadatas or [{}] * len(ids)
        ws = workspace_of(self.name)
        rows = [(self._owner.namespace, self.name, i, ws, d, json.dumps(m or {}), _acl(m),
                 len(e), _vector(e))
                for i, e, d, m in zip(ids, embeddings, documents, metadatas)]
        self._owner._register()
        with _connect() as conn:
            conn.executemany(
                f"INSERT INTO {TABLE} (namespace, collection, id, workspace, document, "
                "metadata, acl, dims, embedding) VALUES (?, ?, ?, ?, ?, ?::jsonb, ?, ?, "
                "?::vector) ON CONFLICT (namespace, collection, id) DO UPDATE SET "
                "document = EXCLUDED.document, metadata = EXCLUDED.metadata, "
                "acl = EXCLUDED.acl, dims = EXCLUDED.dims, embedding = EXCLUDED.embedding",
                rows)
        for dims in {len(e) for e in embeddings}:
            self._owner._index_for(dims)

    def add(self, ids, embeddings=None, documents=None, metadatas=None):
        self.upsert(ids, embeddings, documents, metadatas)

    def update(self, ids, metadatas=None, documents=None, embeddings=None):
        """Each chunk's metadata gets these keys (merged, as Chroma does)."""
        ids = list(ids)
        if documents is not None or embeddings is not None:
            got = self.get(ids=ids, include=["documents", "metadatas"])
            have = dict(zip(got["ids"], zip(got["documents"], got["metadatas"])))
            docs = documents or [have.get(i, (None, None))[0] for i in ids]
            metas = [{**(have.get(i, (None, {}))[1] or {}), **(m or {})}
                     for i, m in zip(ids, metadatas or [{}] * len(ids))]
            self.upsert(ids, embeddings, docs, metas)
            return
        sql, scope = self._scope()
        with _connect() as conn:
            conn.executemany(
                f"UPDATE {TABLE} SET metadata = metadata || ?::jsonb, "
                f"acl = COALESCE(?::text[], acl) WHERE {sql} AND id = ?",
                [(json.dumps(m or {}), _acl(m) if "acl" in (m or {}) else None, *scope, i)
                 for i, m in zip(ids, metadatas or [])])

    def delete(self, ids=None, where=None):
        sql, args = self._scope()
        if ids is not None:
            ids = list(ids)
            if not ids:
                return
            sql += f" AND id IN ({', '.join('?' for _ in ids)})"
            args += ids
        sql += " AND " + _where(where, args)
        with _connect() as conn:
            conn.execute(f"DELETE FROM {TABLE} WHERE {sql}", args)

    def count(self):
        sql, args = self._scope()
        with _connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {sql}", args).fetchone()[0]


def _meta(value):
    if isinstance(value, str):
        return json.loads(value)
    return dict(value or {})


class PgVectorStore(VectorStore):
    """One collection in kb_chunks, as LangChain's Chroma store (see the top)."""

    def __init__(self, collection, embedding_function, folder=None):
        self.collection = collection or "langchain"
        self.namespace = _namespace(folder)
        self._embedding_function = embedding_function
        self._collection = PgCollection(self)

    @property
    def embeddings(self):
        return self._embedding_function

    def _register(self):
        with _connect() as conn:
            conn.execute(f"INSERT INTO {COLLECTIONS} (namespace, name, workspace, created_at) "
                         "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                         (self.namespace, self.collection, workspace_of(self.collection),
                          time.time()))

    def _index_for(self, dims):
        """An HNSW index for vectors of this size (one per size: the column is
        untyped, so a test's small vectors and the model's sit side by side)."""
        if dims > HNSW_MAX_DIMS:
            return
        import db
        with db.platform():
            conn = db.connect()
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_hnsw_{int(dims)} ON {TABLE} "
                         f"USING hnsw ((embedding::vector({int(dims)})) vector_l2_ops) "
                         f"WHERE dims = {int(dims)}")
        finally:
            conn.close()

    # ---- What LangChain's VectorStore asks for ----
    def add_texts(self, texts, metadatas=None, ids=None, **kwargs):
        texts = list(texts)
        ids = list(ids) if ids else [uuid.uuid4().hex for _ in texts]
        self._collection.upsert(ids, self.embeddings.embed_documents(texts), texts,
                                list(metadatas) if metadatas else None)
        return ids

    @classmethod
    def from_texts(cls, texts, embedding, metadatas=None, ids=None, collection_name=None,
                   persist_directory=None, **kwargs):
        store_ = cls(collection_name, embedding, persist_directory)
        store_.add_texts(texts, metadatas, ids)
        return store_

    def similarity_search_with_score(self, query, k=4, filter=None, **kwargs):
        vector = self.embeddings.embed_query(query)
        dims = len(vector)
        args = [self.namespace, self.collection]
        where = _where(filter, args)
        cast = f"vector({dims})" if dims <= HNSW_MAX_DIMS else "vector"
        # dims as a literal and the distance alone in ORDER BY: what lets the planner
        # use the partial HNSW index for this size (_index_for). Ties by seq below.
        sql = (f"SELECT id, document, metadata, embedding::{cast} <-> ?::{cast} AS distance, "
               f"seq FROM {TABLE} WHERE namespace = ? AND collection = ? AND dims = {int(dims)} "
               f"AND {where} ORDER BY distance LIMIT ?")
        import db
        with _connect() as conn:
            conn.isolation_level = None
            conn.execute("BEGIN")
            try:
                # pgvector 0.8: a filtered HNSW scan keeps going until it has k rows.
                if _iterative.get(db.DATABASE_URL):
                    conn.execute("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', "
                                 "true)")
                rows = conn.execute(sql, [_vector(vector), *args, int(k)]).fetchall()
            finally:
                conn.execute("COMMIT")
        rows = sorted(rows, key=lambda r: (r[3], r[4]))    # relaxed_order: sort them again
        return [(Document(id=r[0], page_content=r[1] or "", metadata=_meta(r[2])), r[3])
                for r in rows]

    def similarity_search(self, query, k=4, filter=None, **kwargs):
        return [d for d, _ in self.similarity_search_with_score(query, k, filter)]

    def _select_relevance_score_fn(self):
        return self._euclidean_relevance_score_fn

    def get(self, ids=None, where=None, limit=None, offset=None, include=None, **kwargs):
        return self._collection.get(ids=ids, where=where, limit=limit, offset=offset,
                                    include=include or ("metadatas", "documents"))

    def delete(self, ids=None, **kwargs):
        self._collection.delete(ids=ids)

    def delete_collection(self):
        with _connect() as conn:
            conn.execute(f"DELETE FROM {TABLE} WHERE namespace = ? AND collection = ?",
                         (self.namespace, self.collection))
            conn.execute(f"DELETE FROM {COLLECTIONS} WHERE namespace = ? AND name = ?",
                         (self.namespace, self.collection))
