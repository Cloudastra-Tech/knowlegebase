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
"""

import json
import os
from dotenv import load_dotenv

# LangChain pieces — each is one step of the pipeline
from langchain_community.document_loaders import DirectoryLoader, TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma

# Load the secret OPENAI_API_KEY from the .env file into the program.
load_dotenv()

# Where your documents live, and where the database will be saved.
DOCS_FOLDER = "docs"
DB_FOLDER = "chroma_db"


def _connector_metadata(documents):
    """Files synced by a connector (docs/.../connectors/<provider>/) come with a
    manifest.json giving each file's real title and link — attach it so answers
    can cite "Q3 plan (Google Drive)" with a link instead of a file id."""
    manifests = {}
    for doc in documents:
        source = doc.metadata.get("source", "")
        if "connectors" not in source.replace("\\", "/").split("/"):
            continue
        folder = os.path.dirname(source)
        if folder not in manifests:
            try:
                with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
                    manifests[folder] = json.load(f)
            except (OSError, ValueError):
                manifests[folder] = {}
        info = manifests[folder].get(os.path.basename(source))
        if info:
            doc.metadata.update(title=info.get("title") or "", url=info.get("url") or "",
                                provider=info.get("provider") or "")


def _connector_of(source, docs_folder):
    """The provider whose sync wrote this file (docs/.../connectors/<provider>/...), or None."""
    parts = os.path.relpath(source, docs_folder).replace("\\", "/").split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "connectors" else None


def main(progress=None, docs_folder=DOCS_FOLDER, collection=None, connector_access=None):
    """Rebuild a search index from a docs folder. Returns a summary dict.
    progress(step, total, message) — optional callback, e.g. for a UI progress bar.
    docs_folder / collection — which workspace (defaults = the original setup).
    connector_access — {provider: "public" | "restricted"} for synced files, from
    each connection's setting (integrations_store.chunk_access_levels); a provider
    missing from it is restricted."""
    connector_access = connector_access or {}
    collection = collection or "langchain"
    def report(step, message):
        if progress:
            progress(step, 4, message)

    # ----------------------------------------------------------------
    # 1. LOAD  —  read the raw text from every file in the docs/ folder
    # ----------------------------------------------------------------
    # A "Document" in LangChain = the text + some metadata (like the filename).
    print("1) Loading documents from the 'docs' folder...")
    report(1, "Reading documents")

    documents = []
    if os.path.isdir(docs_folder):
        txt_loader = DirectoryLoader(docs_folder, glob="**/*.txt", loader_cls=TextLoader,
                                     # UTF-8 first (Windows would otherwise guess cp1252
                                     # and garble text), detect the encoding only if that fails.
                                     loader_kwargs={"encoding": "utf-8",
                                                    "autodetect_encoding": True})
        pdf_loader = DirectoryLoader(docs_folder, glob="**/*.pdf", loader_cls=PyPDFLoader)
        documents = txt_loader.load() + pdf_loader.load()
    _connector_metadata(documents)
    print(f"   -> Loaded {len(documents)} document(s).")

    # ----------------------------------------------------------------
    # 1b. CLEAN  —  empty the old collection so we always rebuild fresh.
    # ----------------------------------------------------------------
    # WHY: Chroma ADDS to an existing DB, it doesn't replace it. Without this,
    # re-running ingest leaves stale/duplicate chunks from old documents mixed
    # in, which pollutes search. We drop the collection (instead of deleting
    # the folder) so this also works while the app has the DB open — the
    # "Re-index" button in the UI — and when chroma_db is a Docker volume.
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    if os.path.exists(DB_FOLDER):
        print(f"   Clearing the old collection in '{DB_FOLDER}' for a clean rebuild...")
        Chroma(persist_directory=DB_FOLDER, embedding_function=embeddings,
               collection_name=collection).delete_collection()

    if not documents:
        # Nothing left (e.g. every doc deleted): the cleared index is the result.
        print("   !! No documents found. Put a .txt or .pdf file in the 'docs' folder.")
        report(4, "Done")
        return {"documents": 0, "chunks": 0, "restricted": 0}

    # ----------------------------------------------------------------
    # 2. SPLIT  —  cut big text into small overlapping chunks
    # ----------------------------------------------------------------
    # WHY chunk? Two reasons:
    #   - The AI can only read a limited amount of text at once.
    #   - Search is more accurate on small focused pieces than on a whole book.
    # chunk_size      = how big each piece is (in characters)
    # chunk_overlap   = repeat a little text between pieces so we don't cut a
    #                   sentence in half and lose its meaning.
    print("2) Splitting documents into small chunks...")
    report(2, "Splitting into chunks")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
        add_start_index=True,   # remember where each chunk sits in its document
    )
    chunks = splitter.split_documents(documents)
    print(f"   -> Created {len(chunks)} chunk(s).")

    # CONTEXTUAL CHUNK HEADER: start every chunk with its document's title.
    # A chunk in the middle of "Bible Navigator — Technical Audit" often never
    # says "Bible Navigator"; without the title, a search for it misses those
    # chunks and the model can't tell what they are about.
    for chunk in chunks:
        title = chunk.metadata.get("title") or os.path.splitext(
            os.path.basename(chunk.metadata.get("source", "")))[0]
        if title and not chunk.page_content.startswith(title):
            chunk.page_content = f"[{title}]\n{chunk.page_content}"

    # ----------------------------------------------------------------
    # 2b. TAG  —  give every chunk an ACCESS LEVEL (permission control).
    # ----------------------------------------------------------------
    # This is how enterprises secure RAG: each chunk carries metadata saying
    # WHO may see it. Chunks from docs/restricted/ are "restricted"; other
    # uploads are "public". Files synced by a connector take the level its
    # admin chose for that connection (restricted unless opened to everyone).
    # At query time we only fetch chunks the user is allowed to see — so
    # confidential text never reaches the model for the wrong user.
    for chunk in chunks:
        source = chunk.metadata.get("source", "").replace("\\", "/")
        provider = _connector_of(source, docs_folder)
        if provider:
            chunk.metadata["access"] = connector_access.get(provider, "restricted")
        else:
            chunk.metadata["access"] = "restricted" if "/restricted/" in source else "public"

    n_restricted = sum(1 for c in chunks if c.metadata["access"] == "restricted")
    print(f"   -> Tagged access levels: {n_restricted} restricted, "
          f"{len(chunks) - n_restricted} public.")

    # ----------------------------------------------------------------
    # 3 & 4. EMBED + STORE  —  turn chunks into vectors and save them
    # ----------------------------------------------------------------
    # EMBED: OpenAI reads each chunk and returns a list of ~1536 numbers.
    #        Chunks with similar MEANING get similar numbers. That's the magic
    #        that lets us search by meaning instead of by exact keywords.
    # STORE: Chroma saves these vectors on your disk in the 'chroma_db' folder.
    print("3) Creating embeddings (calling OpenAI) and storing them in Chroma...")
    report(3, f"Embedding {len(chunks)} chunks")

    # This single call does the embedding AND the saving.
    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_FOLDER,
        collection_name=collection,
    )

    print(f"   -> Done! Your knowledge base is saved in the '{DB_FOLDER}' folder.")
    report(4, "Done")
    print("\nNext step: run  ->  uvicorn api:app --reload --port 8010")
    return {"documents": len(documents), "chunks": len(chunks),
            "restricted": n_restricted}


if __name__ == "__main__":
    import integrations_store
    import workspaces
    main(connector_access=integrations_store.chunk_access_levels(workspaces.DEFAULT))
