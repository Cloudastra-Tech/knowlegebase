# 📚 Chat With Your Notes — a beginner-friendly RAG app

This is a small AI app that **answers questions about YOUR documents**.
You drop files in the `docs/` folder, and you can chat with them.

It's built so you can **understand every part** and explain it to anyone.

---

## 🧠 The one idea behind it all: RAG

**RAG = Retrieval-Augmented Generation.** Say it like this to anyone:

> "Instead of asking the AI from its memory (where it can make things up),
> we first **search our own documents** for the relevant bit, then **hand that
> to the AI** and say 'answer using only this.' So the answer is grounded in
> real facts, not guesses."

It's the difference between a **closed-book exam** (AI guesses from memory) and
an **open-book exam** (AI looks up the real answer first). RAG = open book.

---

## 🔧 The tech, and what each piece does (explain-to-anyone version)

| Tool | Job | Plain-English analogy |
|---|---|---|
| **LangChain** | Connects all the steps | The recipe that ties ingredients together |
| **OpenAI Embeddings** | Turns text into numbers | Gives every sentence a "GPS coordinate of meaning" |
| **Chroma** (vector DB) | Stores + searches those numbers | A librarian who finds books by *meaning*, not title |
| **OpenAI gpt-4o-mini** | Writes the final answer | The student who reads the found pages and answers |
| **Guardrails** | Safety checks | A bouncer checking what goes in and comes out |
| **Streamlit** | The chat website | The front door you actually walk through |

**Everything is free except the OpenAI API** — and that costs only cents for a project this size.

---

## 🔄 How the app works (the flow)

There are **two phases**: studying (once), then answering (every question).

### Phase A — `ingest.py` (run ONCE — "studying")
```
Your files → LOAD → SPLIT into chunks → EMBED (to numbers) → STORE in Chroma
```

### Phase B — `app.py` + `rag.py` (every time you ask — "open-book answering")
```
Your question → RETRIEVE matching chunks → AUGMENT the prompt with them
             → GENERATE the answer → GUARD (safety) → show answer + sources
```

---

## 📂 What each file is

| File | What it does |
|---|---|
| `ingest.py` | Reads `docs/`, chunks + embeds them, saves to Chroma. **Run this first.** |
| `rag.py` | The brain: retrieve → augment → generate → guard. |
| `guardrails.py` | The safety rules (input + output checks). |
| `app.py` | The Streamlit chat website. |
| `docs/` | Put your `.txt` and `.pdf` files here. (A `sample.txt` is included.) |
| `inspect_tokens.py` | Bonus: *see* how text becomes tokens (Phase 1 concept). |

---

## ▶️ How to run it (step by step)

**1. Install the tools** (do this once):
```powershell
pip install -r requirements.txt
```

**2. Add your OpenAI key:**
- Copy `.env.example` to a new file named `.env`
- Paste your key after `OPENAI_API_KEY=`
- Get a key at https://platform.openai.com/api-keys

**3. Study the documents** (turns docs into the searchable database):
```powershell
python ingest.py
```

**4. Start chatting:**
```powershell
streamlit run app.py
```
Your browser opens. Ask: *"What is the refund policy?"* — try it!

> Want to use your own files? Drop `.txt` or `.pdf` files into `docs/`,
> then run `python ingest.py` again.

---

## 🗺️ Where this fits in your learning roadmap

This Stage-1 app already covers:
- **Phase 1** — you *use* tokens & embeddings (run `inspect_tokens.py` to see tokens)
- **Phase 2** — embeddings + similarity search + a real vector DB ✅
- **Phase 3** — the full core RAG pipeline (chunk, index, retrieve, augment) ✅
- **Phase 4** — LangChain building blocks (loaders, splitters, prompts) ✅
- **Phase 6** — a basic guardrail ✅

**Coming in later stages** (same project, just add pieces):
- Stage 2 → hybrid search + reranking + RAG evaluation (finishes Phase 3)
- Stage 3 → LangGraph + tools + reflection (Phase 5: turns it into an AGENT)
- Stage 4 → caching + monitoring (rest of Phase 6)

---

## 💡 One-paragraph summary you can say out loud

> "I built an app where you give it your documents. It cuts them into chunks and
> turns each chunk into numbers that capture its meaning, stored in a vector
> database. When you ask a question, it finds the chunks closest in meaning,
> hands them to the AI, and the AI answers using only those — so it doesn't make
> things up. A guardrail checks the input and output for safety, and it all shows
> up in a simple chat website. That whole pattern is called RAG."
