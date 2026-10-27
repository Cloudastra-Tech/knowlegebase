import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { WorkspaceDataView } from "@/components/workspace-data-view"
import type { WorkspaceSettings } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const ACTIVE: WorkspaceSettings = {
  slug: "acme",
  name: "Acme Corp",
  status: "active",
  deleting_at: null,
  delete_requested_at: null,
  delete_requested_by: null,
  support_until: null,
  support_granted_by: null,
  support_hours: [24, 72, 168],
  grace_days: 7,
  audit_days: null,
  audit_day_choices: [365, 730, 1095, 2555],
  export_hours: 24,
  exports: [],
  can_delete: true,
  support_session: false,
}
const DELETING: WorkspaceSettings = {
  ...ACTIVE,
  status: "deleting",
  deleting_at: Date.now() / 1000 + 7 * 86400,
  delete_requested_by: "admin@acme.com",
}
const COMMON = {
  "GET /api/chat-settings": {
    retention_days: 365,
    sharing_enabled: true,
    updated_by: null,
    updated_at: null,
    retention_choices: [30, 90, 365, null],
  },
  "GET /api/question-log/settings": { mode: "redacted", days: 90, logged: true },
}

describe("WorkspaceDataView", () => {
  it("deletes only once the workspace's name is typed", async () => {
    const asked: unknown[] = []
    mockApi({
      ...COMMON,
      "GET /api/admin/workspace": ACTIVE,
      "DELETE /api/admin/workspace": (body: unknown) => {
        asked.push(body)
        return DELETING
      },
    })
    render(withProviders(<WorkspaceDataView />))
    await userEvent.click(await screen.findByRole("button", { name: "Delete workspace" }))
    const dialog = await screen.findByRole("alertdialog")
    const confirm = within(dialog).getByRole("button", { name: "Delete in 7 days" })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/to confirm/), "Acme")
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/to confirm/), " Corp")
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    await waitFor(() => expect(asked).toEqual([{ confirm: "Acme Corp" }]))
    expect(await screen.findByText("This workspace is being deleted")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Cancel the deletion" })).toBeEnabled()
  })

  it("shows support access and lets the admin end it", async () => {
    let revoked = 0
    mockApi({
      ...COMMON,
      "GET /api/admin/workspace": {
        ...ACTIVE,
        support_until: Date.now() / 1000 + 3600,
        support_granted_by: "admin@acme.com",
      },
      "DELETE /api/admin/workspace/support-access": () => {
        revoked += 1
        return ACTIVE
      },
    })
    render(withProviders(<WorkspaceDataView />))
    expect(await screen.findByText(/Allowed until/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "End access now" }))
    await waitFor(() => expect(revoked).toBe(1))
    expect(await screen.findByRole("button", { name: /Allow support/ })).toBeInTheDocument()
  })

  it("is read-only for Cloudastra support", async () => {
    mockApi({ ...COMMON, "GET /api/admin/workspace": { ...ACTIVE, support_session: true } })
    render(withProviders(<WorkspaceDataView />))
    expect(await screen.findByRole("button", { name: "Delete workspace" })).toBeDisabled()
    expect(screen.getByRole("button", { name: /Export workspace/ })).toBeDisabled()
  })
})
