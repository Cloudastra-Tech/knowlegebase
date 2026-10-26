import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ApiKeysView } from "@/components/api-keys-view"
import { mockApi, withProviders } from "@/test/render"

const SETTINGS = {
  api_enabled: true,
  mcp_enabled: true,
  mcp_tools: ["search_knowledge", "ask", "list_sources"],
  updated_by: null,
  updated_at: null,
}
const KEY = {
  id: 7,
  name: "Laptop",
  kind: "personal",
  prefix: "abcdef123456",
  hint: "kb_live_abcdef123456_…",
  scopes: ["ask", "search"],
  created_by: "pat@acme.example",
  created_at: 1_700_000_000,
  last_used_at: null,
  expires_at: Date.now() / 1000 + 30 * 86400,
  revoked_at: null,
  revoked_by: null,
  status: "active",
}
const MINE = {
  keys: [] as unknown[],
  scopes: ["ask", "search", "docs:read"],
  all_scopes: ["ask", "search", "docs:read", "docs:write", "audit:read"],
  expiry_days: [7, 30, 90, 365],
  default_days: 90,
  max_keys: 10,
  settings: SETTINGS,
  api_url: "https://kb.example/api/v1",
  mcp_url: "https://kb.example/mcp",
  openapi_url: "https://kb.example/api/v1/openapi.json",
}

describe("ApiKeysView", () => {
  it("makes a key, shows it once with MCP snippets, and lists it without it", async () => {
    const secret = "kb_live_abcdef123456_" + "s".repeat(43)
    let made = false
    const fetch = mockApi({
      "GET /api/me/api-keys": () => ({ ...MINE, keys: made ? [KEY] : [] }),
      "POST /api/me/api-keys": (body: unknown) => {
        made = true
        expect(body).toEqual({ name: "Laptop", scopes: ["ask", "search"], days: 90 })
        return { ...KEY, key: secret }
      },
    })
    render(withProviders(<ApiKeysView />))
    const user = userEvent.setup()

    expect(await screen.findByText(/No keys yet/)).toBeInTheDocument()
    expect(screen.getAllByText("https://kb.example/mcp").length).toBeGreaterThan(0)
    // Only the scopes the role may give are offered.
    await user.click(screen.getByRole("button", { name: /New key/ }))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).queryByText("Read the audit log")).not.toBeInTheDocument()
    await user.type(within(dialog).getByLabelText("Name"), "Laptop")
    await user.click(within(dialog).getByRole("button", { name: /Make key/ }))

    const shown = await screen.findByRole("dialog", { name: "Your new key" })
    expect(within(shown).getAllByText(secret, { exact: false }).length).toBeGreaterThan(0)
    expect(within(shown).getByText(/claude mcp add --transport http/)).toBeInTheDocument()
    await user.click(within(shown).getByRole("button", { name: "Done" }))

    expect(await screen.findByText("Laptop")).toBeInTheDocument()
    expect(screen.getByText("kb_live_abcdef123456_…")).toBeInTheDocument()
    expect(screen.queryByText(secret, { exact: false })).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalled()
  })

  it("revokes a key after asking", async () => {
    let revoked = false
    mockApi({
      "GET /api/me/api-keys": () => ({
        ...MINE,
        keys: [revoked ? { ...KEY, status: "revoked", revoked_at: 1 } : KEY],
      }),
      "DELETE /api/me/api-keys/7": () => {
        revoked = true
        return { ...KEY, status: "revoked" }
      },
    })
    render(withProviders(<ApiKeysView />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Revoke" }))
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Revoke" }))
    expect(await screen.findByText("Revoked")).toBeInTheDocument()
  })
})
