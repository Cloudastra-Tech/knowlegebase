import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { SlackAppCard } from "@/components/slack-app-card"
import type { SlackApp } from "@/lib/api"
import { json, mockApi, withProviders } from "@/test/render"

const URLS = {
  events: "https://kb.example.com/api/slack/events",
  commands: "https://kb.example.com/api/slack/commands",
  interactions: "https://kb.example.com/api/slack/interactions",
  redirect: "https://kb.example.com/api/slack/oauth/callback",
}
const READY: SlackApp = {
  status: "ready",
  can_manage: true,
  central_available: true,
  mode: "central",
  client_id: null,
  has_app_secrets: false,
  team_id: null,
  team_name: null,
  installed_by: null,
  installed_at: null,
  unknown_users: "refuse",
  auto_answer: false,
  auto_answer_channels: [],
  scopes: ["app_mentions:read", "chat:write"],
  urls: URLS,
}
const INSTALLED: SlackApp = {
  ...READY,
  status: "installed",
  team_id: "T1",
  team_name: "Acme Slack",
  installed_by: "admin@acme.com",
  installed_at: Date.now() / 1000 - 60,
}

describe("SlackAppCard", () => {
  it("installs with this server's app, through Slack's consent screen", async () => {
    let installs = 0
    mockApi({
      "GET /api/admin/slack": READY,
      "POST /api/admin/slack/install": () => {
        installs += 1
        return json({ detail: "Choose the Slack app first." }, 400)
      },
    })
    render(withProviders(<SlackAppCard />))
    expect(await screen.findByText("Not installed")).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "This server's app" })).toBeChecked()
    await userEvent.click(screen.getByRole("button", { name: "Add to Slack" }))
    expect(installs).toBe(1)
    // Refused (the reason is a toast): the button works again.
    await waitFor(() => expect(screen.getByRole("button", { name: "Add to Slack" })).toBeEnabled())
  })

  it("sets up the workspace's own app from the manifest", async () => {
    const sent: unknown[] = []
    mockApi({
      "GET /api/admin/slack": { ...READY, central_available: false },
      "GET /api/admin/slack/manifest": {
        manifest: "display_information:\n  name: Knowledge\n",
        filename: "m.yml",
      },
      "PUT /api/admin/slack/app": (body: unknown) => {
        sent.push(body)
        return { ...READY, mode: "custom", client_id: "1.2" }
      },
    })
    render(withProviders(<SlackAppCard />))
    expect(await screen.findByRole("radio", { name: /This server's app/ })).toBeDisabled()
    await userEvent.click(screen.getByRole("radio", { name: /Your own app/ }))
    expect(await screen.findByText(/name: Knowledge/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Client ID"), "1234567890.1234567890")
    await userEvent.type(screen.getByLabelText("Client Secret"), "clientsecretvalue1")
    await userEvent.type(screen.getByLabelText("Signing Secret"), "signingsecretvalue")
    await userEvent.click(screen.getByRole("button", { name: "Save app" }))
    expect(sent).toEqual([
      {
        mode: "custom",
        client_id: "1234567890.1234567890",
        client_secret: "clientsecretvalue1",
        signing_secret: "signingsecretvalue",
      },
    ])
  })

  it("shows an installed app with its settings, and uninstalls after asking", async () => {
    const settings: unknown[] = []
    let uninstalled = false
    mockApi({
      "GET /api/admin/slack": INSTALLED,
      "PUT /api/admin/slack/settings": (body: unknown) => {
        settings.push(body)
        return { ...INSTALLED, unknown_users: "public" }
      },
      "DELETE /api/admin/slack/install": () => {
        uninstalled = true
        return READY
      },
    })
    render(withProviders(<SlackAppCard />))
    expect(await screen.findByText("Acme Slack")).toBeInTheDocument()
    expect(screen.getByText(/use public documents only/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("radio", { name: "Answer from public documents only" }))
    expect(settings).toEqual([{ unknown_users: "public" }])
    await userEvent.click(screen.getByRole("button", { name: /Uninstall/ }))
    await userEvent.click(await screen.findByRole("button", { name: "Uninstall" }))
    expect(uninstalled).toBe(true)
    expect(await screen.findByText("Not installed")).toBeInTheDocument()
  })

  it("is read-only for HR who aren't admins", async () => {
    mockApi({ "GET /api/admin/slack": { ...READY, can_manage: false } })
    render(withProviders(<SlackAppCard />))
    expect(
      await screen.findByText("An admin installs the Slack app for this workspace."),
    ).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Add to Slack" })).not.toBeInTheDocument()
  })
})
