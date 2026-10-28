import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ChatHome, RECENT } from "@/components/chat-home"
import type { ChatFolder, ConversationSummary, SpaceSummary } from "@/lib/api"

const chat = (id: string, title: string, folder_id: string | null = null): ConversationSummary => ({
  id,
  title,
  role_used: "public",
  role_changed: false,
  pinned: false,
  folder_id,
  created_at: 1,
  updated_at: Date.now() / 1000 - 120,
})
const folder = (id: string, name: string): ChatFolder => ({ id, name, sort: 0, created_at: 1 })
const space = (id: string, kind: "personal" | "team", name: string) => ({ id, kind, name }) as SpaceSummary

function setup(props: Partial<Parameters<typeof ChatHome>[0]> = {}) {
  const on = {
    onAsk: vi.fn(),
    onOpen: vi.fn(),
    onViewAll: vi.fn(),
    onChooseSpace: vi.fn(),
    onOpenFolder: vi.fn(),
  }
  render(
    <ChatHome
      starters={[]}
      firstRun={false}
      chats={[]}
      folders={[]}
      spaces={[]}
      spaceId={null}
      {...on}
      {...props}
    />,
  )
  return on
}

describe("ChatHome", () => {
  it("shows nothing it has nothing for: no recent chats, spaces or folders", () => {
    setup({ folders: [folder("f1", "Empty one")] })
    expect(screen.queryByRole("region", { name: "Recent chats" })).not.toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "Spaces" })).not.toBeInTheDocument()
    // A folder with no chats in it isn't offered.
    expect(screen.queryByRole("group", { name: "Folders" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("lists the last few chats, newest first, and opens one", async () => {
    const chats = Array.from({ length: 8 }, (_, i) => chat(`c${i}`, `Question ${i}`))
    const on = setup({ chats })
    const recent = screen.getByRole("region", { name: "Recent chats" })
    const rows = recent.querySelectorAll("li")
    expect(rows).toHaveLength(RECENT)
    expect(rows[0]).toHaveTextContent("Question 0")
    expect(rows[0]).toHaveTextContent("2 min ago")
    await userEvent.setup().click(screen.getByRole("button", { name: /Question 1/ }))
    expect(on.onOpen).toHaveBeenCalledWith("c1")
    await userEvent.setup().click(screen.getByRole("button", { name: "View all" }))
    expect(on.onViewAll).toHaveBeenCalled()
  })

  it("offers your spaces to ask in, the current one pressed, and the folders with chats", async () => {
    const on = setup({
      spaces: [space("t1", "team", "Finance"), space("p1", "personal", "Mine")],
      spaceId: "t1",
      folders: [folder("f1", "Policies"), folder("f2", "Empty")],
      chats: [chat("c1", "Leave", "f1"), chat("c2", "Travel", "f1")],
    })
    const spaces = screen.getByRole("group", { name: "Spaces" })
    const names = [...spaces.querySelectorAll("button")].map((b) => b.textContent)
    expect(names).toEqual(["All company", "My space", "Finance"])
    expect(screen.getByRole("button", { name: "Finance" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "All company" })).toHaveAttribute("aria-pressed", "false")
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "All company" }))
    expect(on.onChooseSpace).toHaveBeenCalledWith(null)
    await user.click(screen.getByRole("button", { name: "My space" }))
    expect(on.onChooseSpace).toHaveBeenCalledWith("p1")

    const folders = screen.getByRole("group", { name: "Folders" })
    expect(folders.querySelectorAll("button")).toHaveLength(1)
    await user.click(screen.getByRole("button", { name: "Policies, 2 chats" }))
    expect(on.onOpenFolder).toHaveBeenCalledWith(expect.objectContaining({ id: "f1" }))
  })

  it("asks a starter question, and says where they come from on a first run", async () => {
    const on = setup({ starters: ["What is our leave policy?"], firstRun: true })
    expect(screen.getByText(/Try one of these/)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: "What is our leave policy?" }))
    expect(on.onAsk).toHaveBeenCalledWith("What is our leave policy?")
  })
})
