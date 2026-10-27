import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { session, type User } from "@/lib/api"
import { mockApi, renderApp } from "@/test/render"

const PAT: User = { username: "pat", name: "Pat Public", workspace: "acme", role: "public" }
const IDLE = { status: "idle", step: 0, total: 0, message: "", pending: false, summary: null,
               error: null, reason: null, finished_at: null }
const TOKEN = "t".repeat(43)

const SHARED = {
  kind: "chat",
  title: "Refunds",
  shared_by: "Hana",
  created_at: 1,
  expires_at: null,
  withheld: 1,
  messages: [
    { id: "q1", parent_id: null, author: "user", content: "What is the refund policy?",
      state: null, created_at: 1, withheld: false },
    { id: "a1", parent_id: "q1", author: "assistant", content: "Thirty days.", state: "answered",
      created_at: 2, withheld: false, sources: [], steps: [], question: "What is the refund policy?",
      copy: "Thirty days." },
    { id: "q2", parent_id: "a1", author: "user", content: "When is the bonus?", state: null,
      created_at: 3, withheld: false },
    { id: "a2", parent_id: "q2", author: "assistant", content: "", state: "answered",
      created_at: 4, withheld: true, sources: [], steps: [], question: "When is the bonus?",
      copy: null },
  ],
}

describe("a shared chat: Continue in my chats", () => {
  it("copies what the viewer can see into their own chats and opens it", async () => {
    session.setToken("tok")
    let continued = 0
    mockApi({
      "GET /api/auth/me": PAT,
      "GET /api/index/status": IDLE,
      "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
      [`GET /api/shared/${TOKEN}`]: SHARED,
      "GET /api/conversations/c9": { ...SHARED, id: "c9", title: "↳ Refunds", role_used: "public",
                                     role_changed: false, updated_at: 5,
                                     messages: SHARED.messages.slice(0, 2) },
      [`POST /api/shared/${TOKEN}/continue`]: () => {
        continued += 1
        return { id: "c9", title: "↳ Refunds", skipped: 1 }
      },
    })
    const router = renderApp(`/s/${TOKEN}`)
    await screen.findByText("Thirty days.")
    await userEvent.setup().click(screen.getByRole("button", { name: "Continue in my chats" }))
    await waitFor(() => expect(router.state.location.pathname).toBe("/chat/c9"))
    expect(continued).toBe(1)
  }, 15_000) // the chat page loads on the way

  it("isn't offered when nothing in it can be seen", async () => {
    session.setToken("tok")
    mockApi({
      "GET /api/auth/me": PAT,
      "GET /api/index/status": IDLE,
      [`GET /api/shared/${TOKEN}`]: { ...SHARED, messages: SHARED.messages.slice(2), withheld: 1 },
    })
    renderApp(`/s/${TOKEN}`)
    await screen.findByText("When is the bonus?")
    expect(screen.queryByRole("button", { name: "Continue in my chats" })).not.toBeInTheDocument()
  })
})
