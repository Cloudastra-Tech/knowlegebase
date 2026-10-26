import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { IntegrationsView } from "@/components/integrations-view"
import type { Integration } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const base = { availability: "live", picker: "none", unit: "items" } as const

const ITEMS: Integration[] = [
  { id: "uploads", name: "Uploaded files", status: "connected", availability: "live", doc_count: 4 },
  {
    ...base,
    id: "google_drive",
    name: "Google Drive",
    description: "Docs, Sheets, Slides, PDFs, Office and text files",
    auth: "oauth",
    picker: "tree",
    picker_required: true,
    unit: "files",
    icon: "googledrive",
    status: "error",
    account: "ann@acme.com",
    doc_count: 12,
    error: { code: "auth_expired", message: "Access was revoked or has expired. Reconnect to keep syncing." },
    containers: [{ id: "f1", name: "Handbook", kind: "folder" }],
  },
  {
    ...base,
    id: "notion",
    name: "Notion",
    auth: "oauth",
    picker: "list",
    unit: "pages",
    status: "connected",
    doc_count: 30,
    held_deletes: 14,
    containers: [],
  },
  { ...base, id: "outline", name: "Outline", auth: "token", picker: "list", status: "disconnected" },
  { ...base, id: "website", name: "Website", auth: "url", picker: "urls", status: "disconnected" },
  { id: "confluence", name: "Confluence", icon: "confluence", availability: "soon", status: "unavailable" },
  { id: "slack", name: "Slack", availability: "soon", status: "unavailable" },
]

describe("IntegrationsView", () => {
  it("draws every card from the registry, with errors in plain words", async () => {
    mockApi({ "GET /api/integrations": { workspace: "acme", integrations: ITEMS } })
    render(withProviders(<IntegrationsView onOpenDocuments={() => {}} />))

    expect(await screen.findByText("Google Drive")).toBeInTheDocument()
    expect(
      screen.getByText("Access was revoked or has expired. Reconnect to keep syncing."),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeInTheDocument()
    expect(screen.getByText("Handbook")).toBeInTheDocument()
    // The mass-delete guard asks before removing anything.
    expect(screen.getByText("14 pages would be removed")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Connect Outline" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add a website" })).toBeInTheDocument()
    // "Coming soon" comes from the same list.
    const soon = screen.getByText("Coming soon").parentElement!
    expect(within(soon).getByText("Confluence")).toBeInTheDocument()
    expect(within(soon).getByText("Slack")).toBeInTheDocument()
  })

  it("connects a token connector with its form and shows the server's reason", async () => {
    const sent: unknown[] = []
    mockApi({
      "GET /api/integrations": { workspace: "acme", integrations: ITEMS },
      "POST /api/integrations/outline/setup": (body: unknown) => {
        sent.push(body)
        return new Response(JSON.stringify({ detail: "Outline didn't accept that token." }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        })
      },
    })
    render(withProviders(<IntegrationsView onOpenDocuments={() => {}} />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Connect Outline" }))
    await user.type(screen.getByLabelText("Wiki address"), "https://wiki.acme.com")
    await user.type(screen.getByLabelText("API token"), "secret-token")
    await user.click(screen.getByRole("button", { name: "Connect" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("Outline didn't accept that token.")
    expect(sent[0]).toMatchObject({ url: "https://wiki.acme.com", token: "secret-token" })
  })
})
