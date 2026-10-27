import { render, screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { SyncHistorySheet } from "@/components/sync-history-sheet"
import type { Integration, SyncRun } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const ITEM: Integration = {
  id: "notion",
  name: "Notion",
  status: "connected",
  availability: "live",
  unit: "pages",
}

const run = (over: Partial<SyncRun>): SyncRun => ({
  id: 1,
  kind: "incremental",
  status: "done",
  started_at: Date.now() / 1000 - 600,
  finished_at: Date.now() / 1000 - 597,
  upserted: 0,
  deleted: 0,
  skipped: 0,
  unchanged: 0,
  held_deletes: 0,
  limit_reached: 0,
  error: null,
  ...over,
})

describe("SyncHistorySheet", () => {
  it("lists the runs as a timeline whose glyphs say their state in words", async () => {
    mockApi({
      "GET /api/integrations/notion/runs": {
        runs: [
          run({ id: 3, status: "running", finished_at: null }),
          run({ id: 2, status: "held", held_deletes: 9 }),
          run({ id: 1, upserted: 34 }),
        ],
        skipped: [{ title: "Huge.pdf", url: null, reason: "too_large" }],
      },
    })
    render(withProviders(<SyncHistorySheet item={ITEM} open onOpenChange={() => {}} />))

    const runs = await screen.findByRole("list", { name: "Syncs" })
    const rows = within(runs).getAllByRole("listitem")
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByRole("status")).toHaveTextContent("Running")
    expect(rows[1]).toHaveTextContent("Held back")
    expect(rows[1]).toHaveTextContent("9 removals held back for an admin to confirm.")
    expect(rows[2]).toHaveTextContent("Done")
    expect(rows[2]).toHaveTextContent("34 added or updated")
    expect(screen.getByRole("heading", { name: "Not synced" })).toBeInTheDocument()
    expect(screen.getByText("Too large to sync")).toBeInTheDocument()
  })

  it("says when there have been no syncs", async () => {
    mockApi({ "GET /api/integrations/notion/runs": { runs: [], skipped: [] } })
    render(withProviders(<SyncHistorySheet item={ITEM} open onOpenChange={() => {}} />))

    expect(await screen.findByRole("heading", { name: "No syncs yet." })).toBeInTheDocument()
  })
})
