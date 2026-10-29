import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { session, type User } from "@/lib/api"
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
const now = Date.now() / 1000
const chat = (id: string, title: string, extra = {}) => ({
  id,
  title,
  role_used: "public",
  role_changed: false,
  pinned: false,
  folder_id: null,
  created_at: now - 60,
  updated_at: now - 60,
  ...extra,
})
const BASE = {
  "GET /api/auth/me": PAT,
  "GET /api/index/status": IDLE,
  "GET /api/integrations": { workspace: "acme", integrations: [] },
  "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
  "GET /api/chat-settings": {
    retention_days: 365,
    sharing_enabled: true,
    updated_by: null,
    updated_at: null,
    retention_choices: [30, 90, 365, null],
  },
  "GET /api/conversations": {
    conversations: [
      chat("c1", "Refund policy"),
      chat("c2", "Pinned one", { pinned: true }),
      chat("c3", "Filed one", { folder_id: "f1" }),
      chat("c4", "Last month", { updated_at: now - 40 * 86400 }),
    ],
    folders: [{ id: "f1", name: "Policies", sort: 1, created_at: 1 }],
  },
}

function sse(...events: object[]) {
  return new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), {
    headers: { "Content-Type": "text/event-stream" },
  })
}

// A chat whose question was edited once: two versions of it, each with its answer.
const q = (id: string, content: string, siblings: string[]) => ({
  id,
  parent_id: null,
  author: "user",
  content,
  state: null,
  created_at: 1,
  withheld: false,
  siblings,
})
const a = (id: string, parent: string, content: string) => ({
  id,
  parent_id: parent,
  author: "assistant",
  content,
  state: "answered",
  created_at: 2,
  withheld: false,
  sources: [],
  steps: [],
  feedback: null,
  followups: ["What else does refund.md cover?"],
  siblings: [id],
})
const BRANCHED = (second: boolean) => ({
  ...chat("c1", "Refund policy"),
  forked_from: null,
  messages: second
    ? [q("q2", "How long do refunds take?", ["q1", "q2"]), a("a2", "q2", "Thirty days.")]
    : [q("q1", "What is the refund policy?", ["q1", "q2"]), a("a1", "q1", "Refunds are paid.")],
})

describe("the history sidebar", () => {
  it("lists pinned chats, folders and the rest by date; the retention isn't in the sidebar", async () => {
    session.setToken("tok")
    mockApi(BASE)
    renderApp("/chat")
    const history = await screen.findByRole("region", { name: "Chat history" })
    const s = within(history)
    expect(await s.findByText("Pinned")).toBeInTheDocument()
    expect(s.getByText("Pinned one")).toBeInTheDocument()
    expect(s.getByText("Policies")).toBeInTheDocument()
    expect(s.getByText("Filed one")).toBeInTheDocument()
    expect(s.getByText("Today")).toBeInTheDocument()
    expect(s.getByText("Older")).toBeInTheDocument()
    expect(screen.queryByText(/Chats are kept/)).not.toBeInTheDocument()

    // Nor in the account menu: it's Settings › Chats now.
    await userEvent.setup().click(screen.getByRole("button", { name: /^Account,\s*Pat Public/ }))
    expect(await screen.findByRole("menuitem", { name: "Settings" })).toHaveAttribute("href", "/settings")
    expect(screen.queryByText(/Chats are kept/)).not.toBeInTheDocument()
    expect(screen.queryByRole("menuitem", { name: "Chat settings" })).not.toBeInTheDocument()
  })

  it("has every screen the account sees in its nav, and the open one marked", async () => {
    session.setToken("tok")
    mockApi(BASE)
    renderApp("/knowledge")
    const nav = await screen.findByRole("navigation", { name: "Screens" })
    // A member: Chat and Knowledge, no admin group.
    expect(within(nav).getAllByRole("link").map((l) => l.textContent)).toEqual(["Chat", "Knowledge"])
    expect(within(nav).getByRole("link", { name: "Knowledge" })).toHaveAttribute("aria-current", "page")
    expect(within(nav).queryByText("Admin")).not.toBeInTheDocument()
  })

  it("folds the Admin group under its label, and remembers it", async () => {
    const ada: User = { username: "ada", name: "Ada Admin", workspace: "acme", role: "admin" }
    session.setToken("tok")
    mockApi({ ...BASE, "GET /api/auth/me": ada })
    renderApp("/knowledge")
    const nav = await screen.findByRole("navigation", { name: "Screens" })
    // A tall window (the tests' matchMedia matches nothing): open.
    const label = within(nav).getByRole("button", { name: "Admin" })
    expect(label).toHaveAttribute("aria-expanded", "true")
    expect(within(nav).getByRole("link", { name: "Verified" })).toBeInTheDocument()
    await userEvent.setup().click(label)
    expect(label).toHaveAttribute("aria-expanded", "false")
    expect(within(nav).queryByRole("link", { name: "Verified" })).not.toBeInTheDocument()
    expect(localStorage.getItem("kb.sidebar.admin")).toBe("closed")

    // Next time, folded as it was left.
    cleanup()
    renderApp("/knowledge")
    const again = await screen.findByRole("navigation", { name: "Screens" })
    expect(within(again).getByRole("button", { name: "Admin" })).toHaveAttribute("aria-expanded", "false")
    expect(within(again).queryByRole("link", { name: "Insights" })).not.toBeInTheDocument()
  })

  it("searches chats from the sidebar, in the palette, and marks the matches", async () => {
    session.setToken("tok")
    const fetched = mockApi({
      ...BASE,
      "GET /api/conversations/search": {
        results: [
          {
            conversation_id: "c1",
            message_id: "a1",
            title: "Refund policy",
            snippet: "paid within \u0002thirty\u0003 days",
            updated_at: now,
          },
        ],
      },
    })
    renderApp("/chat")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: /^Search chats/ }))
    const dialog = await screen.findByRole("dialog", { name: "Search chats" })
    // Chats alone: no actions, no screens.
    expect(within(dialog).queryByText("New chat")).not.toBeInTheDocument()
    await user.type(within(dialog).getByRole("combobox"), "thirty")
    const match = await within(dialog).findByText("thirty", { selector: "mark" })
    expect(match.closest("[cmdk-item]")).toHaveTextContent("Refund policy")
    const searches = fetched.mock.calls.map(([url]) => String(url)).filter((u) => u.includes("/search"))
    expect(searches.at(-1)).toBe("/api/conversations/search?q=thirty")
  })

  it("pins a chat and deletes one after asking", async () => {
    session.setToken("tok")
    const calls: [string, unknown][] = []
    mockApi({
      ...BASE,
      "PATCH /api/conversations/c1": (body: unknown) => {
        calls.push(["patch", body])
        return chat("c1", "Refund policy", { pinned: true })
      },
      "DELETE /api/conversations/c4": () => {
        calls.push(["delete", null])
        return { ok: true }
      },
    })
    renderApp("/chat")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Options for Refund policy" }))
    await user.click(await screen.findByRole("menuitem", { name: "Pin" }))
    await waitFor(() => expect(calls).toEqual([["patch", { pinned: true }]]))

    await user.click(screen.getByRole("button", { name: "Options for Last month" }))
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }))
    const dialog = await screen.findByRole("alertdialog")
    expect(dialog).toHaveTextContent("“Last month”")
    await user.click(within(dialog).getByRole("button", { name: "Delete" }))
    await waitFor(() => expect(calls.at(-1)).toEqual(["delete", null]))
  })
})

describe("branches", () => {
  it("switches between versions of an edited question", async () => {
    session.setToken("tok")
    let second = true
    const patched: unknown[] = []
    mockApi({
      ...BASE,
      "GET /api/conversations/c1": () => BRANCHED(second),
      "PATCH /api/conversations/c1": (body: unknown) => {
        patched.push(body)
        second = false
        return chat("c1", "Refund policy")
      },
    })
    renderApp("/chat/c1")
    expect(await screen.findByText("Thirty days.")).toBeInTheDocument()
    expect(screen.getByText("2 / 2")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Next version of this question" })).toBeDisabled()

    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Previous version of this question" }))
    expect(await screen.findByText("Refunds are paid.")).toBeInTheDocument()
    expect(screen.getByText("1 / 2")).toBeInTheDocument()
    expect(patched).toEqual([{ active_leaf_id: "q1" }])
  })

  it("asks an edited question in a new branch, and offers follow-ups", async () => {
    session.setToken("tok")
    const asked: unknown[] = []
    mockApi({
      ...BASE,
      "GET /api/conversations/c1": () => BRANCHED(asked.length > 0),
      "POST /api/chat/stream": (body: unknown) => {
        asked.push(body)
        return sse(
          { type: "conversation", id: "c1", title: "Refund policy", user_message_id: "q2", message_id: "a2", new_conversation: false },
          { type: "done", answer: "Thirty days.", sources: [], steps: [], state: "answered", message_id: "a2", followups: [] },
        )
      },
    })
    renderApp("/chat/c1")
    const user = userEvent.setup()
    await screen.findByText("Refunds are paid.")
    expect(screen.getByRole("button", { name: /What else does refund.md cover\?/ })).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Edit question" }))
    const box = screen.getByRole("textbox", { name: "Edit your question" })
    await user.clear(box)
    await user.type(box, "How long do refunds take?{Enter}")
    expect(await screen.findByText("Thirty days.")).toBeInTheDocument()
    expect(asked).toEqual([{ question: "How long do refunds take?", conversation_id: "c1", edit_of: "q1" }])
    expect(await screen.findByText("2 / 2")).toBeInTheDocument()
  })
})

describe("the command palette", () => {
  it("opens with Ctrl+K, finds chats and goes to them", async () => {
    session.setToken("tok")
    mockApi({
      ...BASE,
      "GET /api/conversations/search": {
        results: [{ conversation_id: "c4", message_id: null, title: "Last month", snippet: "\u0002Last\u0003 month", updated_at: now }],
      },
      "GET /api/conversations/c4": { ...chat("c4", "Last month"), forked_from: null, messages: [] },
    })
    const router = renderApp("/knowledge")
    await screen.findByRole("link", { name: "Chat" })
    fireEvent.keyDown(window, { key: "k", ctrlKey: true })
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("New chat")).toBeInTheDocument()
    expect(await within(dialog).findByText("Refund policy")).toBeInTheDocument() // recent

    const user = userEvent.setup()
    await user.type(within(dialog).getByRole("combobox"), "last")
    await user.click(await within(dialog).findByText("month"))
    await waitFor(() => expect(router.state.location.pathname).toBe("/chat/c4"))
  })

  it("lists the keyboard shortcuts with Ctrl+/", async () => {
    session.setToken("tok")
    mockApi(BASE)
    renderApp("/chat")
    await screen.findByRole("textbox", { name: "Your question" })
    fireEvent.keyDown(window, { key: "/", ctrlKey: true })
    const dialog = await screen.findByRole("dialog", { name: "Keyboard shortcuts" })
    expect(dialog).toHaveTextContent("New chat")
    expect(dialog).toHaveTextContent("Stop the answer being written")
  })
})

describe("a shared chat", () => {
  it("withholds an answer the viewer may not read, and offers to ask it", async () => {
    session.setToken("tok")
    mockApi({
      ...BASE,
      "GET /api/shared/tok_abcdefghijklmnopqrstuvwxyz": {
        kind: "chat",
        title: "Bonus questions",
        shared_by: "Hana",
        created_at: now,
        expires_at: null,
        withheld: 1,
        messages: [
          q("q1", "When is the bonus announced?", ["q1"]),
          { ...a("a1", "q1", ""), withheld: true, question: "When is the bonus announced?", copy: null },
        ],
      },
    })
    const router = renderApp("/s/tok_abcdefghijklmnopqrstuvwxyz")
    expect(await screen.findByText("Bonus questions")).toBeInTheDocument()
    expect(screen.getByText(/Shared by Hana/)).toBeInTheDocument()
    expect(screen.getByText("You don't have access to some sources")).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Ask it yourself" }))
    await waitFor(() => expect(router.state.location.pathname).toBe("/chat"))
    expect(await screen.findByRole("textbox", { name: "Your question" })).toHaveValue(
      "When is the bonus announced?",
    )
  })

  it("says plainly when a link no longer works", async () => {
    session.setToken("tok")
    mockApi({
      ...BASE,
      "GET /api/shared/tok_abcdefghijklmnopqrstuvwxyz": new Response(JSON.stringify({ detail: "gone" }), {
        status: 410,
        headers: { "Content-Type": "application/json" },
      }),
    })
    renderApp("/s/tok_abcdefghijklmnopqrstuvwxyz")
    expect(await screen.findByText("This link doesn't work any more")).toBeInTheDocument()
  })
})
