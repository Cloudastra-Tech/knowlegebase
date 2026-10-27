import { useState } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { AccessPicker } from "@/components/access-picker"
import { IntegrationsView } from "@/components/integrations-view"
import type { Integration } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const PRINCIPALS = [
  { principal: "everyone", kind: "everyone", label: "Everyone in the workspace" },
  { principal: "group:hr", kind: "group", label: "HR & Admin", source: "builtin" },
  { principal: "group:g_fin", kind: "group", label: "Finance", source: "manual" },
  { principal: "user:ann@acme.com", kind: "user", label: "Ann", detail: "ann@acme.com" },
]

function Picker({ seen }: { seen: string[][] }) {
  const [acl, setAcl] = useState<string[]>(["everyone"])
  return (
    <AccessPicker
      id="acl"
      value={acl}
      onChange={(next) => {
        seen.push(next)
        setAcl(next)
      }}
    />
  )
}

describe("AccessPicker", () => {
  it("swaps everyone for groups and people, and back", async () => {
    mockApi({ "GET /api/access/principals": { principals: PRINCIPALS } })
    const seen: string[][] = []
    render(withProviders(<Picker seen={seen} />))
    const user = userEvent.setup()
    await user.click(screen.getByRole("combobox"))
    await user.click(await screen.findByRole("option", { name: /Finance/ }))
    await user.click(screen.getByRole("option", { name: /Ann/ }))
    expect(seen).toEqual([["group:g_fin"], ["group:g_fin", "user:ann@acme.com"]])
    await user.click(screen.getByRole("option", { name: /Everyone in the workspace/ }))
    expect(seen.at(-1)).toEqual(["everyone"])
    // Never empty: taking away the last one leaves everyone.
    await user.click(screen.getByRole("option", { name: /Finance/ }))
    await user.click(screen.getByRole("option", { name: /Finance/ }))
    expect(seen.at(-1)).toEqual(["everyone"])
  })
})

describe("the permission policy", () => {
  it("follows the source's sharing, and syncs permissions on request", async () => {
    const sent: unknown[] = []
    const drive: Integration = {
      id: "google_drive",
      name: "Google Drive",
      availability: "live",
      status: "connected",
      auth: "oauth",
      picker: "none",
      acl: true,
      access: "restricted",
      policy: "restricted",
      policies: ["source", "workspace", "restricted"],
      containers: [],
    }
    mockApi({
      "GET /api/integrations": { workspace: "acme", integrations: [drive] },
      "PUT /api/integrations/google_drive/policy": (body: unknown) => {
        sent.push(body)
        drive.policy = "source"
        return { policy: "source" }
      },
      "POST /api/integrations/google_drive/permissions/sync": () => {
        sent.push("sync")
        return { ok: true }
      },
      "GET /api/index/status": { status: "idle", step: 0, total: 4, message: "", pending: false,
                                 summary: null, error: null, finished_at: null },
    })
    render(withProviders(<IntegrationsView onOpenDocuments={() => {}} />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("radio", { name: /As shared in Google Drive/ }))
    expect(sent).toEqual([{ policy: "source" }])
    await user.click(await screen.findByRole("button", { name: "Sync permissions now" }))
    expect(sent).toEqual([{ policy: "source" }, "sync"])
  })
})
