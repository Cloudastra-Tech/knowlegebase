import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ChatHome, RECENT } from "@/components/chat-home"
import type { ConversationSummary } from "@/lib/api"

const chat = (id: string, title: string): ConversationSummary => ({
  id,
  title,
  role_used: "public",
  role_changed: false,
  pinned: false,
  folder_id: null,
  created_at: 1,
  updated_at: Date.now() / 1000 - 120,
})

function setup(props: Partial<Parameters<typeof ChatHome>[0]> = {}) {
  const on = { onAsk: vi.fn(), onOpen: vi.fn(), onViewAll: vi.fn() }
  render(<ChatHome starters={[]} firstRun={false} chats={[]} {...on} {...props} />)
  return on
}

describe("ChatHome", () => {
  it("shows nothing it has nothing for", () => {
    setup()
    expect(screen.queryByRole("region", { name: "Recent chats" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("lists the last few chats, newest first, in one glass card, and opens one", async () => {
    const chats = Array.from({ length: 8 }, (_, i) => chat(`c${i}`, `Question ${i}`))
    const on = setup({ chats })
    const recent = screen.getByRole("region", { name: "Recent chats" })
    // The card the art frosts under (redacted-drift.tsx), on the composer's tier.
    expect(recent).toHaveAttribute("data-drift", "glass")
    expect(recent).toHaveClass("glass", "kb-frost")
    const rows = recent.querySelectorAll("li")
    expect(rows).toHaveLength(RECENT)
    expect(rows[0]).toHaveTextContent("Question 0")
    expect(rows[0]).toHaveTextContent("2 min ago")
    // An icon, the title, the time.
    expect(rows[0].querySelector("svg")).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: /Question 1/ }))
    expect(on.onOpen).toHaveBeenCalledWith("c1")
    // View all, in the card's label row.
    await userEvent.setup().click(screen.getByRole("button", { name: "View all" }))
    expect(on.onViewAll).toHaveBeenCalled()
    expect(recent).toContainElement(screen.getByRole("button", { name: "View all" }))
  })

  it("has no spaces or folders: the composer's space chip and the sidebar have them", () => {
    setup({ chats: [chat("c1", "Leave")] })
    expect(screen.queryByRole("group", { name: "Spaces" })).not.toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "Folders" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "All company" })).not.toBeInTheDocument()
  })

  it("asks a starter question, and says where they come from on a first run", async () => {
    const on = setup({ starters: ["What is our leave policy?"], firstRun: true })
    expect(screen.getByText(/Try one of these/)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: "What is our leave policy?" }))
    expect(on.onAsk).toHaveBeenCalledWith("What is our leave policy?")
  })

  it("offers the starters only once the chats are in", () => {
    setup({ starters: ["What is our leave policy?"], chats: undefined })
    expect(screen.queryByRole("group", { name: "Suggested questions" })).not.toBeInTheDocument()
  })

  it("leaves the starters out for someone with recent chats", () => {
    setup({ starters: ["What is our leave policy?"], chats: [chat("c1", "Leave")] })
    expect(screen.queryByRole("group", { name: "Suggested questions" })).not.toBeInTheDocument()
    expect(screen.getByRole("region", { name: "Recent chats" })).toBeInTheDocument()
  })
})
