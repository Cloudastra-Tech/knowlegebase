import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { session, type KnowledgeItem, type User } from "@/lib/api"
import { GENERIC_STARTERS, startersFrom } from "@/lib/chat-starters"
import { mockApi, renderApp } from "@/test/render"

const PAT: User = { username: "pat", name: "Pat Public", workspace: "acme", role: "public" }
const IDLE = {
  status: "idle",
  step: 0,
  total: 0,
  message: "",
  pending: false,
  summary: null,
  error: null,
  finished_at: null,
}
const SIGNED_IN = {
  "GET /api/auth/me": PAT,
  "GET /api/index/status": IDLE,
  "GET /api/integrations": { workspace: "acme", integrations: [] },
}

function doc(title: string, modified: number, chunks = 3): KnowledgeItem {
  return { title, source: "Uploaded", access: "public", url: null, modified, size: 10, chunks,
           doc_key: `public/${title}`, provider: "uploads", folder: "public" }
}

function sse(...events: object[]) {
  return new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), {
    headers: { "Content-Type": "text/event-stream" },
  })
}

const SAVED = {
  id: "c1",
  title: "What is the refund policy?",
  role_used: "public",
  role_changed: false,
  created_at: 1,
  updated_at: 2,
  messages: [
    {
      id: "q1",
      parent_id: null,
      author: "user",
      content: "What is the refund policy?",
      state: null,
      created_at: 1,
      withheld: false,
    },
    {
      id: "a1",
      parent_id: "q1",
      author: "assistant",
      content: "Refunds are paid within thirty days.",
      state: "answered",
      created_at: 2,
      withheld: false,
      sources: [{ title: "refund.md", provider: "Company documents", url: null }],
      steps: [{ id: "s1", label: "Searching company docs", result: "found 1 relevant document" }],
      standalone_question: null,
      feedback: null,
    },
  ],
}

describe("starter questions", () => {
  it("come from the newest indexed documents' titles", () => {
    expect(
      startersFrom([doc("old_policy.txt", 1), doc("Leave-policy.pdf", 3), doc("Unindexed.txt", 9, 0)]),
    ).toEqual(["What does “Leave policy” cover?", "What does “old policy” cover?"])
  })

  it("are generic without any", () => {
    expect(startersFrom([])).toEqual(GENERIC_STARTERS)
    expect(startersFrom(undefined)).toEqual(GENERIC_STARTERS)
  })
})

describe("the chat", () => {
  it("starts empty with the workspace's own starters and no Outline promise", async () => {
    session.setToken("tok")
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [doc("Travel policy.txt", 5)], outline: false },
    })
    renderApp("/chat")

    expect(
      await screen.findByRole("button", { name: "What does “Travel policy” cover?" }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Answers come from your company's documents, with/)).toBeInTheDocument()
    expect(screen.queryByText(/Outline/)).not.toBeInTheDocument()
    expect(screen.queryByText(/AI Delivery Intelligence Platform/)).not.toBeInTheDocument()
  })

  it("mentions the Outline wiki only where it's connected", async () => {
    session.setToken("tok")
    mockApi({ ...SIGNED_IN, "GET /api/knowledge": { workspace: "acme", items: [], outline: true } })
    renderApp("/chat")
    expect(await screen.findByText(/and the Outline wiki/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: GENERIC_STARTERS[0] })).toBeInTheDocument()
  })

  it("saves a first question as a new conversation and moves to its address", async () => {
    session.setToken("tok")
    const asked: unknown[] = []
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      "POST /api/conversations": {
        id: "c9",
        title: "New chat",
        role_used: "public",
        role_changed: false,
        created_at: 1,
        updated_at: 1,
      },
      "POST /api/chat/stream": (body: unknown) => {
        asked.push(body)
        return sse(
          { type: "conversation", id: "c9", title: "Hi", user_message_id: "q9", message_id: "a9", new_conversation: false },
          { type: "status", text: "Checking your question" },
          { type: "answer", text: "Twenty days" },
          { type: "done", answer: "Twenty days a year.", sources: [], steps: [], state: "answered", message_id: "a9" },
        )
      },
    })
    const router = renderApp("/chat")
    const user = userEvent.setup()
    await user.type(await screen.findByRole("textbox", { name: "Your question" }), "How many leave days?{Enter}")

    expect(await screen.findByText("Twenty days a year.")).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/chat/c9")
    expect(asked).toEqual([{ question: "How many leave days?", conversation_id: "c9" }])
  })

  it("shows a saved conversation again at its address, and asks follow-ups in it", async () => {
    session.setToken("tok")
    const asked: unknown[] = []
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      // Read again once the answer is done (for its versions): now with the follow-up.
      "GET /api/conversations/c1": () =>
        asked.length
          ? {
              ...SAVED,
              messages: [
                ...SAVED.messages,
                { ...SAVED.messages[0], id: "q2", parent_id: "a1", content: "How long?" },
                {
                  ...SAVED.messages[1],
                  id: "a2",
                  parent_id: "q2",
                  content: "Thirty days from the request.",
                  sources: [],
                },
              ],
            }
          : SAVED,
      "POST /api/chat/stream": (body: unknown) => {
        asked.push(body)
        return sse(
          { type: "conversation", id: "c1", title: SAVED.title, user_message_id: "q2", message_id: "a2", new_conversation: false },
          {
            type: "done",
            answer: "Thirty days from the request.",
            sources: [],
            steps: [],
            state: "answered",
            standalone_question: "How long does a refund take?",
            message_id: "a2",
          },
        )
      },
    })
    renderApp("/chat/c1")

    expect(await screen.findByText("Refunds are paid within thirty days.")).toBeInTheDocument()
    expect(screen.getAllByText("What is the refund policy?").length).toBeGreaterThan(0)
    expect(screen.getByText("refund.md")).toBeInTheDocument()

    const user = userEvent.setup()
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "How long?{Enter}")
    expect(await screen.findByText("Thirty days from the request.")).toBeInTheDocument()
    expect(asked).toEqual([{ question: "How long?", conversation_id: "c1" }])
    expect(screen.getByText("Refunds are paid within thirty days.")).toBeInTheDocument()
  })

  it("tells the asker a role change starts a new chat", async () => {
    session.setToken("tok")
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      "GET /api/conversations/c1": { ...SAVED, role_used: "hr", role_changed: true },
    })
    renderApp("/chat/c1")
    expect(await screen.findByText(/Your next question starts a new chat/)).toBeInTheDocument()
  })

  it("goes back to a new chat when the conversation isn't there", async () => {
    session.setToken("tok")
    mockApi({ ...SIGNED_IN, "GET /api/knowledge": { workspace: "acme", items: [], outline: false } })
    const router = renderApp("/chat/gone")
    await waitFor(() => expect(router.state.location.pathname).toBe("/chat"))
  })
})
