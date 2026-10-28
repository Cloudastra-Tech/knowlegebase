import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

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
    // One heading; how answers work is behind the (i), who you search as is under the
    // composer.
    expect(screen.getByRole("heading", { level: 2, name: "Ask your company docs" })).toBeInTheDocument()
    expect(screen.getByText(/Searching as/)).toHaveTextContent("Searching as Public user")
    expect(screen.queryByText(/Answers come from/)).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: "How answers work" }))
    expect(await screen.findByText(/Answers come from your company's documents, with/)).toBeInTheDocument()
    expect(screen.queryByText(/Outline/)).not.toBeInTheDocument()
    expect(screen.queryByText(/AI Delivery Intelligence Platform/)).not.toBeInTheDocument()
  })

  it("mentions the Outline wiki only where it's connected", async () => {
    session.setToken("tok")
    mockApi({ ...SIGNED_IN, "GET /api/knowledge": { workspace: "acme", items: [], outline: true } })
    renderApp("/chat")
    await userEvent.setup().click(await screen.findByRole("button", { name: "How answers work" }))
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

  it("draws the empty chat's art on the empty state only, and the chat's field once asked", async () => {
    session.setToken("tok")
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
      "POST /api/chat/stream": () =>
        sse(
          { type: "conversation", id: "c9", title: "Hi", user_message_id: "q9", message_id: "a9", new_conversation: false },
          { type: "done", answer: "Twenty days a year.", sources: [], steps: [], state: "answered", message_id: "a9" },
        ),
    })
    renderApp("/chat")
    const fields = () => [...document.querySelectorAll('[data-slot="chat-backdrop"] [data-slot="ascii-field"]')]
    const box = await screen.findByRole("textbox", { name: "Your question" })
    // One field on screen: the art, in the chat field's place.
    expect(fields().map((f) => f.getAttribute("data-mode"))).toEqual(["drift"])

    await userEvent.setup().type(box, "How many leave days?{Enter}")
    expect(await screen.findByText("Twenty days a year.")).toBeInTheDocument()
    // It fades out (400ms) and the chat's own field takes over.
    await waitFor(() => expect(fields().map((f) => f.getAttribute("data-mode"))).toEqual(["ambient"]))
    expect(screen.queryByText(/Searching as/)).not.toBeInTheDocument()
  })

  it("draws no art on a saved conversation", async () => {
    session.setToken("tok")
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      "GET /api/conversations/c1": SAVED,
    })
    renderApp("/chat/c1")
    expect(await screen.findByText("Refunds are paid within thirty days.")).toBeInTheDocument()
    expect(document.querySelector('[data-mode="drift"]')).toBeNull()
    expect(document.querySelectorAll('[data-slot="chat-backdrop"] [data-slot="ascii-field"]')).toHaveLength(1)
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

  it("downloads the conversation as markdown, and prints it", async () => {
    session.setToken("tok")
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      "GET /api/conversations/c1": SAVED,
      "GET /api/conversations/c1/export": () =>
        new Response("# Refunds\n", {
          headers: {
            "Content-Type": "text/markdown",
            "Content-Disposition": 'attachment; filename="Refunds.md"',
          },
        }),
    })
    const saved: string[] = []
    URL.createObjectURL = vi.fn(() => "blob:chat")
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download)
    })
    const print = vi.spyOn(window, "print").mockImplementation(() => {})
    renderApp("/chat/c1")
    await screen.findByText("Refunds are paid within thirty days.")

    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Chat options" }))
    await user.click(screen.getByRole("menuitem", { name: "Download as Markdown" }))
    await waitFor(() => expect(saved).toEqual(["Refunds.md"]))

    await user.click(screen.getByRole("button", { name: "Chat options" }))
    await user.click(screen.getByRole("menuitem", { name: "Print" }))
    await waitFor(() => expect(print).toHaveBeenCalled())
  })

  it("pins the question just asked: it is the scroller's one anchor", async () => {
    session.setToken("tok")
    let asked = false
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      "GET /api/conversations/c1": () =>
        asked
          ? {
              ...SAVED,
              messages: [
                ...SAVED.messages,
                { ...SAVED.messages[0], id: "q2", parent_id: "a1", content: "How long?" },
                { ...SAVED.messages[1], id: "a2", parent_id: "q2", content: "Thirty days.", sources: [] },
              ],
            }
          : SAVED,
      "POST /api/chat/stream": () => {
        asked = true
        return sse(
          { type: "conversation", id: "c1", title: SAVED.title, user_message_id: "q2", message_id: "a2", new_conversation: false },
          { type: "done", answer: "Thirty days.", sources: [], steps: [], state: "answered", message_id: "a2" },
        )
      },
    })
    renderApp("/chat/c1")
    await screen.findByText("Refunds are paid within thirty days.")
    // A saved chat opens at its last question.
    const anchors = () => [...document.querySelectorAll('[data-scroll-anchor="true"]')]
    expect(anchors().map((a) => a.textContent)).toEqual(["What is the refund policy?"])

    const user = userEvent.setup()
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "How long?{Enter}")
    expect(await screen.findByText("Thirty days.")).toBeInTheDocument()
    expect(anchors().map((a) => a.textContent)).toEqual(["How long?"])
    expect(screen.getByRole("region", { name: "Chat messages" })).toContainElement(
      screen.getByRole("log", { name: "Conversation" }),
    )
  })

  it("marks a step still running when an answer is stopped as stopped, not done", async () => {
    session.setToken("tok")
    const encode = (e: object) => new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`)
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      "GET /api/conversations/c1": SAVED,
      // Streams a step, then waits until the request is aborted (Stop).
      "POST /api/chat/stream": (_body: unknown, init: RequestInit) =>
        new Response(
          new ReadableStream({
            start(stream) {
              stream.enqueue(encode({ type: "conversation", id: "c1", title: SAVED.title, user_message_id: "q2", message_id: "a2", new_conversation: false }))
              stream.enqueue(encode({ type: "step_start", id: "s9", tool: "search_docs", label: "Searching company docs" }))
              init.signal?.addEventListener("abort", () =>
                stream.error(new DOMException("Aborted", "AbortError")),
              )
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
    })
    renderApp("/chat/c1")
    await screen.findByText("Refunds are paid within thirty days.")
    const user = userEvent.setup()
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "How long?{Enter}")
    expect(await screen.findByText("Running:")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Stop answering" }))
    // The trace's summary line and the step itself.
    expect(await screen.findAllByText("Stopped:")).toHaveLength(2)
    expect(screen.queryByText("Running:")).not.toBeInTheDocument()
    // Said once, in the one polite status line; the focus goes to the stopped answer.
    expect(screen.getByRole("status")).toHaveTextContent("Answer stopped.")
    await waitFor(() => expect(document.activeElement).toHaveTextContent("Stopped"))
  })

  it("marks a step still running when an answer fails as failed, not done", async () => {
    session.setToken("tok")
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      "POST /api/conversations": { id: "c9", title: "New chat", role_used: "public", role_changed: false, created_at: 1, updated_at: 1 },
      "POST /api/chat/stream": () =>
        sse(
          { type: "conversation", id: "c9", title: "Hi", user_message_id: "q9", message_id: "a9", new_conversation: false },
          { type: "step_start", id: "s1", tool: "search_docs", label: "Searching company docs" },
          { type: "error", message: "The model is unavailable." },
        ),
    })
    renderApp("/chat")
    const user = userEvent.setup()
    await user.type(await screen.findByRole("textbox", { name: "Your question" }), "How many leave days?{Enter}")
    expect(await screen.findByText("The model is unavailable.")).toBeInTheDocument()
    expect(screen.getByText("Failed:")).toBeInTheDocument()
    expect(screen.queryByText("Done:")).not.toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveTextContent("Couldn't answer.")
  })

  it("shows a saved stopped answer's unfinished step as stopped", async () => {
    session.setToken("tok")
    const stopped = {
      ...SAVED.messages[1],
      state: "stopped",
      steps: [
        { id: "s1", label: "Searching company docs", result: "found 1 relevant document" },
        { id: "s2", label: "Reading a wiki page" },
      ],
    }
    mockApi({
      ...SIGNED_IN,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      "GET /api/conversations/c1": { ...SAVED, messages: [SAVED.messages[0], stopped] },
    })
    renderApp("/chat/c1")
    await screen.findByText("Refunds are paid within thirty days.")
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: /Stopped/ }))
    expect(screen.getByText("Stopped while reading a wiki page")).toBeInTheDocument()
    expect(screen.getByText("Done:")).toBeInTheDocument()
    expect(screen.getAllByText("Stopped:")).toHaveLength(2) // the summary, and the step
  })

  it("goes back to a new chat when the conversation isn't there", async () => {
    session.setToken("tok")
    mockApi({ ...SIGNED_IN, "GET /api/knowledge": { workspace: "acme", items: [], outline: false } })
    const router = renderApp("/chat/gone")
    await waitFor(() => expect(router.state.location.pathname).toBe("/chat"))
  })
})
