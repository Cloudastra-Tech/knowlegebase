"""
Saved chats around the agent (conversations_store.py holds the rows):

  visible_to()   whether a viewer's CURRENT role may read a message. Checked on
                 every read, so an owner demoted from HR stops seeing answers built
                 from restricted documents, even in their own chats.
  start_turn()   a question in a conversation: saves it, and loads the history the
                 follow-up is answered with (bounded by turns and size). Also an
                 edited question or a regenerated answer: a new branch (C10).
  view()         the messages shown, each with its versions (‹ 2 / 3 ›).
  markdown_of()  an answer for the clipboard, its citations as footnotes (C25).
  outcome()      the state an answer ends in (answered, not_found, ...) and the
                 highest access level of everything the model read for it.

Role rule (decision D5): a conversation keeps the role it was started under. When
the asker's effective role has changed since (HR switched to "view as public", or
an admin changed their role), their next question starts a NEW conversation, so
answers built under one role never become context for another.
"""

import re
import time
import urllib.parse

from fastapi import HTTPException

import conversations_store as store
from kb.services import agents
from rag import ROLE_ACCESS

# History a follow-up is answered with: at most this many earlier question-and-
# answer turns, and about this many tokens of them (4 characters a token).
HISTORY_TURNS = 6
HISTORY_TOKENS = 3000
CHARS_PER_TOKEN = 4
# Answers that may be history. Refusals and failures are left out: a refused
# question shouldn't be something the next one can lean on.
HISTORY_STATES = ("answered", "partial", "not_found", "general_knowledge", "stopped")
# Answers that can be asked again. "pending" too: Stop is pressed, and Retry right
# after, before the stopped answer has been saved (the old run ends off the path).
RETRY_STATES = ("error", "stopped", "pending")
# An answer still unwritten after this long was cut off (e.g. a server restart).
INTERRUPTED_AFTER = 10 * 60
INTERRUPTED = "This answer was interrupted before it finished."
OUTLINE_TOOLS = ("search_outline", "read_outline_doc")
GENERAL_KNOWLEDGE = "General knowledge"


def max_access(levels):
    """The highest of some access levels. Anything that isn't "public" counts as
    restricted, so an unknown level is never shown to everyone."""
    return "public" if all(level == "public" for level in levels) else "restricted"


def visible_to(role, message):
    """True if someone viewing the app as `role` may read this message: the highest
    access level of everything the model read for it (max_access), and of each of
    its sources, is one the role may see. Questions are their asker's own words."""
    if message.get("author") == "user":
        return True
    allowed = ROLE_ACCESS.get(role, ["public"])
    levels = [message.get("max_access") or "restricted"]
    levels += [s["access"] for s in message.get("sources") or [] if s.get("access")]
    return all(level in allowed for level in levels)


def present(message, role, admin=False, feedback=None):
    """A message as the API returns it to a viewer. An answer they may no longer
    read keeps its place but loses everything taken from the documents."""
    state = message.get("state")
    content = message["content"]
    if message["author"] == "assistant" and state is None:
        late = time.time() - message["created_at"] > INTERRUPTED_AFTER
        state, content = ("error", INTERRUPTED) if late else ("pending", content)
    out = {"id": message["id"], "parent_id": message.get("parent_id"),
           "author": message["author"], "content": content, "state": state,
           "created_at": message["created_at"], "withheld": False}
    if message["author"] == "user":
        return out
    if not visible_to(role, message):
        return {**out, "content": "", "withheld": True, "sources": [], "steps": [],
                "standalone_question": None, "feedback": None, "followups": [],
                "verified": None}
    out.update(sources=message.get("sources") or [], steps=message.get("steps") or [],
               standalone_question=message.get("standalone_question"),
               feedback=_feedback_view(feedback), followups=message.get("followups") or [],
               verified=message.get("verified"))
    if admin:                       # the raw tool input and output (steps panel)
        out["trace"] = message.get("trace") or []
    return out


def _feedback_view(f):
    return {"rating": f["rating"], "reasons": f["reasons"], "comment": f["comment"]} if f else None


def history_for(path, role):
    """The conversation before a question, as the agent takes it, and the highest
    access level in it: the last HISTORY_TURNS turns, newest kept first within
    HISTORY_TOKENS. Only answers the role may read now are included."""
    turns, question = [], None
    for m in path:
        if m["author"] == "user":
            question = m
        elif (question and m.get("state") in HISTORY_STATES and m["content"]
              and visible_to(role, m)):
            turns.append((question, m))
            question = None
    budget = HISTORY_TOKENS * CHARS_PER_TOKEN
    history, levels = [], []
    for q, a in reversed(turns[-HISTORY_TURNS:]):
        answer = a["content"][:max(0, budget - len(q["content"]))]
        if not answer:
            break
        history[:0] = [{"role": "user", "content": q["content"]},
                       {"role": "assistant", "content": answer}]
        levels.append(a["max_access"])
        budget -= len(q["content"]) + len(answer)
    return history, max_access(levels)


def _answer_to_ask_again(ws, owner, role, conv, mid, states, refusal):
    """The question of answer `mid` in this conversation, if it may be asked again
    (its state is one of `states`, or any finished one when states is None)."""
    answer = store.get_message(ws, owner, mid)
    state = present(answer, role)["state"] if answer else None
    if (not answer or answer["conversation_id"] != conv["id"] or answer["author"] != "assistant"
            or (state not in states if states else state == "pending")):
        raise HTTPException(400, refusal)
    question = store.get_message(ws, owner, answer["parent_id"])
    if not question:
        raise HTTPException(400, refusal)
    return question


def start_turn(ws, owner, role, conversation_id, question, retry_of=None, regenerate_of=None,
               edit_of=None):
    """Save a question in a conversation and the empty answer to fill in. Or:
       retry_of       ask the question of a failed or stopped answer again
       regenerate_of  a new answer to the same question beside a finished one (C10)
       edit_of        `question` replaces an earlier question: a new question beside
                      it, answered with the conversation before it (C10)
    The new answer (and edited question) are siblings of the old ones, which stay:
    the chat shows the new branch, and the "‹ 2 / 3 ›" controls switch back.
    Returns a dict: conversation, question, user_message_id, message_id, history,
    history_access, and new_conversation (True when a role change started a new
    one, see above)."""
    conv = store.get(ws, owner, conversation_id)
    if not conv:
        raise HTTPException(404, "That conversation doesn't exist.")
    again, parent_id = None, conv["active_leaf_id"]
    if retry_of:
        again = _answer_to_ask_again(ws, owner, role, conv, retry_of, RETRY_STATES,
                                     "Only an answer that failed or was stopped can be retried.")
    elif regenerate_of:
        again = _answer_to_ask_again(ws, owner, role, conv, regenerate_of, None,
                                     "Only a finished answer can be written again.")
    elif edit_of:
        edited = store.get_message(ws, owner, edit_of)
        if not edited or edited["conversation_id"] != conv["id"] or edited["author"] != "user":
            raise HTTPException(400, "Only a question in this chat can be edited.")
        parent_id = edited["parent_id"]
    if again:
        question = again["content"]
    if not question:
        raise HTTPException(400, "Please type a question.")

    new_conversation = conv["role_used"] != role
    if new_conversation:
        conv = store.create(ws, owner, role)
        again, parent_id = None, None     # asked afresh in the new one
    if again:
        path = store.path(conv["id"], again["parent_id"])
        user_message_id = again["id"]
    else:
        path = store.path(conv["id"], parent_id)
        user_message_id = store.add_message(conv["id"], "user", question, parent_id=parent_id)
    message_id = store.add_message(conv["id"], "assistant", "", parent_id=user_message_id)
    history, history_access = history_for(path, role)
    first = not path
    conv = store.get(ws, owner, conv["id"])
    return {"conversation": conv, "question": question, "user_message_id": user_message_id,
            "message_id": message_id, "history": history, "history_access": history_access,
            "new_conversation": new_conversation,
            # The chat's first question, titled from it (suggestions.chat_title).
            "first_question": first and conv["title_source"] == "auto" and not again}


# ---- What a viewer sees of a conversation ------------------------------------------------
def view(conv, messages, leaf_id, role, admin=False, feedback=None):
    """The messages from the first to `leaf_id` as `role` may see them, each with
    the versions beside it (siblings: edits and regenerated answers)."""
    path = store.path(conv["id"], leaf_id, messages)
    versions = store.siblings(messages)
    feedback = feedback or {}
    return [{**present(m, role, admin, feedback.get(m["id"])), "siblings": versions[m["id"]]}
            for m in path]


def forked_from(ws, owner, conv):
    """{conversation_id, title} of the chat a fork was branched from, while it's there."""
    mid = conv.get("forked_from_message_id")
    found = store.conversation_of_message(ws, owner, mid) if mid else None
    return {"conversation_id": found["id"], "title": found["title"]} if found else None


def markdown_of(message):
    """An answer as markdown, its [S#] markers as footnotes ([^1]) and its sources
    listed under it (C25): what Copy puts on the clipboard. Call only for an answer
    the reader may see (visible_to)."""
    sources = message.get("sources") or []
    numbered = {s["n"]: s for s in sources if isinstance(s.get("n"), int)}
    used = []

    def footnote(m):
        n = int(m.group(1))
        if n not in numbered:
            return ""
        if n not in used:
            used.append(n)
        return f"[^{used.index(n) + 1}]"
    text = re.sub(r"[ \t]*\[S(\d{1,3})\]", footnote, message.get("content") or "").strip()
    notes = []
    for i, n in enumerate(used, 1):
        notes.append(f"[^{i}]: {_source_line(numbered[n])}")
    others = [s for s in sources if not isinstance(s.get("n"), int)]
    for s in others:                      # pages read whole, Wikipedia: no marker
        notes.append(f"- {_source_line(s)}")
    if notes:
        text += "\n\n" + "\n".join(notes)
    return text


def _source_line(s):
    title = s.get("title") or "Untitled"
    where = [p for p in (s.get("heading_path"),
                         f"page {s['page']}" if s.get("page") else None) if p]
    line = f"[{title}]({s['url']})" if s.get("url") else title
    if where:
        line += f", {', '.join(where)}"
    provider = ("General knowledge · " if s.get("kind") == "general_knowledge" else "") + (
        s.get("provider") or "")
    return f"{line} — {provider}" if provider else line


def _wikipedia_sources(trace):
    """A Wikipedia lookup the answer used, as a labelled source: general knowledge,
    not the company's documents."""
    out = []
    for t in trace:
        topic = str((t.get("input") or {}).get("topic") or "").strip()
        result = str(t.get("output") or "")
        if (t.get("tool") != "wikipedia" or not topic or result.startswith("Error")
                or "No summary found" in result):
            continue
        source = {"title": topic, "provider": "Wikipedia", "kind": "general_knowledge",
                  "access": "public",
                  "url": "https://en.wikipedia.org/wiki/"
                         + urllib.parse.quote(topic.replace(" ", "_"))}
        if source not in out:
            out.append(source)
    return out


def read_access(out, trace, history_access="public"):
    """The highest access level of everything a model read: the chunks searched,
    the Outline wiki if it was searched, and the earlier turns it was given."""
    levels = set(out.get("access") or ()) | {history_access}
    if any(t.get("tool") in OUTLINE_TOOLS for t in trace):
        levels.add(agents.outline_access())
    return max_access(levels)


def outcome(answer, trace, sources, out, history_access="public"):
    """(state, sources, max_access) of a finished answer. Sources gain a
    Wikipedia entry when it was used; an answer only from Wikipedia is
    general_knowledge, so it's never shown as coming from the documents."""
    general = [] if out.get("blocked") else _wikipedia_sources(trace)
    if out.get("blocked"):
        state = "blocked"
    elif general and not sources:
        state = "general_knowledge"
    else:
        state = {"none": "not_found", "partial": "partial"}.get(out.get("coverage"),
                                                                "answered")
    if state == "not_found":
        general = []
    return state, list(sources) + general, read_access(out, trace, history_access)
