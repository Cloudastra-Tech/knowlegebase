import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"

import { IndexStatusText } from "@/components/index-progress"
import type { IndexStatus } from "@/lib/api"
import { indexProblem } from "@/lib/index-reasons"
import { IndexStatusContext } from "@/lib/index-status-context"

const RAW = "OpenAIError: Missing credentials. Please pass an `api_key`, or set the OPENAI_API_KEY environment variable."

const FAILED: IndexStatus = {
  status: "error",
  step: 0,
  total: 4,
  message: "Indexing failed",
  pending: false,
  summary: null,
  error: RAW,
  reason: "no_ai_key",
  finished_at: 1,
}

function renderStatus(status: IndexStatus, admin: boolean) {
  render(
    <MemoryRouter>
      <IndexStatusContext.Provider value={{ status, justFinished: false, refresh: () => {} }}>
        <IndexStatusText admin={admin} />
      </IndexStatusContext.Provider>
    </MemoryRouter>,
  )
}

describe("indexProblem", () => {
  it("words each reason plainly, never with the exception", () => {
    expect(indexProblem(FAILED)).toEqual({
      short: "Indexing paused",
      sentence: "Indexing paused: no AI provider key set.",
    })
    expect(indexProblem({ ...FAILED, reason: "unknown" })?.sentence).toBe("Indexing failed.")
    // Waiting to try again after a failure.
    expect(
      indexProblem({ ...FAILED, status: "indexing", reason: "ai_quota", message: "Trying again soon" })
        ?.sentence,
    ).toBe("Indexing paused: the AI provider's quota or rate limit was reached. Trying again soon.")
    expect(indexProblem({ ...FAILED, status: "indexing", reason: null })).toBeNull()
    expect(indexProblem({ ...FAILED, status: "idle", reason: null, error: null })).toBeNull()
  })
})

describe("IndexStatusText", () => {
  it("shows a member the plain status only", async () => {
    renderStatus({ ...FAILED, error: null }, false)
    const status = screen.getByRole("button", { name: "Indexing paused" })
    expect(screen.queryByText(/Missing credentials/)).not.toBeInTheDocument()
    await userEvent.setup().click(status)
    expect(await screen.findByText("Indexing paused: no AI provider key set.")).toBeInTheDocument()
    expect(screen.queryByText(/OpenAIError/)).not.toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("gives an admin the error's text and the jobs, behind the status", async () => {
    renderStatus(FAILED, true)
    expect(screen.queryByText(/OpenAIError/)).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: "Indexing paused" }))
    expect(await screen.findByText(RAW)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "See the background jobs" })).toHaveAttribute(
      "href",
      "/documents?jobs=1",
    )
  })
})
