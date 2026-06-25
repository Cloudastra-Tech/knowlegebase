"""
app.py  —  the CHAT WEBSITE (now powered by the AGENT).

Streamlit turns this Python file into a web page. Run it with:
    streamlit run app.py

The agent DECIDES which tools to use (search docs / calculator / currency /
wikipedia) and chains them. We show every tool call so you can SEE its reasoning.
"""

import streamlit as st
from agent import build_agent

# ---- Page setup ----
st.set_page_config(page_title="Chat With Your Notes", page_icon="🤖")
st.title("🤖 Chat With Your Notes — Agent")
st.caption("An AI agent that searches your docs, does math, converts currency, and looks things up.")

# ---- Pick WHO is using the app (their role / permission level) ----
role_label = st.sidebar.selectbox("Sign in as:", ["Public user", "HR / Admin"])
role = "hr" if role_label == "HR / Admin" else "public"
st.sidebar.caption(
    "Public users can't retrieve restricted documents (e.g. staff salaries). HR/Admin can."
)
st.sidebar.markdown("**Tools the agent can use:**\n\n🔍 search_docs\n\n🧮 calculator\n\n💱 convert_currency\n\n📖 wikipedia")

# ---- Build the AGENT for this role (cached per role) ----
@st.cache_resource
def get_agent(role):
    return build_agent(role)

agent = get_agent(role)

# ---- Chat history (short-term MEMORY) ----
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---- The input box ----
question = st.chat_input("Ask anything — e.g. 'Pro plan per year in USD?'")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Agent is thinking..."):
            answer, trace = agent(question)
        st.markdown(answer)

        # ---- Show the agent's tool calls (the "logging" / reasoning trace) ----
        if trace:
            with st.expander(f"🛠️ Agent steps ({len(trace)} tool call(s))", expanded=True):
                for i, step in enumerate(trace, 1):
                    st.markdown(f"**Step {i} — `{step['tool']}`**")
                    st.markdown(f"&nbsp;&nbsp;↳ input: `{step['input']}`")
                    st.markdown(f"&nbsp;&nbsp;↳ result: {step['output']}")
        else:
            st.caption("_(answered directly — no tools needed)_")

    st.session_state.messages.append({"role": "assistant", "content": answer})
