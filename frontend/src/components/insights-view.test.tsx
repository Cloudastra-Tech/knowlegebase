import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { InsightsView } from "@/components/insights-view"
import { LoggingNotice } from "@/components/logging-notice"
import { mockApi, withProviders } from "@/test/render"

// What Radix's Select needs and jsdom doesn't have.
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}

const day = (d: string, questions = 0) => ({
  day: d,
  questions,
  active_users: 0,
  answered: questions,
  partial: 0,
  not_found: 0,
  blocked: 0,
  general_knowledge: 0,
  error: 0,
  stopped: 0,
})

const REPORT = {
  from: "2026-10-21",
  to: "2026-10-22",
  today: "2026-10-22",
  logging: { mode: "redacted", days: 90 },
  totals: {
    questions: 40,
    active_users: 7,
    answered: 28,
    partial: 2,
    not_found: 6,
    blocked: 1,
    general_knowledge: 3,
    error: 0,
    stopped: 0,
    tokens_in: 12000,
    tokens_out: 3000,
    cost_usd: 0.0036,
    latency_p50_ms: 2400,
    latency_p95_ms: 7100,
    rates: { answered: 0.75, not_found: 0.15, blocked: 0.025, general_knowledge: 0.075 },
  },
  daily: [day("2026-10-21", 15), day("2026-10-22", 25)],
  top_sources: [
    { doc_key: "public/handbook.txt", title: "handbook.txt", provider: "Company documents", url: null, access: "public", cited: 12 },
  ],
  never_cited: [],
  never_cited_count: 0,
  stale: [],
  gaps: [
    {
      id: 1,
      label: "Parental leave in Germany",
      hidden: false,
      size: 5,
      not_found: 4,
      general_knowledge: 1,
      samples: ["parental leave in Germany?"],
      computed_at: 1,
    },
    { id: 2, label: null, hidden: true, size: 3, not_found: 3, general_knowledge: 0, samples: [], computed_at: 1 },
  ],
  connectors: [],
  thresholds: { never_cited_days: 30, stale_days: 180, stale_min_cited: 3, gap_min_size: 3 },
}

describe("InsightsView", () => {
  it("shows the totals, gaps and sources, and lets only admins change the logging", async () => {
    mockApi({ "GET /api/admin/insights": REPORT })
    render(withProviders(<InsightsView tab="overview" canEdit={false} onTabChange={() => {}} />))

    expect(await screen.findByText("Parental leave in Germany")).toBeInTheDocument()
    expect(screen.getByText("75%")).toBeInTheDocument()
    expect(screen.getByText("2.4 s")).toBeInTheDocument()
    expect(screen.getByText("Questions about documents your role can't see")).toBeInTheDocument()
    expect(screen.getAllByText("handbook.txt").length).toBeGreaterThan(0)
    expect(screen.getByText(/Only admins can change this/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  })

  it("saves a new logging mode for admins", async () => {
    const saved: unknown[] = []
    mockApi({
      "GET /api/admin/insights": REPORT,
      "PUT /api/admin/question-log/settings": (body: unknown) => {
        saved.push(body)
        return { mode: "metadata", days: 90, logged: false }
      },
    })
    render(withProviders(<InsightsView tab="overview" canEdit onTabChange={() => {}} />))
    await userEvent.click(await screen.findByRole("combobox", { name: "Text kept" }))
    await userEvent.click(await screen.findByRole("option", { name: "Metadata only" }))
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(saved).toEqual([{ mode: "metadata", days: 90 }]))
  })
})

describe("LoggingNotice", () => {
  it("tells people when their questions are logged", async () => {
    mockApi({ "GET /api/question-log/settings": { mode: "redacted", days: 90, logged: true } })
    render(withProviders(<LoggingNotice />))
    expect(
      await screen.findByText(/Questions are logged for your admins for 90 days/),
    ).toBeInTheDocument()
  })

  it("says nothing when only metadata is kept", async () => {
    const fetch = mockApi({
      "GET /api/question-log/settings": { mode: "metadata", days: 90, logged: false },
    })
    const { container } = render(withProviders(<LoggingNotice />))
    await new Promise((r) => setTimeout(r, 50))
    expect(fetch).toHaveBeenCalled()
    expect(container).toBeEmptyDOMElement()
  })
})
