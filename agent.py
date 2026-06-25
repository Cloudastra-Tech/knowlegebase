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

import requests
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition

from rag import load_chain, answer_question


def build_agent(role="public"):
    """Create an agent for a given user role. Returns a run(question) function."""
    retriever, rag_llm = load_chain(role)

    # ---- The TOOLS the agent can choose from --------------------------------
    @tool
    def search_docs(question: str) -> str:
        """Search Cloudastra Academy's own documents: pricing, refund policy,
        support hours, student stipend, staff. Use for anything about the academy."""
        answer, _ = answer_question(question, retriever, rag_llm, role)
        return answer

    @tool
    def calculator(expression: str) -> str:
        """Do arithmetic. Pass a math expression, e.g. '2000*12' or '24000*0.8'."""
        try:
            return str(eval(expression, {"__builtins__": {}}, {}))
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
        """Get a short summary of a general-knowledge topic from Wikipedia."""
        try:
            t = topic.strip().replace(" ", "_")
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}"
            # Wikipedia REQUIRES a User-Agent header, or it rejects the request.
            headers = {"User-Agent": "cloudastra-learning-app/1.0 (student project)"}
            data = requests.get(url, headers=headers, timeout=10).json()
            return data.get("extract", "No summary found.")
        except Exception as e:
            return f"Error: {e}"

    tools = [search_docs, calculator, convert_currency, wikipedia]

    # The model is told about the tools; it decides when to call them.
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0).bind_tools(tools)

    SYSTEM = SystemMessage(content=(
        "You are a helpful assistant that solves multi-step questions by CHAINING tools.\n"
        "- For Cloudastra Academy facts (prices, refund, support, stipend): search_docs.\n"
        "- IMPORTANT: search_docs only knows base facts in RUPEES. To get a price per "
        "year or in another currency, FIRST search_docs for the base MONTHLY rupee "
        "price, THEN use calculator for the math, THEN convert_currency for the currency.\n"
        "- Never ask search_docs for dollars or yearly totals directly — it can't do math.\n"
        "- Use wikipedia only for general world knowledge, not company facts."
    ))

    # ---- THE GRAPH ---------------------------------------------------------
    # MessagesState is a tiny built-in state: just {"messages": [...]} that
    # auto-appends new messages. This replaces the hand-managed `messages` list.

    def agent_node(state):
        """The 'think' step: ask the LLM what to do next, given the conversation."""
        # We prepend SYSTEM each call so the rules are always in view.
        ai = llm.invoke([SYSTEM] + state["messages"])
        return {"messages": [ai]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))     # runs whatever tool the LLM asked for
    builder.add_edge(START, "agent")
    # tools_condition routes to "tools" if the LLM made tool calls, else to END.
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")             # after a tool runs, think again
    graph = builder.compile()

    # ---- RUN: same signature as before -> (answer, trace) ------------------
    # app.py doesn't change. We run the graph, then rebuild the tool-call
    # "trace" from the final message list so the UI can show each step.
    def run(user_input):
        result = graph.invoke({"messages": [HumanMessage(content=user_input)]})
        messages = result["messages"]

        # Map each tool result back to its call id so we can pair them up.
        outputs = {m.tool_call_id: m.content for m in messages
                   if isinstance(m, ToolMessage)}

        trace = []
        for m in messages:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    out = outputs.get(tc["id"], "")
                    print(f"[agent] -> {tc['name']}({tc['args']})")   # console log
                    print(f"[agent]    = {out}")
                    trace.append({"tool": tc["name"], "input": tc["args"],
                                  "output": str(out)})

        answer = messages[-1].content or "(no answer)"
        return answer, trace

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
