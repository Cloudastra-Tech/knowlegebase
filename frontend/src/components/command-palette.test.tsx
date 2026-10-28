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

  it("opens on chats alone from the sidebar's Search chats", async () => {
    mockApi({
      "GET /api/conversations": { conversations: [{ id: "c1", title: "Leave policy", updated_at: 1 }] },
      "GET /api/prompts": { prompts: [] },
    })
    render(
      withProviders(
        <MemoryRouter>
          <CommandPalette user={ADMIN} role="hr" />
        </MemoryRouter>,
      ),
    )
    window.dispatchEvent(new CustomEvent(OPEN_SEARCH))
    await waitFor(() => expect(item("Leave policy")).not.toBeNull())
    expect(item("New chat")).toBeNull()
    expect(item("Knowledge")).toBeNull()
  })

  // Go to: the sidebar's screens, gated the same way (lib/app-screens.ts).
  const goTo = async (user: User, role: "hr" | "public") => {
    mockApi({
      "GET /api/conversations": { conversations: [] },
      "GET /api/prompts": { prompts: [] },
    })
    render(
      withProviders(
        <MemoryRouter>
          <CommandPalette user={user} role={role} />
        </MemoryRouter>,
      ),
    )
    await userEvent.setup().keyboard("{Control>}k{/Control}")
    const group = (await screen.findByText("Go to")).closest("[cmdk-group]")!
    return [...group.querySelectorAll("[cmdk-item]")].map((el) => el.textContent)
  }

  it("goes to every screen an admin's sidebar has, Verified and Assistant too", async () => {
    expect(await goTo(ADMIN, "hr")).toEqual([
      "Chat",
      "Knowledge",
      "Documents",
      "Integrations",
      "Spaces",
      "My prompts",
      "Insights",
      "Verified",
      "Assistant",
      "Access",
    ])
  })

  it("keeps Assistant and Access from HR", async () => {
    const hr = { username: "hr@acme.example", role: "hr" } as User
    const hrScreens = await goTo(hr, "hr")
    expect(hrScreens).toContain("Verified")
    expect(hrScreens).not.toContain("Assistant")
    expect(hrScreens).not.toContain("Access")
  })

  it("shows a member no admin screens", async () => {
    const member = { username: "pat@acme.example", role: "public" } as User
    expect(await goTo(member, "public")).toEqual(["Chat", "Knowledge", "Spaces", "My prompts"])
  })
})
