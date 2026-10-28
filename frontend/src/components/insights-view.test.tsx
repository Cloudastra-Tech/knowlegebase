import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
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
  surfaces: [
    { surface: "web", questions: 9, answered: 7 },
    { surface: "teams", questions: 3, answered: 2 },
    { surface: "email", questions: 0, answered: 0 },
  ],
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
  quality: {
    daily: [
      { day: "2026-10-21", questions: 15, answered: 10, answer_rate: 0.6667, thumbs_up: 2,
        thumbs_down: 1, thumbs_up_share: 0.6667, faithfulness_flags: 1, cached: 0 },
      { day: "2026-10-22", questions: 25, answered: 20, answer_rate: 0.8, thumbs_up: 1,
        thumbs_down: 0, thumbs_up_share: 1, faithfulness_flags: 0, cached: 4 },
    ],
    totals: { questions: 40, answered: 30, answer_rate: 0.7, thumbs_up: 3, thumbs_down: 1,
              thumbs_up_share: 0.25, faithfulness_flags: 1, cached: 4 },
  },
  thresholds: { never_cited_days: 30, stale_days: 180, stale_min_cited: 3, gap_min_size: 3 },
}

describe("InsightsView", () => {
  it("shows the totals, gaps and sources, and lets only admins change the logging", async () => {
    mockApi({ "GET /api/admin/insights": REPORT })
    render(
      withProviders(
        <MemoryRouter>
          <InsightsView tab="overview" canEdit={false} onTabChange={() => {}} />
        </MemoryRouter>,
      ),
    )

    expect(await screen.findByText("Parental leave in Germany")).toBeInTheDocument()
    expect(screen.getByText("75%")).toBeInTheDocument()
    expect(screen.getByText("2.4 s")).toBeInTheDocument()
    expect(screen.getByText("Questions about documents your role can't see")).toBeInTheDocument()
    expect(screen.getAllByText("handbook.txt").length).toBeGreaterThan(0)
    // Where questions come from: Teams and email among the surfaces.
    expect(screen.getByText("Microsoft Teams")).toBeInTheDocument()
    expect(screen.getByText("3 questions · 66.7% answered")).toBeInTheDocument()
    expect(screen.getByText("Email")).toBeInTheDocument()
    // The quality trend: answer rate, thumbs and faithfulness flags.
    expect(screen.getByText("Quality trend")).toBeInTheDocument()
    expect(screen.getByText("3 up · 1 down")).toBeInTheDocument()
    expect(screen.getByText("Faithfulness flags")).toBeInTheDocument()
    expect(screen.getByText(/Only admins can change this/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  })

  it("says 1 question, not 1 questions", async () => {
    mockApi({
      "GET /api/admin/insights": {
        ...REPORT,
        totals: { ...REPORT.totals, active_users: 1, not_found: 1 },
        surfaces: [{ surface: "slack", questions: 1, answered: 1 }],
      },
    })
    render(
      withProviders(
        <MemoryRouter>
          <InsightsView tab="overview" canEdit={false} onTabChange={() => {}} />
        </MemoryRouter>,
      ),
    )
    expect(await screen.findByText("1 question · 100% answered")).toBeInTheDocument()
    expect(screen.getByText("1 person asked")).toBeInTheDocument()
    expect(screen.queryByText(/\b1 questions\b/)).not.toBeInTheDocument()
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
    render(
      withProviders(
        <MemoryRouter>
          <InsightsView tab="overview" canEdit onTabChange={() => {}} />
        </MemoryRouter>,
      ),
    )
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
