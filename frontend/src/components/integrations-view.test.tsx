import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

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

  it("connects Confluence Data Center with its own form and says SharePoint needs an admin", async () => {
    const sent: unknown[] = []
    const items: Integration[] = [
      { ...base, id: "confluence_dc", name: "Confluence Data Center", auth: "token", picker: "list", status: "disconnected" },
      {
        ...base,
        id: "sharepoint",
        name: "SharePoint & OneDrive",
        auth: "oauth",
        picker: "tree",
        picker_required: true,
        status: "disconnected",
        note: "Needs a Microsoft 365 admin: they approve read-only access for the organisation when connecting.",
      },
    ]
    mockApi({
      "GET /api/integrations": { workspace: "acme", integrations: items },
      "POST /api/integrations/confluence_dc/setup": (body: unknown) => {
        sent.push(body)
        return { ok: true, account: "confluence.acme.com (Ann)", choose: true }
      },
    })
    render(withProviders(<IntegrationsView onOpenDocuments={() => {}} />))
    const user = userEvent.setup()
    expect(await screen.findByText(/Needs a Microsoft 365 admin/)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Connect Confluence Data Center" }))
    expect(screen.queryByText("Behind Cloudflare Access?")).not.toBeInTheDocument()
    await user.type(screen.getByLabelText("Confluence address"), "https://confluence.acme.com")
    await user.type(screen.getByLabelText("Personal access token"), "pat")
    await user.click(screen.getByRole("button", { name: "Connect" }))
    await screen.findByText("Confluence Data Center")
    expect(sent[0]).toEqual({ url: "https://confluence.acme.com", token: "pat" })
  })

  it("connects Zendesk with an email and token, or signs in on its subdomain", async () => {
    const sent: unknown[] = []
    const signIns: unknown[] = []
    const items: Integration[] = [
      { ...base, id: "zendesk", name: "Zendesk", auth: "token", picker: "tree", status: "disconnected", sign_in: true },
    ]
    mockApi({
      "GET /api/integrations": { workspace: "acme", integrations: items },
      "POST /api/integrations/zendesk/setup": (body: unknown) => {
        sent.push(body)
        return { ok: true, account: "acme.zendesk.com (Ann)", choose: false }
      },
      "POST /api/integrations/zendesk/connect": (body: unknown) => {
        signIns.push(body)
        return new Response(JSON.stringify({ detail: "Enter your Zendesk subdomain." }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        })
      },
    })
    render(withProviders(<IntegrationsView onOpenDocuments={() => {}} />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Connect Zendesk" }))
    await user.type(screen.getByLabelText("Zendesk subdomain"), "acme")
    // Signing in sends the subdomain first; the server's reason is shown as is.
    await user.click(screen.getByRole("button", { name: "Sign in with Zendesk" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter your Zendesk subdomain.")
    expect(signIns[0]).toEqual({ url: "acme" })
    await user.type(screen.getByLabelText("Agent or admin email"), "ann@acme.com")
    await user.type(screen.getByLabelText("API token"), "tok")
    await user.click(screen.getByRole("button", { name: "Connect" }))
    await screen.findByText("Zendesk")
    expect(sent[0]).toEqual({ url: "acme", email: "ann@acme.com", token: "tok" })
  })

  it("keeps code per GitHub repository and locks access while Zendesk tickets sync", async () => {
    const saved: unknown[] = []
    const items: Integration[] = [
      {
        ...base,
        id: "github",
        name: "GitHub",
        auth: "oauth",
        picker: "list",
        picker_required: true,
        status: "connected",
        unit: "files",
        containers: [{ id: "acme/handbook", name: "acme/handbook", kind: "repo", code: false }],
        options: { code_extensions: [".py"] },
      },
      {
        ...base,
        id: "zendesk",
        name: "Zendesk",
        auth: "token",
        picker: "tree",
        status: "connected",
        access: "restricted",
        access_locked: true,
        options: { tickets: true },
        containers: [],
      },
    ]
    mockApi({
      "GET /api/integrations": { workspace: "acme", integrations: items },
      "GET /api/integrations/github/containers": {
        items: [
          { id: "acme/api", name: "acme/api", kind: "repo", private: false },
          { id: "acme/handbook", name: "acme/handbook", kind: "repo", private: true },
        ],
        picked: [],
      },
      "PUT /api/integrations/github/containers": (body: unknown) => {
        saved.push(body)
        return { containers: [] }
      },
    })
    render(withProviders(<IntegrationsView onOpenDocuments={() => {}} />))
    const user = userEvent.setup()
    expect(await screen.findByText(/Locked to HR and admins while Zendesk tickets/)).toBeInTheDocument()
    expect(screen.getByText("synced (HR and admins only)")).toBeInTheDocument()
    const everyone = screen.getAllByRole("radio", { name: /Everyone in the workspace/ })
    expect(everyone.map((r) => r.hasAttribute("disabled"))).toEqual([false, true])

    await user.click(screen.getAllByRole("button", { name: "Edit selection" })[0])
    await user.click(await screen.findByRole("checkbox", { name: "Sync acme/api" }))
    await user.click(screen.getByRole("switch", { name: "Also sync code from acme/api" }))
    expect(screen.getByLabelText("Code file types")).toHaveValue(".py")
    await user.click(screen.getByRole("button", { name: "Save and sync" }))
    await vi.waitFor(() => expect(saved).toHaveLength(1))
    expect(saved[0]).toEqual({
      containers: [
        { id: "acme/handbook", name: "acme/handbook", kind: "repo", code: false },
        { id: "acme/api", name: "acme/api", kind: "repo", private: false, code: true },
      ],
      options: { code_extensions: [".py"] },
    })
  })
})
