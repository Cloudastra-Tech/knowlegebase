import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { MyConnectionsView } from "@/components/my-connections-view"
import type { Integration } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const base = {
  availability: "live",
  auth: "oauth",
  picker: "list",
  picker_required: true,
  personal: true,
} as const

const MINE: Integration[] = [
  {
    ...base,
    id: "gmail",
    name: "Gmail",
    icon: "gmail",
    unit: "threads",
    status: "connected",
    account: "ann@acme.com",
    doc_count: 12,
    containers: [{ id: "Label_7", name: "HR", kind: "label" }],
  },
  {
    ...base,
    id: "google_calendar",
    name: "Google Calendar",
    icon: "googlecalendar",
    unit: "events",
    status: "disconnected",
    note: "Only you can ask about your events.",
  },
]

describe("MyConnectionsView", () => {
  it("shows the member's own connections, theirs alone", async () => {
    mockApi({ "GET /api/me/integrations": { integrations: MINE } })
    render(withProviders(<MyConnectionsView />))
    expect(await screen.findByText("Gmail")).toBeInTheDocument()
    expect(screen.getByText("HR")).toBeInTheDocument()
    expect(screen.getByText(/Only you can ask about these threads/)).toBeInTheDocument()
    expect(screen.getByText("Only you can ask about your events.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Connect your Google Calendar" })).toBeInTheDocument()
  })

  it("picks labels through the member's own endpoints", async () => {
    const saved: unknown[] = []
    mockApi({
      "GET /api/me/integrations": { integrations: MINE },
      "GET /api/me/integrations/gmail/containers": {
        items: [
          { id: "INBOX", name: "Inbox", kind: "label" },
          { id: "Label_7", name: "HR", kind: "label" },
        ],
        picked: [],
      },
      "PUT /api/me/integrations/gmail/containers": (body: unknown) => {
        saved.push(body)
        return { containers: [] }
      },
    })
    render(withProviders(<MyConnectionsView />))
    await userEvent.click(await screen.findByRole("button", { name: "Edit selection" }))
    await userEvent.click(await screen.findByRole("checkbox", { name: "Sync Inbox" }))
    await userEvent.click(screen.getByRole("button", { name: "Save and sync" }))
    await vi.waitFor(() =>
      expect(saved).toEqual([
        {
          containers: [
            { id: "Label_7", name: "HR", kind: "label" },
            { id: "INBOX", name: "Inbox", kind: "label" },
          ],
        },
      ]),
    )
  })
})
