# Internal Knowledge Assistant (RAG Agent): Cost & Effort Estimate

Assumed model: OpenAI gpt-4o-mini · Hosting: a cloud server (VPS) · As of Oct 2026

---

## 1. How much will it cost to run? (monthly)

Each question costs about **$0.0007** in OpenAI fees. The monthly bill depends on how many people use it and how often.

### Usage levels (questions per user per day)

| Usage | Questions / user / day | OpenAI cost per user per month |
|---|---|---|
| Light | 10–15 | ~$0.15–0.25 |
| Medium | 20–50 | ~$0.30–0.80 |
| Heavy | 50–100 | ~$0.80–1.55 |

### Monthly total (OpenAI + server + embeddings)

| Users | Light (10–15/day) | Medium (20–50/day) | Heavy (50–100/day) |
|---|---|---|---|
| 25 | ~$10–30 | ~$15–40 | ~$25–60 |
| 100 | ~$20–45 | ~$35–100 | ~$85–175 |
| 500 | ~$100–160 | ~$180–430 | ~$410–815 |

### What makes up the total

| Item | Monthly |
|---|---|
| OpenAI answers | $0.0007 × questions (22 working days a month) |
| OpenAI embeddings (nightly re-index of the docs) | ~$1–3 |
| Server (VPS) | ~$5–20 up to 100 users; ~$20–40 for 500 users |
| Source APIs (Notion, Google Drive, Confluence...) | $0 |

---

## 2. How much effort to build it?

| Version | What you get | Effort |
|---|---|---|
| MVP | 1 source, chat web app, Google/Microsoft login, simple roles (e.g. public / HR), basic safety checks | **7–10 developer-weeks** (~2–2.5 months) |
| Production | 3 sources, permissions copied from each source, multi-step agent, Slack bot, logging, backups | **15–25 developer-weeks** (~4–6 months) |

Build cost = developer-weeks × the developer's weekly rate.

### Effort breakdown

| Work item | MVP | Production |
|---|---|---|
| Core RAG pipeline: load docs, split, embed, vector DB, search (keyword + meaning), rerank, answers with sources | 1.5–2 | 2–2.5 |
| Source connectors (sync, updates, deletes) | 1–2 (1 source) | 3–5 (3 sources) |
| Chat web app | 0.5–1 | 0.5–1 |
| Login (Google / Microsoft SSO) | 0.5–1 | 0.5–1 |
| Permissions | 0.5 (fixed roles) | 2–3 (follow each source's sharing) |
| Safety checks (prompt injection, sensitive data leaks) | 0.5–1 | 1–1.5 |
| Agent that can use tools and answer multi-step questions | – | 1–2 |
| Testing answer quality on 50–100 real questions + tuning | 1 | 1.5–2 |
| Slack / Teams bot | – | 0.5–1.5 |
| Logging, cost tracking, thumbs-up/down feedback | – | 0.5–1 |
| Deployment (Docker, HTTPS, backups, secrets) | 0.5 | 1–1.5 |
| Bigger database for large doc bases (see section 4) | – | 0–2 (only if 10k+ pages) |
| QA, user testing, handover docs | 0.5–1 | 1–2 |
| **Total (developer-weeks)** | **~7–10** | **~15–25** |

---

## 3. What will change the cost?

| If this goes up... | ...this happens |
|---|---|
| More users or more questions | OpenAI bill grows in direct proportion |
| A bigger model (e.g. gpt-4o instead of gpt-4o-mini) | 15–20× more per question |
| Data must stay on our own servers | We would need our own GPU server, which costs hundreds to thousands of dollars a month |
| More sources (Notion, Drive, Slack...) | +0.5–1.5 developer-weeks per source |
| Strict permissions (each person sees only what they can see in the source) | +2–3 developer-weeks |
| Instant updates instead of nightly | +about 1 developer-week per source |
| Scanned PDFs or images | Need OCR, which adds cost and effort |
| Very large document base (10k+ pages) | Bigger server, plus a database change (+1–2 developer-weeks) |
| Slack / Teams bot instead of a web app | +0.5–1.5 developer-weeks |

---

## 4. How big can the database be?

| Docs size | Pages | Database size | Server RAM | Change needed? |
|---|---|---|---|---|
| Small | up to 1,000 | ~100 MB | 2 GB | No |
| Medium | 1,000–10,000 | 0.1–1 GB | 4–8 GB | No |
| Large | 10,000–50,000 | 1–4 GB | 8–16 GB | Yes: move to pgvector or Qdrant (+1–2 developer-weeks) |
| Very large | 50,000+ | 4 GB+ | 16 GB+ or a managed service | Yes: a managed vector DB (get a quote) |

Turning the docs into searchable vectors costs very little: about $0.15 for 10,000 pages.

---

## 5. What can we use as a source?

| Source | Effort | Notes |
|---|---|---|
| PDF / Word / TXT files | 0.5–1 week | Scanned PDFs need OCR |
| Wiki tools (Outline, etc.) | ~1 week | Most have a search and export API |
| Google Docs / Drive | 1–1.5 weeks | Easy: the API shows who can see each file |
| Notion | 1–1.5 weeks | Permissions are hard: the API doesn't show who can see a page |
| Confluence | 1–1.5 weeks | Easy: permissions are in the API |
| SharePoint / OneDrive | 1.5–2 weeks | Needs Microsoft admin approval |
| Slack | 1–2 weeks | Messages are informal and noisy; API limits are strict |
| Jira / GitHub / Zendesk | ~1 week each | Good for support and engineering questions |
| Gmail | Not recommended | Privacy risk |

---

## 6. Questions to finalise the numbers

1. Which sources do we need first, and how many pages are in each?
2. How many people will use it, and how many questions a day?
3. Is there private data (HR, salaries, finance) that only some people should see?
4. Should answers update nightly, or instantly?
5. Where will people ask questions: a web app, Slack, or Teams?
6. Is it OK to send company data to OpenAI, or must it stay on our own servers?
7. What is the developer's weekly rate?

---

*Prices are approximate (OpenAI list prices; VPS prices vary by provider). Check them before quoting a client.*
