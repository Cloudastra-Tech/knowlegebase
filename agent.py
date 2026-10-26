"""
agent.py  —  PHASE 6: the AGENT, now built on LangGraph.

A plain RAG app always does the same steps. An AGENT is given TOOLS and decides
— on its own — which to call, in what order, and chains them for multi-step
questions. Here it has 4 tools:

   search_docs       -> your company documents (the existing RAG)
   calculator        -> arithmetic
   convert_currency  -> live exchange rates (Frankfurter API, free, no key)
   wikipedia         -> general-knowledge summaries (free, no key)

WHAT CHANGED IN THIS PHASE
--------------------------
Before, the "agent loop" (think -> call tool -> feed result -> repeat) was a
hand-written `for` loop. Now that SAME loop is a LangGraph *state graph*:

       START -> [agent] --has tool calls?--> [tools] --+
                   ^                                    |
                   +------------------------------------+
                   |
                   +--no tool calls--> END

   * [agent]  node = ask the LLM what to do next
   * [tools]  node = run whichever tool(s) the LLM asked for (ToolNode)
   * the arrow that decides "tools or finish?" = tools_condition

The TOOLS and the SYSTEM prompt below are UNCHANGED — only the loop machinery
was swapped. (Memory/checkpointer is NOT added yet — that's the next step.)

Run a quick demo:  python agent.py
"""

import ast
import operator
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition

from rag import load_chain, answer_question
from safety import check_prompt_injection, redact_pii


# ---- Calculator: arithmetic only --------------------------------------------
# eval() — even with no builtins — can be escaped (e.g. via ().__class__), and the
# expression comes from the model, which a user's question can steer. So we walk
# the parsed expression ourselves and allow numbers and + - * / // % ** only.
_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
              ast.Mod: operator.mod, ast.Pow: operator.pow,
              ast.UAdd: operator.pos, ast.USub: operator.neg}
MAX_EXPRESSION = 200        # characters
MAX_EXPONENT = 100          # 10**100 is fine; 9**9**9 would hang the server


def calculate(expression):
    """The value of an arithmetic expression like '2000*12'. Raises ValueError
    for anything that isn't plain arithmetic."""
    if len(expression) > MAX_EXPRESSION:
        raise ValueError("expression is too long")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](walk(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
                raise ValueError("exponent is too large")
            return _OPERATORS[type(node.op)](left, right)
        raise ValueError("only numbers and + - * / // % ** are allowed")

    try:
        tree = ast.parse(expression.replace(",", ""), mode="eval")   # "2,000" -> 2000
    except SyntaxError:
        raise ValueError("not a valid arithmetic expression")
    return walk(tree)


def _safe(text):
    """Make text printable on a Windows cp1252 console (emoji-safe)."""
    enc = sys.stdout.encoding or "utf-8"
    return str(text).encode(enc, "replace").decode(enc, "replace")


def _outline_collections():
    """OUTLINE_COLLECTIONS: comma-separated Outline collection ids the assistant
    may search and read. Empty = every collection the server's token can see."""
    return [c.strip() for c in os.getenv("OUTLINE_COLLECTIONS", "").split(",") if c.strip()]


def _outline_post(endpoint, payload, timeout=15):
    """Shared helper: POST to the Outline API, handling auth + Cloudflare Access.
    Returns (json, error_string). Used by both Outline tools below.
    With OUTLINE_COLLECTIONS set, searches run once per allowed collection (Outline's
    `collectionId` filter, which older self-hosted servers also know) and documents
    are also checked here, so nothing outside those collections gets through."""
    allowed = _outline_collections()
    if not allowed or endpoint not in ("documents.search", "documents.info"):
        return _outline_request(endpoint, payload, timeout)
    if endpoint == "documents.info":
        data, err = _outline_request(endpoint, payload, timeout)
        if not err and ((data or {}).get("data") or {}).get("collectionId") not in allowed:
            return None, "Error: that document isn't in a wiki collection this assistant may read."
        return data, err
    results = []
    for collection_id in allowed:
        data, err = _outline_request(endpoint, {**payload, "collectionId": collection_id},
                                     timeout)
        if err:
            return None, err
        results += [r for r in (data or {}).get("data", [])
                    if (r.get("document") or {}).get("collectionId") in allowed]
    results.sort(key=lambda r: -(r.get("ranking") or 0))
    return {"data": results[:payload.get("limit") or len(results)]}, None


def _outline_request(endpoint, payload, timeout=15):
    """One POST to the Outline API (see _outline_post)."""
    base = os.getenv("OUTLINE_URL", "https://app.cloudastra.co").rstrip("/")
    token = os.getenv("OUTLINE_API_TOKEN")
    if not token:
        return None, "Error: OUTLINE_API_TOKEN is not set in the .env file."

    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    # The wiki sits behind Cloudflare Access (Zero Trust). Send the service-token
    # headers if configured so we get past the gate before Outline sees us.
    cf_id = os.getenv("CF_ACCESS_CLIENT_ID")
    cf_secret = os.getenv("CF_ACCESS_CLIENT_SECRET")
    if cf_id and cf_secret:
        headers["CF-Access-Client-Id"] = cf_id
        headers["CF-Access-Client-Secret"] = cf_secret

    resp = requests.post(f"{base}/api/{endpoint}", headers=headers,
                         json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json(), None


def _outline_doc_url(doc):
    """Full browser link to an Outline document (the API returns a relative path)."""
    path = doc.get("url") or ""
    if not path:
        return None
    base = os.getenv("OUTLINE_URL", "https://app.cloudastra.co").rstrip("/")
    return path if path.startswith("http") else f"{base}{path}"


_STOPWORDS = set("""a an the is are was were be been am do does did what whats what's who whom
whose which when where why how waht wat tell me about give show explain describe please
can could would should will shall may might must of in on at to for from by with and or
not no our your my their its this that these those it i we you they us any some all
info information details detail regarding""".split())


def _keywords(text):
    """Meaningful words of a question, longest first (for keyword fallbacks)."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", text or "")
    keep = [w for w in dict.fromkeys(words) if w.lower() not in _STOPWORDS]
    return sorted(keep, key=len, reverse=True)[:3]


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


_NOT_FOUND = re.compile(
    r"\b(couldn[’']?t|could not|can[’']?t|cannot|unable to|did not|didn[’']?t|wasn[’']?t able to) (find|locate)\b"
    r"|\bno (specific |relevant )?(information|details|mention|data)\b"
    r"|\bnot (mentioned|covered|available|specified|included|provided) in\b"
    r"|\b(don[’']?t|do not|does not|doesn[’']?t) (have|contain|mention|include|provide|cover|specify)"
    r" (any |specific )?(information|details|mention|data|anything)\b"
    r"|\bdon[’']?t know\b|\bdo not know\b|\bnot sure\b", re.IGNORECASE)


_SMALL_TALK = re.compile(
    r"^\s*(hi+|hello|hey|hiya|yo|thanks?|thank you|thx|ok(ay)?|cool|great|nice|bye|"
    r"good (morning|afternoon|evening|night))\b[\s!.?,]*(there|team|bot|assistant)?[\s!.?]*$",
    re.IGNORECASE)
_PURE_MATH = re.compile(
    r"^\s*(what('?s| is)|calculate|compute|solve)?\s*[-+*/×x÷^%().\d\s]+[?=\s]*$",
    re.IGNORECASE)


def needs_retrieval(question):
    """RETRIEVAL-FIRST: every question searches the company docs before the agent
    thinks — except small talk ("hi", "thanks") and pure arithmetic ("18*7")."""
    q = (question or "").strip()
    if not q:
        return False
    if _SMALL_TALK.match(q):
        return False
    if _PURE_MATH.match(q) and re.search(r"\d", q):
        return False
    return True


def _is_not_found(answer):
    """True when the answer as a whole says "I couldn't find it" — a short
    refusal, or one that opens with it. A long answer that only notes one
    missing detail ("X is not specified in the docs") still has sources."""
    m = _NOT_FOUND.search(answer or "")
    return bool(m) and (len(answer) < 280 or m.start() < 60)


def _chunks_used(answer, docs, min_overlap=0.3):
    """The retrieved chunks the answer actually draws on.
    Retrieval returns the top-k chunks even when only one is relevant, so we
    keep a chunk only if a good share of the answer's words appear in it."""
    answer_words = _words(answer)
    if not answer_words or not docs or _is_not_found(answer):
        return []                     # "I couldn't find..." cites nothing
    scored =[(len(answer_words & _words(d.page_content)) / len(answer_words), d) for d in docs]
    used = [d for score, d in scored if score >= min_overlap]
    if used:
        return used
    # Filler in the answer ("feel free to ask...") can dilute every score below
    # the bar — then cite the single best match if it shares anything real.
    best_score, best = max(scored, key=lambda sd: sd[0])
    return [best] if best_score >= 0.15 else []


def format_sources(sources):
    """Markdown 'Sources' list for the end of an answer ('' if nothing was read)."""
    if not sources:
        return ""
    lines = []
    for i, s in enumerate(sources, 1):
        name = f"[{s['title']}]({s['url']})" if s.get("url") else s["title"]
        lines.append(f"{i}. {name} — _{s['provider']}_")
    return "\n\n**Sources**\n\n" + "\n".join(lines)


# ---- Live steps (shown to the user while the agent works) --------------------
def step_label(tool, args):
    """What the UI shows while a tool runs, e.g. 'Searching company docs'."""
    args = args or {}
    if tool == "search_docs":
        return "Searching company docs"
    if tool == "search_outline":
        return f"Searching the Outline wiki for \u201c{args.get('query', '')}\u201d"
    if tool == "read_outline_doc":
        return "Reading a wiki page"
    if tool == "calculator":
        return f"Calculating {args.get('expression', '')}"
    if tool == "convert_currency":
        return (f"Converting {args.get('amount', '')} {args.get('from_currency', '')} "
                f"to {args.get('to_currency', '')}")
    if tool == "wikipedia":
        return f"Looking up \u201c{args.get('topic', '')}\u201d on Wikipedia"
    return f"Using {tool}"


def step_result(tool, output, found_docs=None):
    """A short summary of what a tool returned, e.g. 'found 2 documents'."""
    out = str(output or "")
    if out.startswith("BLOCKED BY ACCESS RULES"):
        return "blocked by access rules"
    if out.startswith("Error"):
        return "failed"
    if tool == "search_docs":
        if found_docs:
            return f"found {found_docs} relevant document{'s' if found_docs != 1 else ''}"
        return "nothing relevant found"
    if tool == "search_outline":
        n = sum(1 for line in out.splitlines() if line.startswith("- "))
        return f"{n} matching page{'s' if n != 1 else ''}" if n else "no matching pages"
    if tool == "read_outline_doc":
        first = out.splitlines()[0] if out else ""
        return first[2:].strip() if first.startswith("# ") else "read"
    if tool in ("calculator", "convert_currency"):
        return out[:80]
    if tool == "wikipedia":
        return "summary found" if out and "No summary" not in out else "nothing found"
    return "done"


def build_agent(role="public", collection=None, outline=True):
    """Create an agent for a given user role. Returns a run(question) function.
    collection: the workspace's search index (None = default workspace).
    outline: whether this workspace has the Outline wiki connected."""
    retriever, rag_llm = load_chain(role, collection)

    # CITATIONS: every tool that reads a document records it here. run() clears
    # it per question and appends a "Sources" list built from it — so citations
    # come from what was ACTUALLY read, not from what the model claims it read.
    cited = []
    # GUARD refusals: if a tool's safety check blocks the question (e.g. a public
    # user asking for staff salary), we return that exact message to the user,
    # instead of letting the agent keep searching and reword it as "not found".
    blocked = []
    # LIVE STEPS: run(..., on_event=fn) sets this so nodes can report progress.
    emit_holder = [None]

    def emit(event):
        if emit_holder[0]:
            emit_holder[0](event)

    def cite(title, provider, url=None):
        entry = {"title": title, "provider": provider, "url": url}
        if entry not in cited:
            cited.append(entry)

    # ---- The TOOLS the agent can choose from --------------------------------
    @tool
    def search_docs(question: str) -> str:
        """Search the company's documents: files uploaded on the Documents screen
        and files synced from connected tools such as Google Drive / Google Docs:
        policies (leave, refund, HR), handbooks, pricing, support hours, stipend,
        FAQs, PDFs and any other files added there. Try this FIRST for any
        question about the company, its policies or its products."""
        answer, docs = answer_question(question, retriever, rag_llm, role)
        if docs is None:                 # a guard refused before any search ran
            blocked.append(answer)
            return (f"BLOCKED BY ACCESS RULES: {answer} Do not search any other tool "
                    "for this; just tell the user this message.")
        for d in _chunks_used(answer, docs):
            source = d.metadata.get("source", "")
            if d.metadata.get("title"):          # synced from a connector (e.g. Drive)
                cite(d.metadata["title"], d.metadata.get("provider") or "Connected source",
                     d.metadata.get("url") or None)
            elif source:
                cite(os.path.basename(source), "Company documents")
        return answer

    @tool
    def calculator(expression: str) -> str:
        """Do arithmetic. Pass a math expression, e.g. '2000*12' or '24000*0.8'."""
        try:
            return str(calculate(expression))
        except Exception as e:
            return f"Error: {e}"

    @tool
    def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
        """Convert money between currencies with LIVE rates.
        e.g. amount=24000, from_currency='INR', to_currency='USD'."""
        try:
            url = (f"https://api.frankfurter.app/latest?amount={amount}"
                   f"&from={from_currency}&to={to_currency}")
            data = requests.get(url, timeout=10).json()
            value = data["rates"][to_currency]
            return f"{amount} {from_currency} = {value} {to_currency}"
        except Exception as e:
            return f"Error: {e}"

    @tool
    def wikipedia(topic: str) -> str:
        """Get a short summary of a PUBLIC general-knowledge topic from Wikipedia.
        LAST RESORT: use only after search_docs found nothing. Never use it for the
        company's own products, projects, clients, people or documents."""
        try:
            t = topic.strip().replace(" ", "_")
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}"
            # Wikipedia REQUIRES a User-Agent header, or it rejects the request.
            headers = {"User-Agent": "cloudastra-learning-app/1.0 (student project)"}
            data = requests.get(url, headers=headers, timeout=10).json()
            return data.get("extract", "No summary found.")
        except Exception as e:
            return f"Error: {e}"

    @tool
    def search_outline(query: str) -> str:
        """Search Cloudastra's internal knowledge base / wiki. This is the
        company's internal documentation tool (Outline, self-hosted at
        app.cloudastra.co). USE THIS whenever the user mentions any of:
        Outline, the wiki, the knowledge base, "internal docs",
        "internal documentation", "our docs", "company docs", app.cloudastra,
        app.cloudastra.co, runbooks, project notes, how-tos, meeting notes, or
        engineering knowledge. (People may call it "the wiki" or "internal docs"
        even though it is Outline — treat all of those as THIS tool.)
        Returns the top matches as short SNIPPETS, each with a document id. To
        read the FULL content of the best match, follow up with read_outline_doc
        using that id. Use for INTERNAL wiki knowledge; for uploaded company
        documents use search_docs, and for general world knowledge use wikipedia."""
        try:
            data, err = _outline_post("documents.search",
                                      {"query": query, "limit": 5})
            if err:
                return err
            results = (data or {}).get("data", [])

            # Outline only returns docs containing ALL the words, so "Project X
            # tech stack" can miss the doc about Project X. With few hits, search
            # again with just the name in the query (its capitalised words).
            # Typos ("waht is deviq") and question words also break the
            # all-words match, so then try each keyword on its own.
            name = " ".join(w for w in query.split() if w[:1].isupper())
            fallbacks = [name] if name and name != query else []
            keywords = _keywords(query)
            fallbacks += [w for w in keywords if w.lower() != query.lower()]
            seen = {r.get("document", {}).get("id") for r in results}
            for q in fallbacks:
                if len(results) >= 3:
                    break
                more, _ = _outline_post("documents.search", {"query": q, "limit": 5})
                for r in (more or {}).get("data", []):
                    # A loose keyword match counts only if the keyword is in the
                    # page TITLE ("deviq" -> "DevIQ: Product Guide"), not just
                    # somewhere in the text ("navigator" in a code snippet).
                    # With several keywords, the title must share at least two
                    # ("bible navigator" doesn't match "Lead Generation Bible").
                    title_words = _words(r.get("document", {}).get("title", ""))
                    shared = {k.lower() for k in keywords} & title_words
                    if len(shared) < min(2, len(keywords)) or not (_words(q) & title_words):
                        continue
                    if r.get("document", {}).get("id") not in seen:
                        seen.add(r.get("document", {}).get("id"))
                        results.append(r)

            if not results:
                return "No matching documents found in the Outline wiki."

            # Outline ranks by body text, so a long doc that merely MENTIONS the
            # topic can outrank the doc ABOUT it. Put title matches first.
            query_words = _words(query)
            results.sort(key=lambda r: -len(query_words & _words(
                r.get("document", {}).get("title", ""))))

            lines = []
            for r in results:
                doc = r.get("document", {})
                title = doc.get("title", "Untitled")
                doc_id = doc.get("id", "")
                snippet = (r.get("context") or "").strip().replace("\n", " ")
                lines.append(f"- {title} (id: {doc_id}): {snippet}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @tool
    def read_outline_doc(document_id: str) -> str:
        """Read the FULL text of one Outline wiki document, given its id.
        First call search_outline to find the right document and copy its id,
        then call this to read the whole document before answering a detailed
        question. The id looks like a long code from search_outline's output."""
        try:
            data, err = _outline_post("documents.info", {"id": document_id})
            if err:
                return err
            doc = (data or {}).get("data", {})
            title = doc.get("title", "Untitled")
            text = (doc.get("text") or "").strip()
            if not text:
                return f"'{title}' has no readable text."
            cite(title, "Outline wiki", _outline_doc_url(doc))
            # Cap very long docs so we don't blow up the token budget.
            if len(text) > 8000:
                text = text[:8000] + "\n\n...(document truncated)"
            return f"# {title}\n\n{text}"
        except Exception as e:
            return f"Error: {e}"

    tools = [search_docs, calculator, convert_currency, wikipedia]
    if outline:                       # only workspaces with the wiki connected
        tools += [search_outline, read_outline_doc]

    # The model is told about the tools; it decides when to call them.
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0).bind_tools(tools)

    SYSTEM = SystemMessage(content=(
        "You are the company's internal knowledge assistant. You solve multi-step "
        "questions by CHAINING tools.\n"
        "- ALL company sources have ALREADY been searched for the user's question: "
        "search_docs (uploads + Google Drive + Notion) and, when available, "
        "search_outline (the wiki) — see their results at the start of the "
        "conversation. Answer from them when they cover the question. If the wiki "
        "search lists relevant pages, call read_outline_doc on the best match "
        "before answering. Do NOT repeat a search with the same query; only with a "
        "clearly different query (other words, a sub-question) when needed. Use "
        "wikipedia ONLY if every company source found nothing.\n"
        "- RULE #1: for ANY question about a product, project, client, person, "
        "document, process, policy, tool or name, call search_docs FIRST — even if "
        "it sounds like general knowledge (e.g. 'Bible Navigator', 'DevIQ', 'the "
        "audit'). search_docs covers uploaded files AND everything synced from "
        "Google Drive / Google Docs / Sheets AND Notion. If it finds nothing, try "
        "search_outline (when available). Only after those find nothing may you "
        "use wikipedia, and only for genuinely public world knowledge.\n"
        "- For company policies, handbooks, prices, refund, support, stipend and HR "
        "rules: search_docs.\n"
        "- 'Notion', 'Google Drive', 'Drive', 'Docs' or 'Sheets' -> search_docs (they "
        "are synced into it). The Outline wiki is at app.cloudastra.co; people "
        "also call it 'the wiki', 'internal docs' or 'our docs' -> search_outline.\n"
        "- For ANY question about INTERNAL company/team knowledge (runbooks, "
        "project notes, how-tos, engineering docs, meeting notes), ALWAYS follow "
        "this 3-step flow:\n"
        "    1. search_outline(query) to find which documents are relevant.\n"
        "    2. Choose the match whose TITLE best fits the SUBJECT of the question "
        "(e.g. for 'tech stack of the AI Delivery Intelligence Platform', open the "
        "doc titled 'AI Delivery Intelligence Platform'), and call "
        "read_outline_doc(id) to read its FULL content.\n"
        "    3. If that document does NOT contain the answer, read the NEXT most "
        "relevant match before concluding. Try up to 3 docs. Only say the wiki "
        "doesn't cover it after genuinely reading the best candidates.\n"
        "    4. Answer ONLY the specific thing that was asked — concise and "
        "precise, using the exact details (names, numbers, terms) from the doc. "
        "Do NOT dump or summarize the whole document; just answer the question, "
        "If the doc doesn't cover it, say so plainly.\n"
        "   NEVER answer a wiki question from the short search snippets alone — "
        "the snippets only tell you WHICH doc to open, not the full answer.\n"
        "- IMPORTANT: search_docs only knows base facts in RUPEES. To get a price per "
        "year or in another currency, FIRST search_docs for the base MONTHLY rupee "
        "price, THEN use calculator for the math, THEN convert_currency for the currency.\n"
        "- Never ask search_docs for dollars or yearly totals directly — it can't do math.\n"
        "- wikipedia is the LAST resort: only for public world knowledge, and only "
        "after search_docs found nothing. Never answer a company/project question "
        "from wikipedia.\n"
        "- Do NOT write your own 'Sources' list or mention document titles as "
        "citations — the app appends a verified Sources list automatically."
    ))

    # ---- THE GRAPH ---------------------------------------------------------
    # MessagesState is a tiny built-in state: just {"messages": [...]} that
    # auto-appends new messages. This replaces the hand-managed `messages` list.

    def agent_node(state):
        """The 'think' step: ask the LLM what to do next, given the conversation."""
        # We prepend SYSTEM each call so the rules are always in view.
        ai = llm.invoke([SYSTEM] + state["messages"])
        return {"messages": [ai]}

    def retrieve_node(state):
        """RETRIEVAL-FIRST (industry standard for company assistants): search the
        company docs for EVERY question, in code, before the agent runs — so an
        answer can never skip the docs because the model guessed the question was
        "general knowledge". The search is recorded as an ordinary search_docs
        call, so citations, guard blocks and the steps panel work unchanged; the
        agent then decides only what ELSE it needs (another query, Outline,
        calculator, Wikipedia)."""
        question = next((m.content for m in reversed(state["messages"])
                         if isinstance(m, HumanMessage)), "")
        if not needs_retrieval(question):
            return {"messages": []}
        # Every connected company source is searched, in code, IN PARALLEL:
        # the index (uploads + Google Drive + Notion) and, where connected, the
        # Outline wiki (searched live). The user never has to name the source.
        searches = [("retrieve_docs", search_docs, "search_docs", {"question": question})]
        if outline:
            searches.append(("retrieve_wiki", search_outline, "search_outline",
                             {"query": question}))
        for call_id, _, name, args in searches:
            emit({"type": "step_start", "id": call_id, "tool": name,
                  "label": step_label(name, args)})

        before = len(cited)

        def run_one(item):
            call_id, tool_fn, name, args = item
            out = tool_fn.invoke(args)
            found = len(cited) - before if name == "search_docs" else None
            emit({"type": "step_end", "id": call_id, "tool": name,
                  "result": step_result(name, out, found)})
            return out

        with ThreadPoolExecutor(max_workers=len(searches)) as pool:
            outputs = list(pool.map(run_one, searches))

        return {"messages": [
            AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}
                                              for call_id, _, name, args in searches]),
            *[ToolMessage(content=out, tool_call_id=call_id, name=name)
              for (call_id, _, name, _), out in zip(searches, outputs)],
        ]}

    builder = StateGraph(MessagesState)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))     # runs whatever tool the LLM asked for
    builder.add_edge(START, "retrieve")            # ALWAYS search the docs first
    builder.add_edge("retrieve", "agent")
    # tools_condition routes to "tools" if the LLM made tool calls, else to END.
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")             # after a tool runs, think again
    graph = builder.compile()

    # ---- RUN: same signature as before -> (answer, trace) ------------------
    # app.py doesn't change. We run the graph, then rebuild the tool-call
    # "trace" from the final message list so the UI can show each step.
    # One question at a time per agent: `cited` is shared, so two overlapping
    # questions would otherwise mix up each other's citations.
    lock = threading.Lock()

    def _stream(user_input, on_event):
        """Run the graph while reporting each step and the answer tokens live.
        Returns the full message list, like graph.invoke would."""
        messages = [HumanMessage(content=user_input)]
        text = ""                         # the answer streamed so far
        started = {}                      # tool call id -> cited count at start
        for mode, data in graph.stream({"messages": list(messages)},
                                       stream_mode=["updates", "messages"]):
            if mode == "messages":
                chunk, meta = data
                if (meta.get("langgraph_node") == "agent" and isinstance(chunk.content, str)
                        and chunk.content and not blocked):
                    text += chunk.content
                    # Mask PII on the text so far, so it never flashes on screen.
                    on_event({"type": "answer", "text": redact_pii(text)})
                continue
            for node, update in (data or {}).items():
                new = (update or {}).get("messages", []) if isinstance(update, dict) else []
                messages += new
                for m in new:
                    if node == "agent" and isinstance(m, AIMessage) and m.tool_calls:
                        if text:          # it wrote a little, then chose a tool
                            text = ""
                            on_event({"type": "answer", "text": ""})
                        for tc in m.tool_calls:
                            started[tc["id"]] = len(cited)
                            on_event({"type": "step_start", "id": tc["id"], "tool": tc["name"],
                                      "label": step_label(tc["name"], tc["args"])})
                    elif node == "tools" and isinstance(m, ToolMessage):
                        found = len(cited) - started.get(m.tool_call_id, len(cited))
                        on_event({"type": "step_end", "id": m.tool_call_id, "tool": m.name,
                                  "result": step_result(m.name, m.content, found)})
        return messages

    def run(user_input, with_sources=False, on_event=None):
        """Answer a question -> (answer, trace).
        with_sources=True -> (answer, trace, sources) with the sources as a list
        of {title, provider, url} instead of a markdown list in the answer.
        on_event(dict) -> stream progress: step_start / step_end / answer events."""
        if on_event:
            on_event({"type": "status", "text": "Checking your question"})
        # GUARD (input): block prompt-injection / jailbreak before doing any work.
        ok, msg = check_prompt_injection(user_input)
        if not ok:
            return (msg, [], []) if with_sources else (msg, [])

        with lock:
            cited.clear()      # citations are per question
            blocked.clear()
            emit_holder[0] = on_event
            try:
                if on_event:
                    messages = _stream(user_input, on_event)
                else:
                    messages = graph.invoke(
                        {"messages": [HumanMessage(content=user_input)]})["messages"]
            finally:
                emit_holder[0] = None
            sources = list(cited)
            blocked_reply = list(blocked)

        # Map each tool result back to its call id so we can pair them up.
        outputs = {m.tool_call_id: m.content for m in messages
                   if isinstance(m, ToolMessage)}

        trace = []
        for m in messages:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    out = outputs.get(tc["id"], "")
                    print(_safe(f"[agent] -> {tc['name']}({tc['args']})"))   # console log
                    print(_safe(f"[agent]    = {out}"))
                    trace.append({"tool": tc["name"], "input": tc["args"],
                                  "output": str(out)})

        answer = messages[-1].content or "(no answer)"
        if blocked_reply:                 # a guard refused: say exactly why
            answer = blocked_reply[0]
            sources = []
        elif _is_not_found(answer):       # "I couldn't find..." -> nothing to cite
            sources = []
        # GUARD (output): scrub any PII (emails, phones, cards) from the answer.
        answer = redact_pii(answer)
        # CITATIONS: list every document the tools actually read.
        if with_sources:
            return answer, trace, sources
        return answer + format_sources(sources), trace

    return run


# Quick demo
if __name__ == "__main__":
    agent = build_agent("public")
    for q in [
        "hi",
        "What is the Pro plan per year in US dollars?",
        "Who founded the company Anthropic?",
    ]:
        print("\n" + "=" * 60)
        print("Q:", q)
        answer, trace = agent(q)
        print("TOOLS USED:", [t["tool"] for t in trace] or "none")
        print("A:", answer)
