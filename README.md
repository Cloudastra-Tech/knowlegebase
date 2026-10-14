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
| **React + FastAPI** | The chat website (`frontend/`) and its API (`api.py`) | The front door you actually walk through |
| **Google / Microsoft sign-in** | Who you are, and what you may see | The badge that opens some doors and not others |

**Everything is free except the OpenAI API** — and that costs only cents for a project this size.

---

## 🔄 How the app works (the flow)

There are **two phases**: studying (once), then answering (every question).

### Phase A — `ingest.py` (run ONCE — "studying")
```
Your files → LOAD → SPLIT into chunks → EMBED (to numbers) → STORE in Chroma
```

### Phase B — `api.py` + `agent.py` + `rag.py` (every time you ask — "open-book answering")
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
| `agent.py` | The agent: picks tools (docs search, calculator, currency, Wikipedia, wiki) and chains them. |
| `safety.py` | The safety rules (input + output checks). |
| `api.py` | The API behind the website: chat, documents, integrations, sign-in. |
| `frontend/` | The website (React + shadcn/ui). |
| `auth.py` / `sso.py` | Sign-in: username + password accounts, and Google / Microsoft. Both decide your **role**. |
| `app.py` | The original Streamlit chat website (still works; the React site is the main one). |
| `docs/` | Your `.txt` and `.pdf` files: `docs/public/` for everyone, `docs/restricted/` for HR / Admin. |
| `tests/` | Automatic checks — run `pytest`. |
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

**4. Create a login** (once). `--role hr` makes it HR / Admin; leave it off for a
normal (public) user:
```powershell
venv\Scripts\python.exe auth.py add <username> --role hr
```
Or sign in with Google / Microsoft instead — see step 4c of `deploy.md`.

**5. Start chatting** — the website needs Node.js 20+:
```powershell
venv\Scripts\uvicorn api:app --reload --port 8010  # terminal 1: the API
cd frontend; npm install; npm run dev              # terminal 2: the website
```
Open **http://localhost:5173**, sign in, and ask: *"What is the refund policy?"*

What you see depends on your account's **role**. Everyone gets **Chat** and
**Knowledge**; HR / Admin accounts also get **Documents** and **Integrations**, can
search restricted files, and can switch to *View as: Public user* to see what
others see. The role comes from your account, never from the browser.

> Want to use your own files? Add them on the **Documents** screen (they're
> re-indexed automatically), or drop them into `docs/` and run `python ingest.py`.

**Check everything still works:**
```powershell
pip install -r requirements-dev.txt
pytest
```

### Or: the original Streamlit app
```powershell
streamlit run app.py
```
Same assistant, simpler screen. It uses the same password accounts and roles.

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
