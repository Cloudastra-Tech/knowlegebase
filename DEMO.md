# 🎤 DEMO GUIDE — "Chat With Your Notes"

A step-by-step script to **demo this project to anyone** (manager, team, interview).
Follow it top to bottom. Each section = what to SAY + what to SHOW.

---

## 0. Before you start (setup — 1 minute)

Create an HR / Admin login once (it asks for the password). Optionally also a
normal one, to show that a public account can't switch to HR:

```powershell
venv\Scripts\python.exe auth.py add demo --role hr
venv\Scripts\python.exe auth.py add guest
```

Then, in the project folder, start the API and the website (two terminals):

```powershell
venv\Scripts\uvicorn api:app --port 8010
cd frontend; npm run dev
```

Open **http://localhost:5173** and sign in as `demo`.
Keep the **API terminal visible too** — it prints the agent's tool calls live (great for the demo).

> 💡 If documents ever change, rebuild the DB first: `venv\Scripts\python.exe ingest.py`

---

## 1. The one-line pitch (say this first)

> "This is a **secure AI assistant** that answers questions from **our own documents**.
> It only answers from what it's allowed to see, it's protected against unsafe input,
> and it's an **agent** — it can use tools like a calculator and live currency rates,
> and chain them together to answer multi-step questions."

---

## 2. Show the architecture (30 seconds)

Open **ARCHITECTURE.md** in the IDE (Ctrl+Shift+V for the diagram preview) and say:

> "There are two parts: an **ingest step** that reads our documents once and stores
> them in a searchable database, and a **question step** that runs every time someone
> asks something. On top of that sits an **agent** that decides which tools to use."

Point to the layers:
1. **React website + FastAPI** — the chat screen and the API behind it
2. **RAG pipeline** — finds the right piece of our docs and answers from it
3. **Guardrails + permissions** — safety and access control
4. **LangGraph agent** — decides + chains tools

---

## 3. DEMO PART A — It answers from our documents

**View as: Public user** (the role menu in the header — only HR accounts have it)

Ask:
```
What is the refund policy?
```
👉 **Show:** the answer comes from the documents. Open the "🛠️ Agent steps" panel —
it used **search_docs** (one tool).

> "It didn't guess. It searched our documents, found the relevant chunk, and answered
> only from that. That's **RAG** — Retrieval-Augmented Generation."

Ask another:
```
stipend for student
```
👉 Same thing — one tool, grounded answer.

---

## 4. DEMO PART B — It chains MULTIPLE tools (the wow moment)

Ask:
```
What is the Pro plan per year in US dollars?
```

👉 **Watch the terminal** — it prints, live:
```
[agent] -> search_docs(...)        = 2000 rupees/month
[agent] -> calculator(2000*12)     = 24000
[agent] -> convert_currency(...)   = 254.43 USD
```

> "Notice nobody told it the steps. The **AI decided**: first get the monthly price
> from our docs, then multiply by 12, then convert rupees to dollars using **live
> exchange rates**. It chained **three tools** on its own. That's an **agent**."

---

## 5. DEMO PART C — It uses general knowledge when needed

Ask:
```
Who founded Anthropic?
```

👉 It uses the **wikipedia** tool, not our docs.

> "It knows our docs don't have this, so it picks the right tool — Wikipedia — instead."

---

## 6. DEMO PART D — Security & access control (the important part)

Still **signed in as Public user**, ask:
```
What is the teacher's salary?
```
👉 It is **BLOCKED / refuses**.

> "A public user can't see salaries. Two things protect this: a **permission filter**
> that won't even search the salary file, and a **guardrail** that blocks the question."

Now **switch View as back to: HR / Admin** and ask the **same question**:
```
What is the teacher's salary?
```
👉 Now it **answers** (e.g. "60,000 rupees").

> "Same question, different role, different result. HR is authorized, so the salary
> file is searchable for them. That's **role-based access control** — the whole point."

Optional: sign out and sign in as `guest`. There's **no role menu** at all.

> "The role comes from the account — a password account here, or the company's
> Google / Microsoft login in production. The website can't promote anyone: the
> server checks the account's role on every single request."

---

## 7. How it works under the hood (explain after the live demo)

Use this simple story:

> "Think of three roles:
> - 🧠 a **Brain** (the AI) that decides what to do,
> - 🏃 **Runners** (the tools: search docs, calculator, currency, Wikipedia),
> - 👔 a **Manager** (**LangGraph**) that loops: *ask the Brain → run a tool →
>   ask again* — until the Brain has the final answer.
>
> The Brain picks tools based on their **descriptions** and a **system prompt** —
> there's no hard-coded if/else. The Manager just keeps the loop going."

Then point to the code:
- **agent.py** — the tools + the LangGraph graph (the loop)
- **rag.py** — retrieval + answer generation (hybrid search: keyword + meaning, merged with RRF)
- **safety.py** — the input/output guardrails
- **api.py** + **frontend/** — the API and the React chat website
- **auth.py** + **sso.py** — sign-in (password, Google, Microsoft) and where your role comes from

---

## 8. The tech stack (one slide)

| Tool | Job |
|---|---|
| **React + FastAPI** | the chat website and its API |
| **Google / Microsoft sign-in** | single sign-on; the account decides the role |
| **LangChain** | the model, tools, messages, RAG pieces |
| **LangGraph** | the agent loop (decide → run tool → repeat) |
| **OpenAI** | embeddings (`text-embedding-3-small`) + answers (`gpt-4o-mini`) |
| **Chroma** | vector database (meaning search) |
| **BM25 + RRF** | keyword search, merged with meaning search |
| **Guardrails AI** | safety checks on input & output |
| **Permission filter** | role-based access control |

---

## 9. What's next (roadmap — shows you have a plan)

> "Right now each question is independent. **Next step is memory** — using a
> LangGraph checkpointer so it remembers the conversation and handles follow-ups
> like 'and per year?'. After that: saving chat history to disk, and adding more tools."

---

## 10. Cheat-sheet — questions to ask during the demo

| Goal | Ask this | Tool(s) it uses |
|---|---|---|
| Doc answer | `What is the refund policy?` | search_docs |
| Doc answer | `stipend for student` | search_docs |
| **Multi-tool chain** | `Pro plan per year in US dollars?` | search_docs → calculator → convert_currency |
| General knowledge | `Who founded Anthropic?` | wikipedia |
| **Security (blocked)** | `teacher's salary?` *(as Public)* | blocked 🚫 |
| **Security (allowed)** | `teacher's salary?` *(as HR/Admin)* | search_docs ✅ |

---

### 30-second version (if you're short on time)
1. Ask **"Pro plan per year in USD?"** → show it chaining 3 tools in the terminal.
2. Ask **"teacher's salary?"** as Public (blocked) then as HR (answers).
3. Say: *"RAG answers from our docs, an agent chains tools, and access is role-based."*
