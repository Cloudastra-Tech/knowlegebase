import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { AssistantMessage, type AnswerActions, type Message } from "@/components/chat-answer"
import { KnowledgeView } from "@/components/knowledge-view"
import { session } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

// Verified answers in the chat (C18) and source rules on the Knowledge screen (PL-28).

function show(message: Partial<Message>, actions: Partial<AnswerActions> = {}) {
  const all: AnswerActions = { onFeedback: vi.fn(), onFork: vi.fn(), ...actions }
  render(
    withProviders(
      <AssistantMessage
        message={{ key: "a1", id: "m1", role: "assistant", text: "", state: "answered", ...message }}
        actions={all}
      />,
    ),
  )
  return all
}

describe("a verified answer in the chat", () => {
  it("is shown in a panel saying who verified it and when", () => {
    show({
      text: "You get **20 days** a year.",
      verified: { id: "v1", title: "Annual leave", by: "Priya", at: Date.UTC(2026, 8, 12) / 1000 },
    })
    const panel = screen.getByLabelText("Verified answer")
    expect(panel).toHaveTextContent(/Verified by Priya · (12 Sep|Sep 12)/) // the browser's locale
    expect(within(panel).getByText("20 days")).toBeInTheDocument()
  })

  it("can be saved as a verified answer by HR from the More menu", async () => {
    const actions = show({ text: "Refunds take thirty days." }, { onVerify: vi.fn() })
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "More" }))
    await user.click(await screen.findByRole("menuitem", { name: "Save as verified answer" }))
    expect(actions.onVerify).toHaveBeenCalledWith(expect.objectContaining({ id: "m1" }))
  })

  it("isn't offered to members, nor on an answer that is verified already", async () => {
    show({ text: "Refunds take thirty days." })
    await userEvent.setup().click(screen.getByRole("button", { name: "More" }))
    expect(screen.queryByRole("menuitem", { name: "Save as verified answer" })).not.toBeInTheDocument()
  })

  it("keeps the steps panel hidden when the admin turned it off", () => {
    show({ text: "Hi.", steps: [{ id: "s1", label: "Searching company docs", done: true }] }, { hideSteps: true })
    expect(screen.queryByText(/1 step/)).not.toBeInTheDocument()
  })
})

const ITEM = {
  title: "2021 policy.txt",
  source: "Uploaded",
  access: "public",
  url: null,
  modified: 1_700_000_000,
  size: 100,
  chunks: 3,
  doc_key: "public/2021 policy.txt",
  provider: "uploads",
  folder: "public",
  rule: null,
}

describe("source rules on the Knowledge screen", () => {
  it("blocks a document from its row and lists the rule", async () => {
    session.setRole("hr")
    let rules: object[] = []
    const fetch = mockApi({
      "GET /api/knowledge": () => ({ workspace: "acme", items: [ITEM], outline: false, rules }),
      "POST /api/admin/doc-rules": (body: unknown) => {
        rules = [{ id: 1, weight: 0, ...(body as object) }]
        return rules[0]
      },
    })
    render(withProviders(<KnowledgeView />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Source rules for 2021 policy.txt" }))
    const block = await screen.findAllByRole("menuitem", { name: "Block from answers" })
    await user.click(block[0]) // "This document" comes first
    await waitFor(() =>
      expect(fetch.mock.calls.some(([url, init]) => String(url).endsWith("/doc-rules") && init?.method === "POST")).toBe(true),
    )
    const post = fetch.mock.calls.find(([url]) => String(url).endsWith("/doc-rules"))!
    expect(JSON.parse(post[1]!.body as string)).toEqual({
      match_type: "doc_key",
      match_value: "public/2021 policy.txt",
      action: "block",
    })
    expect(await screen.findByRole("heading", { name: "Source rules" })).toBeInTheDocument()
  })

  it("shows no rule controls to someone viewing as public", async () => {
    session.setRole("public")
    mockApi({ "GET /api/knowledge": { workspace: "acme", items: [ITEM], outline: false } })
    render(withProviders(<KnowledgeView />))
    expect(await screen.findByText("2021 policy.txt")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Source rules for/ })).not.toBeInTheDocument()
  })
})
