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

import os
import shutil
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


def main():
    # ----------------------------------------------------------------
    # 0. CLEAN  —  delete the old database so we always rebuild fresh.
    # ----------------------------------------------------------------
    # WHY: Chroma ADDS to an existing DB, it doesn't replace it. Without this,
    # re-running ingest leaves stale/duplicate chunks from old documents mixed
    # in, which pollutes search. Wiping first guarantees the DB matches docs/.
    if os.path.exists(DB_FOLDER):
        print(f"0) Removing old database '{DB_FOLDER}' for a clean rebuild...")
        shutil.rmtree(DB_FOLDER)

    # ----------------------------------------------------------------
    # 1. LOAD  —  read the raw text from every file in the docs/ folder
    # ----------------------------------------------------------------
    # A "Document" in LangChain = the text + some metadata (like the filename).
    print("1) Loading documents from the 'docs' folder...")

    txt_loader = DirectoryLoader(DOCS_FOLDER, glob="**/*.txt", loader_cls=TextLoader)
    pdf_loader = DirectoryLoader(DOCS_FOLDER, glob="**/*.pdf", loader_cls=PyPDFLoader)

    documents = txt_loader.load() + pdf_loader.load()
    print(f"   -> Loaded {len(documents)} document(s).")

    if not documents:
        print("   !! No documents found. Put a .txt or .pdf file in the 'docs' folder.")
        return

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

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
    )
    chunks = splitter.split_documents(documents)
    print(f"   -> Created {len(chunks)} chunk(s).")

    # ----------------------------------------------------------------
    # 2b. TAG  —  give every chunk an ACCESS LEVEL (permission control).
    # ----------------------------------------------------------------
    # This is how enterprises secure RAG: each chunk carries metadata saying
    # WHO may see it. Chunks from docs/restricted/ are "restricted"; everything
    # else is "public". At query time we only fetch chunks the user is allowed
    # to see — so confidential text never reaches the model for the wrong user.
    for chunk in chunks:
        source = chunk.metadata.get("source", "").replace("\\", "/")
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

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    # This single call does the embedding AND the saving.
    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_FOLDER,
    )

    print(f"   -> Done! Your knowledge base is saved in the '{DB_FOLDER}' folder.")
    print("\nNext step: run  ->  streamlit run app.py")


if __name__ == "__main__":
    main()
