import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { JobsSheet, RebuildIndexDialog } from "@/components/index-admin"
import { mockApi, withProviders } from "@/test/render"

const JOBS = [
  {
    id: 3,
    kind: "sync",
    status: "running",
    attempts: 1,
    max_attempts: 3,
    details: { provider: "google_drive" },
    progress: "Fetching changes",
    error: null,
    reason: null,
    created_at: 100,
    started_at: 101,
    finished_at: null,
  },
  {
    id: 2,
    kind: "index",
    status: "failed",
    attempts: 3,
    max_attempts: 3,
    details: { full: true },
    progress: null,
    error: "AuthenticationError: Error code: 401 - Incorrect API key provided",
    reason: "ai_key_rejected",
    created_at: 50,
    started_at: 51,
    finished_at: 60,
  },
]

describe("JobsSheet", () => {
  it("lists the workspace's jobs with their status and why one failed", async () => {
    mockApi({ "GET /api/admin/jobs": { jobs: JOBS } })
    render(withProviders(<JobsSheet open onOpenChange={() => {}} />))

    const list = await screen.findByRole("list", { name: "Jobs" })
    const [sync, index] = within(list).getAllByRole("listitem")
    expect(within(sync).getByText("Syncing a source")).toBeInTheDocument()
    expect(within(sync).getByText("Running")).toBeInTheDocument()
    expect(within(sync).getByText("google drive")).toBeInTheDocument()
    expect(within(sync).getByText("Fetching changes")).toBeInTheDocument()
    expect(within(index).getByText("Failed")).toBeInTheDocument()
    expect(within(index).getByText("from scratch · try 3 of 3")).toBeInTheDocument()
    expect(
      within(index).getByText("Indexing paused: the AI provider key was rejected."),
    ).toBeInTheDocument()
    // The raw error is one click further.
    expect(within(index).queryByText(/Incorrect API key/)).not.toBeInTheDocument()
    await userEvent.setup().click(within(index).getByRole("button", { name: "Error details" }))
    expect(within(index).getByText(/Incorrect API key/)).toBeInTheDocument()
  })
})

describe("RebuildIndexDialog", () => {
  it("warns about the cost, then asks for a full rebuild", async () => {
    const fetch = mockApi({ "POST /api/reindex": { status: "indexing" } })
    const started = vi.fn()
    render(
      withProviders(
        <RebuildIndexDialog open onOpenChange={() => {}} documents={12} onStarted={started} />,
      ),
    )
    expect(screen.getByText(/costs about as much as indexing them the first time/)).toBeInTheDocument()
    expect(screen.getByText(/12 uploaded documents/)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: "Rebuild from scratch" }))
    await vi.waitFor(() => expect(started).toHaveBeenCalled())
    expect(String(fetch.mock.calls[0][0])).toBe("/api/reindex?full=true")
  })
})
