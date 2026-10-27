// Admin insights, the question log's settings, answer feedback and the test set
// (/api/admin/insights, /api/question-log/settings, /api/admin/feedback,
// /api/admin/eval-cases). HR and admins read; admins change.

import { download, json, request, seg } from "@/lib/api/client"
import type { Access, FeedbackReason, Source } from "@/lib/api/types"

// How much of a question's and answer's text the log keeps, and for how long.
export type LogMode = "full" | "redacted" | "metadata"

export interface LogSettings {
  mode: LogMode
  days: number
  logged?: boolean // any text is kept (the chat tells people so)
}

export type Outcome =
  | "answered"
  | "partial"
  | "not_found"
  | "blocked"
  | "general_knowledge"
  | "error"
  | "stopped"

export type InsightsDay = { day: string; questions: number; active_users: number } & Record<
  Outcome,
  number
>

export interface InsightsTotals extends Record<Outcome, number> {
  questions: number
  active_users: number
  tokens_in: number
  tokens_out: number
  cost_usd: number
  // Models whose price isn't known (a Bedrock model not in the price table): their
  // tokens aren't in cost_usd.
  cost_unknown_models?: string[]
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  rates: { answered: number; not_found: number; blocked: number; general_knowledge: number }
}

export interface DocStat {
  doc_key: string
  title: string | null
  provider: string | null
  url: string | null
  access: Access
  cited?: number
  first_seen?: number
  modified?: number | null
}

export interface ContentGap {
  id: number
  label: string | null
  hidden: boolean // built from answers the viewer's role can't see: its size only
  size: number
  not_found: number
  general_knowledge: number
  samples: string[]
  computed_at: number
}

export interface ConnectorHealth {
  provider: string
  name: string
  status: string
  last_run_at: number | null
  last_status: "running" | "done" | "failed" | "held" | null
  last_error: string | null
  last_success_at: number | null
  items: number
  skipped: number
  failures: number
}

export interface InsightsReport {
  from: string
  to: string
  today: string
  logging: LogSettings
  totals: InsightsTotals
  daily: InsightsDay[]
  // Where questions were asked (every surface, most asked first).
  surfaces: SurfaceStat[]
  top_sources: DocStat[]
  never_cited: DocStat[]
  never_cited_count: number
  stale: DocStat[]
  gaps: ContentGap[]
  connectors: ConnectorHealth[]
  quality: QualityTrend
  thresholds: {
    never_cited_days: number
    stale_days: number
    stale_min_cited: number
    gap_min_size: number
  }
}

// The quality trend (kb/services/quality.py): per day, the share of questions answered,
// the thumbs, faithfulness flags (a figure the answer's sources don't show) and answers
// reused from the answer cache. A rate is null on a day with nothing to rate.
export interface QualityDay {
  day: string
  questions: number
  answered: number
  answer_rate: number | null
  thumbs_up: number
  thumbs_down: number
  thumbs_up_share: number | null
  faithfulness_flags: number
  cached: number
}

export interface QualityTrend {
  daily: QualityDay[]
  totals: Omit<QualityDay, "day">
}

export type Surface = "web" | "api" | "mcp" | "slack" | "teams" | "extension" | "email"

export interface SurfaceStat {
  surface: Surface
  questions: number
  answered: number // fully or partly
}

export type InsightsTable =
  | "daily"
  | "surfaces"
  | "sources"
  | "never_cited"
  | "stale"
  | "gaps"
  | "connectors"

// An answer someone rated, as it was when they rated it.
export interface RatedAnswer {
  message_id: string
  rating: 1 | -1
  reasons: FeedbackReason[]
  comment: string | null
  question: string | null
  standalone_question: string | null
  answer: string | null
  state: Outcome | null
  sources: Source[]
  created_at: number
  updated_at: number
}

export interface EvalCase {
  id: number
  message_id: string
  question: string
  answer: string | null
  expected_answer: string | null
  sources: Source[]
  reasons: FeedbackReason[]
  comment: string | null
  role: "public" | "hr"
  created_by: string
  created_at: number
  // admin: promoted by hand; feedback / not_found: suggested by the weekly job.
  origin: "admin" | "feedback" | "not_found"
  golden: Record<string, unknown> // the case as a line of eval/golden/*.jsonl
}

function range(params: { from?: string; to?: string; table?: string }) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
  const s = q.toString()
  return s ? `?${s}` : ""
}

export const insightsApi = {
  logSettings: () => request<LogSettings>("/question-log/settings"),
  setLogSettings: (mode: LogMode, days: number) =>
    request<LogSettings>("/admin/question-log/settings", json("PUT", { mode, days })),
  insights: (from?: string, to?: string) =>
    request<InsightsReport>(`/admin/insights${range({ from, to })}`),
  exportInsights: (table: InsightsTable, from?: string, to?: string) =>
    download(`/admin/insights/export${range({ table, from, to })}`),
  ratedAnswers: (rating?: 1 | -1) =>
    request<{ feedback: RatedAnswer[] }>(`/admin/feedback${rating ? `?rating=${rating}` : ""}`).then(
      (r) => r.feedback,
    ),
  evalCases: () => request<{ cases: EvalCase[] }>("/admin/eval-cases").then((r) => r.cases),
  addEvalCase: (messageId: string, expectedAnswer: string | null) =>
    request<EvalCase>(
      "/admin/eval-cases",
      json("POST", { message_id: messageId, expected_answer: expectedAnswer }),
    ),
  deleteEvalCase: (id: number) =>
    request<{ ok: true }>(`/admin/eval-cases/${seg(String(id))}`, json("DELETE")),
}
