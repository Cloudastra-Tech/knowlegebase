import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router"

import { CommandPalette } from "@/components/command-palette"
import type { User } from "@/lib/api"
import { OPEN_SEARCH } from "@/lib/chat-events"
import { mockApi, withProviders } from "@/test/render"

const ADMIN = { username: "admin@acme.example", role: "admin" } as User

// The item that reads `label` (by text: role queries are slow on a dialog in jsdom).
const item = (label: string) => screen.queryByText(label)?.closest("[cmdk-item]") ?? null

describe("CommandPalette", () => {
  it("lights the first item again after a search", async () => {
    mockApi({
      "GET /api/conversations": { conversations: [{ id: "c1", title: "Leave policy", updated_at: 1 }] },
      "GET /api/conversations/search": [],
      "GET /api/prompts": { prompts: [] },
    })
    render(
      withProviders(
        <MemoryRouter>
          <CommandPalette user={ADMIN} role="hr" />
        </MemoryRouter>,
      ),
    )
    const user = userEvent.setup()
    await user.keyboard("{Control>}k{/Control}")
    const box = await screen.findByPlaceholderText("Search chats, or type a screen…")
    await waitFor(() => expect(item("New chat")).toHaveAttribute("aria-selected", "true"))

    // Down to another item, then a search that leaves only the screens.
    await user.keyboard("{ArrowDown}")
    expect(item("New chat")).toHaveAttribute("aria-selected", "false")
    await user.type(box, "know")
    await waitFor(() => expect(item("New chat")).toBeNull())
    expect(item("Knowledge")).toHaveAttribute("aria-selected", "true")
  })

  it("opens on a folder's chats from the new chat's home", async () => {
    mockApi({
      "GET /api/conversations": {
        conversations: [
          { id: "c1", title: "Leave policy", updated_at: 2, folder_id: "f1" },
          { id: "c2", title: "Laptop setup", updated_at: 1, folder_id: null },
        ],
        folders: [{ id: "f1", name: "Policies", sort: 0, created_at: 1 }],
      },
      "GET /api/prompts": { prompts: [] },
    })
    render(
      withProviders(
        <MemoryRouter>
          <CommandPalette user={ADMIN} role="hr" />
        </MemoryRouter>,
      ),
    )
    window.dispatchEvent(new CustomEvent(OPEN_SEARCH, { detail: { folderId: "f1" } }))
    await waitFor(() => expect(item("Leave policy")).not.toBeNull())
    expect(screen.getByText("Policies")).toBeInTheDocument() // the group's heading
    expect(item("Laptop setup")).toBeNull()
  })
})
