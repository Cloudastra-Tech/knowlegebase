import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { TeamsAppCard } from "@/components/teams-app-card"
import type { TeamsApp } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const BOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
const TENANT = "11111111-2222-3333-4444-555555555555"
const NOT_SET: TeamsApp = {
  status: "not_configured",
  can_manage: true,
  central_available: false,
  mode: "custom",
  app_id: null,
  app_tenant: null,
  tenant_id: null,
  has_secret: null,
  bot_app_id: null,
  last_activity_at: null,
  updated_by: null,
  updated_at: null,
  unknown_users: "refuse",
  messaging_endpoint: "https://kb.example.com/api/teams/messages",
}
const CONNECTED: TeamsApp = {
  ...NOT_SET,
  status: "connected",
  app_id: BOT,
  app_tenant: TENANT,
  tenant_id: TENANT,
  has_secret: true,
  bot_app_id: BOT,
  last_activity_at: Date.now() / 1000 - 60,
  messaging_endpoint: "https://kb.example.com/api/teams/apps/k1/messages",
}

describe("TeamsAppCard", () => {
  it("connects the workspace's own Azure Bot", async () => {
    const sent: unknown[] = []
    mockApi({
      "GET /api/admin/teams": NOT_SET,
      "PUT /api/admin/teams/app": (body: unknown) => {
        sent.push(body)
        return { ...CONNECTED, status: "configured", last_activity_at: null }
      },
    })
    render(withProviders(<TeamsAppCard />))
    expect(await screen.findByText("Not set up")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: /Connect your bot/ }))
    await userEvent.type(screen.getByLabelText("Microsoft App ID"), BOT)
    await userEvent.type(screen.getByLabelText("App Tenant ID"), TENANT)
    await userEvent.type(screen.getByLabelText("Client secret value"), "a-secret-value-123")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(sent).toEqual([
        { mode: "custom", app_id: BOT, app_tenant: TENANT, client_secret: "a-secret-value-123" },
      ]),
    )
    expect(await screen.findByText("Waiting for Teams")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Download Teams app/ })).toBeInTheDocument()
  })

  it("shows the tenant and who gets answered once connected", async () => {
    const sent: unknown[] = []
    mockApi({
      "GET /api/admin/teams": CONNECTED,
      "PUT /api/admin/teams/settings": (body: unknown) => {
        sent.push(body)
        return { ...CONNECTED, unknown_users: "public" }
      },
    })
    render(withProviders(<TeamsAppCard />))
    expect(await screen.findByText("Connected")).toBeInTheDocument()
    expect(screen.getByText(TENANT)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("radio", { name: "Answer from public documents only" }))
    await waitFor(() => expect(sent).toEqual([{ unknown_users: "public" }]))
  })

  it("is read-only for HR", async () => {
    mockApi({ "GET /api/admin/teams": { ...NOT_SET, can_manage: false } })
    render(withProviders(<TeamsAppCard />))
    expect(
      await screen.findByText("An admin connects Microsoft Teams for this workspace."),
    ).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Connect your bot/ })).toBeNull()
  })
})
