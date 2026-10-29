import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it } from "vitest"

import { session, type User } from "@/lib/api"
import type { BillingSummary } from "@/lib/api/billing"
import { mockApi, renderApp } from "@/test/render"

// The account menu, the Settings screen and the old addresses of its sections, per role
// (components/account-menu.tsx, components/settings/settings-view.tsx, routes.tsx).

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
const MEMBER: User = { username: "pat@acme.example", name: "Pat Public", workspace: "acme", role: "public" }
const ADMIN: User = { username: "ada@acme.example", name: "Ada Admin", workspace: "acme", role: "admin" }
const BILLING = { can_view: true, owner: true } as BillingSummary
const CHAT_SETTINGS = {
  retention_days: 365,
  sharing_enabled: true,
  updated_by: null,
  updated_at: null,
  retention_choices: [30, 90, 365, null],
}

function signedIn(user: User, more: Record<string, unknown> = {}) {
  session.setToken("tok")
  session.setRole(user.role === "public" ? "public" : "hr")
  return mockApi({
    ...more,
    "GET /api/auth/me": user,
    "GET /api/index/status": IDLE,
    "GET /api/integrations": { workspace: "acme", integrations: [] },
    "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
    "GET /api/conversations": { conversations: [], folders: [] },
    "GET /api/chat-settings": CHAT_SETTINGS,
    "GET /api/prompts": { prompts: [], can_manage_workspace: user.role === "admin" },
  })
}

const menuItems = () => screen.getAllByRole("menuitem").map((m) => m.textContent?.trim())

// A phone, for useIsMobile (hooks/use-mobile.ts): the one query it asks about matches.
function asPhone() {
  const desktop = window.matchMedia
  window.matchMedia = (query: string) =>
    ({ ...desktop(query), matches: query.includes("max-width: 767px") }) as MediaQueryList
  return () => (window.matchMedia = desktop)
}

let restore: (() => void) | null = null
afterEach(() => {
  restore?.()
  restore = null
})

describe("the account menu", () => {
  it("is short for a member: Settings, Appearance, the shortcuts and Sign out", async () => {
    signedIn(MEMBER)
    renderApp("/knowledge")
    await userEvent.setup().click(await screen.findByRole("button", { name: /^Account,\s*Pat Public/ }))
    expect(await screen.findByRole("menuitem", { name: "Settings" })).toHaveAttribute("href", "/settings")
    expect(menuItems().map((t) => t?.replace(/(Ctrl|⌘)\/$/, ""))).toEqual([
      "Settings",
      "Appearance",
      "Keyboard shortcuts",
      "Sign out",
    ])
    const menu = screen.getByRole("menu")
    // Who you are: name, email and workspace, in the menu's head.
    expect(within(menu).getByText("pat@acme.example")).toBeInTheDocument()
    expect(within(menu).getByText("acme workspace")).toBeInTheDocument()
    // None of the personal screens, nor the retention, nor signing out everywhere.
    for (const gone of ["Spaces", "My prompts", "API keys", "Digests", "My connections", "Sign out everywhere"])
      expect(within(menu).queryByRole("menuitem", { name: gone })).not.toBeInTheDocument()
    expect(within(menu).queryByText(/Chats are kept/)).not.toBeInTheDocument()
  })

  it("adds View as for an admin, and nothing else", async () => {
    signedIn({ ...ADMIN, billing: BILLING })
    renderApp("/knowledge")
    await userEvent.setup().click(await screen.findByRole("button", { name: /^Account,\s*Ada Admin/ }))
    await screen.findByRole("menuitem", { name: "Settings" })
    expect(menuItems().map((t) => t?.replace(/(Ctrl|⌘)\/$/, "").replace(/HR \/ Admin$/, ""))).toEqual([
      "Settings",
      "View as",
      "Appearance",
      "Keyboard shortcuts",
      "Sign out",
    ])
    expect(screen.queryByRole("menuitem", { name: /billing/i })).not.toBeInTheDocument()
  })

  it("opens Settings from its Settings item", async () => {
    signedIn(MEMBER)
    const router = renderApp("/knowledge")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: /^Account,\s*Pat Public/ }))
    await user.click(await screen.findByRole("menuitem", { name: "Settings" }))
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings/general"))
    expect(await screen.findByRole("heading", { level: 2, name: "Settings" })).toBeInTheDocument()
  })
})

describe("the Settings screen", () => {
  // From md up the sections are tabs across the top.
  const sections = async () => {
    const tabs = await screen.findByRole("tablist", { name: "Settings" })
    return within(tabs).getAllByRole("tab").map((t) => t.textContent)
  }

  it("has every section but Billing for a member, General open on a desktop", async () => {
    signedIn(MEMBER)
    const router = renderApp("/settings")
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings/general"))
    expect(await sections()).toEqual(["General", "Chats", "Connections", "Prompts", "Digests", "API keys", "Security"])
    const tabs = screen.getByRole("tablist", { name: "Settings" })
    expect(within(tabs).getByRole("tab", { name: "General" })).toHaveAttribute("aria-selected", "true")
    expect(await screen.findByRole("heading", { level: 4, name: "Profile" })).toBeInTheDocument()
  })

  it("adds Billing for whoever may see Plan & billing", async () => {
    signedIn({ ...ADMIN, billing: BILLING })
    renderApp("/settings/general")
    expect(await sections()).toEqual([
      "General",
      "Chats",
      "Connections",
      "Prompts",
      "Digests",
      "API keys",
      "Billing",
      "Security",
    ])
  })

  it("sends a section the account can't see, or doesn't exist, back to Settings", async () => {
    signedIn(MEMBER)
    const router = renderApp("/settings/billing")
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings/general"))
    router.navigate("/settings/nope")
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings/general"))
  })

  it("lets an admin viewing as HR change the chat settings; a member only reads them", async () => {
    signedIn(MEMBER)
    renderApp("/settings/chats")
    expect(await screen.findByText("For 1 year")).toBeInTheDocument()
    expect(screen.getByText("Your workspace's admins set these.")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Change" })).not.toBeInTheDocument()
  })

  it("asks before signing out everywhere", async () => {
    const calls: string[] = []
    signedIn(MEMBER, {
      "POST /api/auth/logout-everywhere": () => {
        calls.push("everywhere")
        return { ok: true, ended: 2 }
      },
    })
    const router = renderApp("/settings/security")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Sign out everywhere" }))
    const confirm = await screen.findByRole("alertdialog", { name: "Sign out everywhere?" })
    expect(calls).toEqual([])
    await user.click(within(confirm).getByRole("button", { name: "Sign out everywhere" }))
    await waitFor(() => expect(calls).toEqual(["everywhere"]))
    await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  })

  it("is a list of the sections on a phone, each a page with its way back", async () => {
    restore = asPhone()
    signedIn(MEMBER)
    const router = renderApp("/settings")
    const nav = await screen.findByRole("navigation", { name: "Settings" })
    expect(router.state.location.pathname).toBe("/settings")
    await userEvent.setup().click(within(nav).getByRole("link", { name: "Prompts" }))
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings/prompts"))
    // The section's own name is the title; "← Settings" goes back to the list.
    expect(await screen.findByRole("heading", { level: 2, name: "Prompts" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Back to Settings" })).toHaveAttribute("href", "/settings")
    expect(await screen.findByText("No saved prompts yet")).toBeInTheDocument()
  })
})

describe("the personal screens' old addresses", () => {
  it.each([
    ["/prompts", "/settings/prompts"],
    ["/api-keys", "/settings/api-keys"],
    ["/digests", "/settings/digests"],
    ["/connections?connected=gmail&choose=1", "/settings/connections"],
    ["/billing?checkout=cancelled", "/settings/billing"],
  ])("%s goes to %s, keeping its query", async (from, to) => {
    signedIn({ ...ADMIN, billing: BILLING })
    const router = renderApp(from)
    await waitFor(() => expect(router.state.location.pathname).toBe(to))
    const query = from.split("?")[1]
    if (query === "checkout=cancelled") expect(router.state.location.search).toBe(`?${query}`)
  })

  it("the connections redirect keeps Google's answer for the section, which then clears it", async () => {
    signedIn(MEMBER, { "GET /api/me/integrations": { integrations: [] } })
    const router = renderApp("/connections?error=consent_denied")
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings/connections"))
    await waitFor(() => expect(router.state.location.search).toBe(""))
    expect(await screen.findByText("Nothing to connect yet")).toBeInTheDocument()
  })
})
