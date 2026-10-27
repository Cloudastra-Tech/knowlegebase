import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ExtensionCard } from "@/components/extension-card"
import type { ExtensionStatus } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const NOT_SET_UP: ExtensionStatus = { configured: false, api_enabled: true, connected: [], key_days: 90 }
const CONNECTED: ExtensionStatus = {
  configured: true,
  api_enabled: true,
  key_days: 90,
  connected: [
    {
      id: 7,
      name: "Browser extension",
      hint: "kb_live_abc_…",
      created_at: Date.now() / 1000 - 3600,
      last_used_at: null,
      expires_at: Date.now() / 1000 + 86400,
    },
  ],
}

describe("ExtensionCard", () => {
  it("says when the server doesn't accept the extension yet", async () => {
    mockApi({ "GET /api/extension": NOT_SET_UP })
    render(withProviders(<ExtensionCard />))
    expect(await screen.findByText("Not set up")).toBeInTheDocument()
    expect(screen.getByText(/EXTENSION_ORIGINS/)).toBeInTheDocument()
  })

  it("disconnects a connected extension by revoking its key", async () => {
    let status = CONNECTED
    const revoked: string[] = []
    mockApi({
      "GET /api/extension": () => status,
      "DELETE /api/me/api-keys/7": () => {
        revoked.push("7")
        status = { ...CONNECTED, connected: [] }
        return { id: 7 }
      },
    })
    render(withProviders(<ExtensionCard />))
    expect(await screen.findByText("Connected")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: /Disconnect/ }))
    await waitFor(() => expect(revoked).toEqual(["7"]))
    expect(await screen.findByText("Not connected")).toBeInTheDocument()
  })
})
