# 🏗️ Chat With Your Notes — Architecture (Simple Version)

A plain-language map of how the whole app works and what tech does each job.
If you can read this, you can explain the app to anyone.

---

## 🧠 The big idea in one line
> You give it documents. You ask questions. It finds the right piece of your
> documents and the AI answers from it — **safely**, and **only what you're
> allowed to see**.

---

## 🔧 The tech, and the one job each does

| Tool | Its one job | Plain-English name |
|---|---|---|
| **React + FastAPI** | The chat website you type into, and its API | The front desk |
| **Sign-in** (password, Google, Microsoft) | Proves who you are; your account sets your role | The badge at the door |
| **LangChain** | Connects all the steps together | The recipe / glue |
| **OpenAI `text-embedding-3-small`** | Turns text into numbers (meaning) | The translator |
| **Chroma** | Stores those numbers + searches them | The librarian |
| **BM25** | Keyword search (exact words) | The index at the back of a book |
| **RRF** | Merges keyword + meaning results | The judge that ranks both |
| **OpenAI `gpt-4o-mini`** | Writes the final answer (ANSWER_MODEL; small jobs on FAST_MODEL, models.py) | The writer |
| **Guardrails AI** | Safety checks on input & output | The security guard |
| **Permission filter** | Decides which docs you may search | The keycard / access control |

**Everything is free except the two OpenAI models** (a few cents to run).

---

## 📦 PHASE 1 — Ingest (run once: "studying the documents")

```
  docs/public/handbook.txt          docs/restricted/staff_salaries.txt
        │                                      │
        └──────────────┬───────────────────────┘
                       ▼
              [ LangChain loads files ]
                       │
                       ▼
              [ Split into chunks by structure ]  ← chunking.py: headings, table rows, code, pages
                       │
                       ▼
        [ TAG each chunk: public or restricted ] ← permission metadata
                       │
                       ▼
     [ OpenAI embeddings: text → numbers ]       ← text-embedding-3-small
                       │
                       ▼
        [ Store numbers + tags in Chroma ]       ← chroma_db/ folder
```
**Result:** a searchable database where every chunk knows (a) its meaning-numbers
and (b) who is allowed to see it.

---

## 💬 PHASE 2 — Ask a question (runs every time)

```
                    ┌─────────────────────────────────────┐
   You type  ─────► │  Chat website  +  you, signed in     │   role = from your account
   a question       └──────────────────┬──────────────────┘
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  GUARD 1 — INPUT (Guardrails AI)      │
                    │  • too long? toxic?  → block (anyone) │
                    │  • about staff salary? → block        │
                    │       (ONLY if you're 'public')       │
                    └──────────────────┬───────────────────┘
                                       │ passes
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  RETRIEVE — search YOUR allowed docs  │
                    │                                       │
                    │   ┌── BM25 keyword search ──┐         │
                    │   │                          ├─ RRF ──┼──► best chunks
                    │   └── Chroma meaning search ─┘         │
                    │   (+ optional reranker: rerankers.py)  │
                    │                                       │
                    │   🔑 permission filter: a 'public'    │
                    │      user only ever searches PUBLIC   │
                    │      chunks — the salary chunk is      │
                    │      NEVER fetched for them.           │
                    └──────────────────┬───────────────────┘
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  AUGMENT — paste chunks into a prompt │  ← LangChain template
                    └──────────────────┬───────────────────┘
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  GENERATE — AI writes the answer      │  ← gpt-4o-mini
                    └──────────────────┬───────────────────┘
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  GUARD 2 — OUTPUT (Guardrails AI)     │
                    │  • does the answer leak staff pay?    │
                    │       (ONLY checked if 'public')      │
                    │  • if leak → REASK: rewrite without it│
                    └──────────────────┬───────────────────┘
                                       ▼
                              Answer shown to you
```
To answer sooner, GUARD 1 (and the prompt-injection check) actually runs at the
same time as RETRIEVE. GENERATE waits for it, and nothing retrieved is shown or
put in a prompt until it passes — a blocked question gets the same refusal.
Each question keeps its own state, so people asking at once don't wait for
each other.

**One answer, cited.** The search hands the model numbered passages (S1, S2, …,
each its own spotlighted block), and ONE model call writes the answer, putting
`[S1]` after what it took from each. The server keeps only markers that point at a
passage read for this question that the asker may see, renumbers them in order of
first use, and lists those passages under the answer, where each opens at the
cited span (`GET /api/passages/{chunk_id}`, checked against the role every time).
Every chunk carries what that needs: `chunk_id`, `doc_id`, title, provider, link,
access, `modified`, `char_start`/`char_end`, PDF page and heading path (ingest.py).

---

## 🔐 The two safety layers (don't mix them up)

```
LAYER 1 — PERMISSION FILTER   "the wall"
   Decides WHICH documents you can search, based on your role.
   Public user can't even look in the salary file.
   → The model can't leak what it never received.

LAYER 2 — GUARDRAILS          "the backup net"
   • Generic checks (too long / toxic)  → apply to EVERYONE
   • Salary checks (input + output)      → apply to PUBLIC only (HR skips them)
   • If a leak slips through             → REASK rewrites it cleanly
```

The question is screened for prompt injection, but documents can carry planted
instructions too (anyone who can edit a synced doc or wiki page). `untrusted.py`
handles that, for every role:
- **Spotlighting.** Every chunk and tool result reaches a model inside an
  `<untrusted_data source="...">` block, and the system prompts say text in a block
  is data, never instructions. Text that imitates the delimiter is defused first.
- **Egress.** Wikipedia and currency calls whose arguments quote 40+ letters/digits
  of text retrieved for the same question are refused. `ALLOW_WIKIPEDIA` /
  `ALLOW_CURRENCY` (on by default) remove those tools altogether.
- **Links.** Links in an answer that appear in neither the sources read nor the
  question become plain text (and are logged). The chat view never loads images.
- **Red team.** `eval/redteam.py` plants such instructions (`eval/corpus_redteam/`);
  `tests/test_injection.py` runs it with a model that obeys them, and
  `python evaluate.py --redteam` with the real one.

---

## 🔑 Where your role comes from

```
  Sign in ──► password account ──► allowed unless the workspace requires single sign-on
          │                        (admins always keep theirs)
          └─► single sign-on: Google / Microsoft, or the workspace's own Okta / Entra /
              SAML provider ──► sso.py checks the database (members_store.py):
                                 • is your email's domain verified by this workspace?
                                   no → turned away
                                 • your role, in this order:
                                     access turned off           → no access
                                     a role an admin set          → that role
                                     your groups at the provider  → the highest rule
                                     otherwise                    → public
```
`admin` is HR plus control over who may sign in (the **Access** screen); the
app treats both as HR / Admin. Groups are read at each sign-in through the
workspace's **role source** (the one provider with group rules); signing in
through another provider leaves the role alone. Admin from groups the role
source hasn't sent in 30 days (configurable) expires, except for the last admin.
Your role is looked up again on **every request**, so taking someone's HR role
away works straight away. The website can *lower* it (an HR user can "view as
public"), but it can never *raise* it — the server decides.

---

## 👤 Same question, two roles (the whole point)

```
"What is the teacher salary?"

  PUBLIC user                         HR / ADMIN user
  ───────────                         ───────────────
  input guard: about pay? YES         input guard: SKIPPED (authorized)
        → BLOCKED 🚫                   retrieve: searches BOTH rooms
                                       finds salary chunk
                                       AI answers "60,000 rupees" ✅
```

---

## 🗺️ Which roadmap phase each part covers

| Part of the app | Roadmap phase |
|---|---|
| Embeddings + Chroma | Phase 2 — Embeddings & Vector DB |
| Chunking, retrieve, BM25, RRF, hybrid | Phase 3 — RAG |
| LangChain loaders / splitters / prompts | Phase 4 — Frameworks |
| Guardrails + permission filtering | Phase 6 — Production safety |
| Sign-in (SSO) + account roles | Phase 6 — Production safety |

---

## 🗣️ Say this out loud and you've explained the whole app
> "Documents are split into chunks, turned into meaning-numbers by an embedding
> model, and stored in Chroma with a tag for who can see them. When you ask a
> question, a guard checks it, then the app searches **only the chunks you're
> allowed to see** — using both keyword and meaning search, merged with RRF. The
> best chunks go to gpt-4o-mini, which writes the answer. A final guard checks the
> answer for leaks and rewrites it if needed. That whole pattern is **secure RAG**."

---

## 🔁 Re-indexing and background jobs

Work that shouldn't hold up a request runs as a **job**: a row in the `jobs`
table that a pool of worker threads claims, runs and marks done (`kb/jobs.py`).
Re-indexing a workspace, syncing a connector, and the periodic connector sync,
SAML metadata refresh and clean-up are all jobs. A claim is a lease in the
database, so a job survives a restart (another worker takes it over when the
lease runs out), a failed job is retried with a growing wait, and each lane runs
one job at a time: one index run per workspace, while workspaces take turns
and never wait on each other. The workers run inside the API, or as
`python -m kb.worker` (deploy.md, "Background jobs").

```
  upload / edit / delete ──► index job (that document)     ┐
  connector sync changed ──► index job (those documents)   ├─► ingest.update: only what changed
  "Re-index"             ──► index job                     ┘     is embedded; deleted files'
  access level changed   ──► retag job ─► chunks' access tag      chunks are removed
                                          updated, no embedding
  "Rebuild everything",  ──► index job ─► ingest.main into a NEW collection, then the
  new chunking settings                   workspace switches to it (search_indexes)
```

Every chunk has a **stable id** from (workspace, document, chunk number, chunk
text), and carries its document's key, a hash of its content and its access
level, so a run can tell what's new, changed or gone without embedding anything
it already has. Questions are answered throughout: an update changes the index
a document at a time, and a full rebuild builds beside the collection in use,
which answers until the switch (a failed rebuild leaves it in place). Each
workspace's row in `search_indexes` names its collection and a version that
changes with every update; agents reload when it does (`kb/services/agents.py`).

---

## 📊 Question log and insights

Every question is logged in `question_log` (`kb/services/question_log.py`): the
chat router wraps the agent once (`question_log.tracked(...)`), so web, API and
later Slack or MCP questions are all logged, saved in a conversation or not. A row
holds the outcome (answered, not found, blocked + why, general knowledge, error,
stopped), the tools and sources used, the highest access level the model read,
the latency, and the tokens and model of every model call (a LangChain callback
set for the run). Its **text** follows the workspace's setting, applied before it
is written: `redacted` (the default: `safety.redact_pii`), `full`, or `metadata`
(none). Text is cleared after `question_log_days` (90 by default); deleting a chat
clears what was logged of its words.

```
  question ──► agent ──► question_log ──nightly──► insights_daily, source_stats
                                     └──weekly───► content_gaps (embed, cluster, name)
  documents (uploads + synced) ──────────────────► insights_docs (never cited, stale)
```

The admin Insights screen (`/admin/insights`, HR and admins) reads
`GET /api/admin/insights` (`kb/services/insights.py`): rolled-up days plus today
worked out live. It shows aggregates only: who asked is never shown (chat.md
D3), sample questions follow the retention mode and leave out answers the
viewer's role can't see. Its Feedback tab lists thumbs-down answers and promotes
one to `eval_candidates`, a test-set candidate for `eval/golden`.

---

## 💾 Chat history and sharing

A chat is a tree of messages (`messages.parent_id`); the one shown is the path down
to `conversations.active_leaf_id`. Editing a question or writing an answer again
adds a **sibling** and moves the leaf there, so the old version stays one click
away ("‹ 1 / 2 ›"). "Branch in new chat" copies the path into a new conversation.

One rule covers every way an answer can be read again: `visible_to(role, message)`
(`kb/services/conversations.py`) compares the reader's role **now** with the
highest access level of everything the model read for that answer. It applies to
the owner's own chat (after a demotion, or "view as public"), to search, to Copy,
and to share links:

- **Search** (`conversation_search`, SQLite FTS5) keeps each row's access level,
  and the query only matches rows the searcher's role may read, so a withheld
  answer's words can never find its chat.
- **Share links** (`kb/services/shares.py`) store only the token's sha256, work
  for the same workspace only, expire (never after retention) and can be revoked.
  Each answer is checked against the **lowest** of the viewer's role, the sharer's
  role now and the role it was shared as; one that fails is withheld whole, not
  just its sources (chat.md D1), with "Ask it yourself".
- **Retention**: `chat_settings.retention_days` per workspace (default a year,
  D4); the nightly `chat_retention` job deletes older chats through the normal
  delete, so feedback, links, search rows and the question log's text go too.
  Answers promoted to `eval_candidates` are copies and stay (D2).

---

## 🔌 Public API, API keys and the MCP server

Every surface asks through one service, `kb/services/answering.ask()`: the web
chat, the public API and the MCP server get the same agent, guards, `[S#]`
citations, `max_access` and question log (with `surface` web / api / mcp / slack).

```
  browser session ──► /api/*   (the app's own API: changes with the site)
  API key ──────────► /api/v1  (kb/routers/api_v1.py: the stable, public one)
  personal API key ─► /mcp     (kb/mcp_server.py: MCP, Streamable HTTP, stateless)
                         │
                         └──► kb/api_auth.py ──► answering.ask() / retrieval.search()
```

- **Keys** (`api_keys_store.py`, table `api_keys`): `kb_live_<prefix>_<secret>`,
  shown once, kept as a SHA-256 hash. A **personal** key acts as the member who
  made it, with their role looked up on every call (`kb/api_auth.py`), so
  demoting, turning off or removing them limits the key at once. A **service**
  key (admins only) acts as a public user, never HR, and stops when its admin
  is no longer one. Scopes: `ask`, `search`, `docs:read`, `docs:write` (HR /
  Admin), `audit:read` (admins). Keys last 7, 30, 90 (default) or 365 days.
- **Settings** (`api_settings`): the API and the MCP server on or off per
  workspace, and which MCP tools agents get (`read_document` is off by default:
  it returns whole documents). Admins set them on Access → API & MCP; everyone
  manages their own keys on the API keys page (account menu).
- **Limits and audit**: every key call is rate-limited per key and per workspace
  (`RATE_LIMIT_API_KEY`, 60 / minute; `RATE_LIMIT_API_WORKSPACE`, 600), a
  question also counts as one of its person's chat questions, and every call is
  audited (`actor_type` api_key, `details.surface` api / mcp, the key's prefix;
  a question by its length and outcome, never its text).
- **OpenAPI**: `/api/v1/openapi.json` describes `/api/v1` only and is always on;
  FastAPI's own `/docs` (the whole internal API) stays off in production.
- **MCP auth** is a personal key as a bearer token, for headless clients and
  the configs the API keys page shows (Claude Code, Claude Desktop through
  `mcp-remote`, Cursor). OAuth 2.1 sign-in for MCP clients (the MCP
  authorization spec, federating to this app's sign-in) is a follow-up.

---

## 💬 The Slack app

People ask in Slack through the same `answering.ask()` (surface `slack`):
`kb/routers/slack.py` takes Slack's requests, `kb/services/slack.py` answers them,
`slack_store.py` keeps the installs (bot token sealed by `secret_box`).

```
  Slack ──signed request──► /api/slack/events | commands | interactions
            (signature + 5-minute window checked first; event ids deduped)
                    │  queue a "slack" job, reply within Slack's 3 s
                    ▼
  job: users.info email ──► the member (role looked up now; refused if off / unknown)
       where was it asked?
         the asker's own DM with the app ──► ask() as their role
         anywhere else (channel, thread, /ask, shortcut) ──► ask() as PUBLIC
              └─ a search as their role finds restricted passages?
                   ──► ephemeral "I've sent you a DM" + ask() as their role in the DM
       ──► Block Kit: answer, [n] links to the source (or /passages/<chunk>), 👍 / 👎
```

A Slack team is installed in one workspace. The app is ours (`SLACK_*` in
`.env`, one set of request URLs) or the customer's own internal app (its
secrets pasted on the Integrations card, sealed; its request URLs carry a key,
so each request is checked with its own signing secret). Answers are saved as
the asker's conversations (a Slack thread continues one), so 👍 / 👎 land in
`message_feedback` and Insights like the web chat's. deploy.md, "Slack app",
has the scopes, the Marketplace and rate-limit rules, and a by-hand checklist.

---

## 🗄️ Database migrations

Accounts, sign-in providers, sessions and integrations live in one SQLite file,
`config/app.db` (`APP_DB`). `db.py` is the only place it's found and opened
(WAL mode, a 5 s busy timeout, `foreign_keys = ON`, `sqlite3.Row` rows); the
stores use plain `sqlite3`. Its schema is kept by **Alembic** migrations in
`migrations/versions/`, numbered `0001`, `0002`, ….

**How a database gets upgraded.** The first `db.connect()` in a process runs
every migration the file doesn't have yet, in one transaction (`BEGIN IMMEDIATE`:
all or nothing, and a second worker starting at the same moment waits, then finds
nothing to do). A database made before Alembic (app tables, no `alembic_version`)
is first brought to the baseline by `migrations/legacy.py`, the old stores' own
add-a-column and data-fix steps, frozen, then marked as at `0001`. Nothing to do
by hand on deploy: the new image upgrades `config/app.db` on its first request.
`alembic upgrade head` (in the repo root, or `docker compose exec knowledgebase
alembic upgrade head`) does the same on demand; `alembic current` shows where a
database is. Back up first (`scripts/backup.sh`): there's no automatic way down.

**Adding a migration.**

1. `alembic revision -m "add members.locale" --rev-id 0002` (next number) makes
   `migrations/versions/0002_add_members_locale.py`.
2. Write `upgrade()` with `op.add_column`, `op.create_table`, `op.create_index`,
   or raw SQL through `op.get_bind().exec_driver_sql(...)`. To change a column or
   a CHECK, which SQLite can't do in place, use `with op.batch_alter_table("t") as b:`
   (it copies the table; foreign keys are off while migrating, so the copy doesn't
   cascade-delete anything, and the upgrade is undone if it leaves a dangling
   reference). Fill new columns from old data in the same migration.
3. Never edit a migration that has been merged, and never edit
   `migrations/legacy.py` or `0001_baseline.py`: databases already ran them.
4. Keep it **expand / contract**: add the new column or table and start writing
   it; switch reads in a later release; drop the old one later still. The previous
   release must keep working on the upgraded schema, so a rollback is just
   deploying the old image.
5. Test it: `tests/test_migrations.py` upgrades an empty database and every
   pre-Alembic fixture in `tests/fixtures/legacy_dbs/` to the newest migration and
   checks every row survives and the schema matches a new database's. Add a test
   for your data change there.

---

## 📊 Mermaid diagrams (render in GitHub / VS Code preview)

> To view: open this file's **Markdown Preview** in VS Code (Ctrl+Shift+V).
> If diagrams don't render, install the "Markdown Preview Mermaid Support" extension.

### Diagram 1 — Ingest (run once)

```mermaid
flowchart TD
    D1["docs/public/handbook.txt"] --> L["LangChain loads the files"]
    D2["docs/restricted/staff_salaries.txt"] --> L
    L --> SP["Split into small chunks"]
    SP --> TAG["Tag each chunk: public or restricted"]
    TAG --> EMB["OpenAI embeddings: text to numbers"]
    EMB --> STORE["Store numbers + tags in Chroma"]
```

### Diagram 2 — Asking a question (every time)

```mermaid
flowchart TD
    U["User types a question"] --> ROLE{"Account's role"}
    ROLE -->|Public| INP{"Input Guard (Guardrails AI)"}
    ROLE -->|HR / Admin| INP
    INP -->|too long or toxic| B1["Blocked (everyone)"]
    INP -->|staff-salary question AND public| B2["Blocked (not authorized)"]
    INP -->|ok| FILT["Permission filter:<br/>keep only chunks this role may see"]
    FILT --> BM25["BM25 keyword search"]
    FILT --> CHROMA["Chroma meaning search"]
    BM25 --> RRF["RRF: merge both rankings"]
    CHROMA --> RRF
    RRF --> AUG["Augment: paste chunks into the prompt"]
    AUG --> GEN["gpt-4o-mini writes the answer"]
    GEN --> OUTG{"Output Guard (Guardrails AI)"}
    OUTG -->|clean| SHOW["Show answer to user"]
    OUTG -->|leaks staff pay AND public| REASK["Reask: rewrite without the pay"]
    REASK --> SHOW
```

### Diagram 3 — The two safety layers (zoomed in)

```mermaid
flowchart LR
    Q["Question"] --> P{"Permission filter<br/>(by role)"}
    P -->|public| PUB["Search PUBLIC chunks only"]
    P -->|hr| ALL["Search ALL chunks"]
    PUB --> M["Model writes answer"]
    ALL --> M
    M --> OG{"Output guard"}
    OG -->|public + leak| RW["Reask / refuse"]
    OG -->|hr or clean| OUT["Show answer"]
    RW --> OUT
```

