import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { NotFoundQuestions } from "@/components/insights-not-found"
import { mockApi, withProviders } from "@/test/render"

const now = Date.now() / 1000

const OPEN = {
  from: "2026-09-28",
  to: "2026-10-27",
  text_kept: true,
  hidden: 2,
  questions: [
    { id: 7, question: "How long is parental leave?", at: now - 60, day: "2026-10-27",
      surface: "slack", language: "en", resolved_at: null },
  ],
}

describe("NotFoundQuestions", () => {
  it("lists open questions, marks one resolved and drafts a doc for one", async () => {
    let resolved = false
    const fetch = mockApi({
      "GET /api/admin/insights/not-found": () =>
        resolved ? { ...OPEN, questions: [], hidden: 0 } : OPEN,
      "POST /api/admin/insights/not-found/7/resolve": () => {
        resolved = true
        return { ok: true }
      },
    })
    const draft = vi.fn()
    render(withProviders(<NotFoundQuestions onDraftDoc={draft} />))

    const list = await screen.findByRole("list", { name: "Unanswered questions" })
    const row = within(list).getByRole("listitem")
    expect(within(row).getByText("How long is parental leave?")).toBeInTheDocument()
    expect(within(row).getByText(/Slack/)).toBeInTheDocument()
    expect(screen.getByText(/2 more questions reached documents your role can't see/)).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(within(row).getByRole("button", { name: "Draft a doc" }))
    expect(draft).toHaveBeenCalledWith(7)

    await user.click(within(row).getByRole("button", { name: "Mark resolved" }))
    expect(await screen.findByText("No unanswered questions in these days.")).toBeInTheDocument()
    expect(fetch.mock.calls.some(([url, init]) =>
      String(url).endsWith("/not-found/7/resolve") && init?.method === "POST")).toBe(true)
  })

  it("asks for the resolved ones, which can be reopened", async () => {
    const fetch = mockApi({
      "GET /api/admin/insights/not-found": OPEN,
    })
    render(withProviders(<NotFoundQuestions />))
    await screen.findByText("How long is parental leave?")
    await userEvent.setup().click(screen.getByRole("radio", { name: "Resolved" }))
    expect(await screen.findByRole("button", { name: "Reopen" })).toBeInTheDocument()
    expect(String(fetch.mock.calls.at(-1)?.[0])).toContain("resolved=true")
  })

  it("says why there's no text under metadata-only logging", async () => {
    mockApi({
      "GET /api/admin/insights/not-found": {
        ...OPEN,
        text_kept: false,
        hidden: 0,
        questions: [{ ...OPEN.questions[0], question: null }],
      },
    })
    render(withProviders(<NotFoundQuestions onDraftDoc={() => {}} />))
    expect(await screen.findByText(/logged without their text/)).toBeInTheDocument()
    expect(screen.getByText("Question text isn't kept")).toBeInTheDocument()
    // Nothing to draft from.
    expect(screen.queryByRole("button", { name: "Draft a doc" })).not.toBeInTheDocument()
  })
})
