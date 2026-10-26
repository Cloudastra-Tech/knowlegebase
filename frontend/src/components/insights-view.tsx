import { useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { format, subDays } from "date-fns"
import { BarChart3, Download, Loader2 } from "lucide-react"
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"
import { toast } from "sonner"

import { DateRangePicker, type DateRange } from "@/components/date-range-picker"
import { InsightsFeedback } from "@/components/insights-feedback"
import { QuestionLogSettings } from "@/components/question-log-settings"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { insights as insightsApi, type InsightsReport, type InsightsTable } from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

export type InsightsTab = "overview" | "feedback"

const iso = (d: Date) => format(d, "yyyy-MM-dd")

function percent(rate: number) {
  return `${Math.round(rate * 1000) / 10}%`
}

function seconds(ms: number | null) {
  if (ms === null) return "—"
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

function count(n: number) {
  return n.toLocaleString()
}

function day(epoch?: number | null) {
  return epoch ? format(new Date(epoch * 1000), "d MMM yyyy") : "—"
}

async function save(table: InsightsTable, from?: string, to?: string) {
  try {
    const { blob, filename } = await insightsApi.exportInsights(table, from, to)
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "Couldn't export")
  }
}

function ExportButton({ table, from, to }: { table: InsightsTable; from?: string; to?: string }) {
  const [busy, setBusy] = useState(false)
  return (
    <Button
      variant="ghost"
      size="sm"
      className="text-muted-foreground"
      disabled={busy}
      onClick={async () => {
        setBusy(true)
        await save(table, from, to)
        setBusy(false)
      }}
    >
      {busy ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Download data-icon="inline-start" />}
      CSV
    </Button>
  )
}

function Panel({
  title,
  description,
  action,
  children,
}: {
  title: string
  description: ReactNode
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-medium">{title}</h3>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        {action}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-xl border px-4 py-6 text-center text-sm text-muted-foreground">{children}</p>
  )
}

// A table from `sm` up; on phones each row stacks: the first column, then the rest.
interface Column<T> {
  label: string
  cell: (row: T) => ReactNode
  className?: string
}

function DataTable<T>({ rows, columns, rowKey }: { rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string }) {
  const [first, ...rest] = columns
  return (
    <>
      <div className="divide-y rounded-xl border sm:hidden">
        {rows.map((row) => (
          <div key={rowKey(row)} className="flex flex-col gap-0.5 px-4 py-3 text-sm">
            <div className="min-w-0 font-medium">{first.cell(row)}</div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
              {rest.map((c) => (
                <span key={c.label}>
                  {c.label}: {c.cell(row)}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="hidden rounded-xl border sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((c, i) => (
                <TableHead
                  key={c.label}
                  className={`${i === 0 ? "pl-4" : ""} ${i === columns.length - 1 ? "pr-4" : ""} ${c.className ?? ""}`}
                >
                  {c.label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={rowKey(row)}>
                {columns.map((c, i) => (
                  <TableCell
                    key={c.label}
                    className={`align-top ${i === 0 ? "pl-4" : ""} ${i === columns.length - 1 ? "pr-4" : ""} ${c.className ?? ""}`}
                  >
                    {c.cell(row)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  )
}

function DocTitle({ title, url, docKey }: { title: string | null; url: string | null; docKey: string }) {
  const name = title || docKey
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" className="block max-w-72 truncate hover:underline" title={name}>
      {name}
    </a>
  ) : (
    <span className="block max-w-72 truncate" title={name}>
      {name}
    </span>
  )
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Card size="sm">
      <CardContent className="flex flex-col gap-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-2xl font-semibold tracking-tight tabular-nums">{value}</span>
        {note && <span className="truncate text-xs text-muted-foreground">{note}</span>}
      </CardContent>
    </Card>
  )
}

// The palette is achromatic (DESIGN.md), so the series differ by ink AND dash.
const chartConfig = {
  answered: { label: "Answered", color: "var(--foreground)" },
  not_found: { label: "Not found", color: "var(--chart-2)" },
  general_knowledge: { label: "General knowledge", color: "var(--chart-3)" },
  blocked: { label: "Blocked", color: "var(--chart-4)" },
} satisfies ChartConfig
const DASH: Record<keyof typeof chartConfig, string | undefined> = {
  answered: undefined,
  not_found: "6 4",
  general_knowledge: "2 3",
  blocked: "10 3 2 3",
}

function QuestionsChart({ report }: { report: InsightsReport }) {
  const data = report.daily.map((d) => ({
    day: d.day,
    answered: d.answered + d.partial,
    not_found: d.not_found,
    general_knowledge: d.general_knowledge,
    blocked: d.blocked,
  }))
  const label = (v: string) => format(new Date(`${v}T00:00:00`), "d MMM")
  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
      <LineChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="day" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} tickFormatter={label} />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={32} />
        <ChartTooltip cursor content={<ChartTooltipContent labelFormatter={(v) => label(String(v))} />} />
        {(Object.keys(chartConfig) as (keyof typeof chartConfig)[]).map((key) => (
          <Line
            key={key}
            dataKey={key}
            type="linear"
            stroke={`var(--color-${key})`}
            strokeWidth={2}
            strokeDasharray={DASH[key]}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartContainer>
  )
}

// The key draws each line as the chart does (ink and dash), so no series is told
// apart by shade alone.
function ChartKey() {
  return (
    <ul className="mt-2 flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {(Object.keys(chartConfig) as (keyof typeof chartConfig)[]).map((key) => (
        <li key={key} className="flex items-center gap-1.5">
          <svg width="20" height="6" aria-hidden="true">
            <line
              x1="0"
              y1="3"
              x2="20"
              y2="3"
              stroke={chartConfig[key].color}
              strokeWidth="2"
              strokeDasharray={DASH[key]}
            />
          </svg>
          {chartConfig[key].label}
        </li>
      ))}
    </ul>
  )
}

function Overview({ canEdit }: { canEdit: boolean }) {
  const [today] = useState(() => new Date())
  const [range, setRange] = useState<DateRange | undefined>(() => ({ from: subDays(today, 29), to: today }))
  const from = range?.from ? iso(range.from) : undefined
  const to = range?.from ? iso(range.to ?? range.from) : undefined
  const report = useQuery(queries.insights(from, to)).data

  if (!report) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
        <Skeleton className="h-72 rounded-2xl" />
      </div>
    )
  }
  const t = report.totals
  const range_ = { from: report.from, to: report.to }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2">
        <DateRangePicker value={range} onChange={setRange} className="min-w-0 basis-60 grow sm:basis-72 sm:grow-0" />
        <p className="text-xs text-muted-foreground">
          {range?.from ? "" : "The last 30 days. "}Days are UTC; today updates as questions come in.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label="Questions" value={count(t.questions)} note={`${count(t.active_users)} people asked`} />
        <Tile
          label="Answered"
          value={percent(t.rates.answered)}
          note={t.partial ? `${count(t.partial)} only partly` : `${count(t.answered)} answers`}
        />
        <Tile label="Not found" value={percent(t.rates.not_found)} note={`${count(t.not_found)} questions`} />
        <Tile
          label="General knowledge"
          value={percent(t.rates.general_knowledge)}
          note="Answered from Wikipedia only"
        />
        <Tile label="Blocked" value={percent(t.rates.blocked)} note={`${count(t.blocked)} refused by a guard`} />
        <Tile label="Typical answer time" value={seconds(t.latency_p50_ms)} note={`95% within ${seconds(t.latency_p95_ms)}`} />
        <Tile
          label="Tokens"
          value={count(t.tokens_in + t.tokens_out)}
          note={`${count(t.tokens_in)} in · ${count(t.tokens_out)} out`}
        />
        <Tile label="Estimated cost" value={`$${t.cost_usd.toFixed(2)}`} note="At list prices per model" />
      </div>

      <Panel
        title="Questions per day"
        description="By how each one ended. Partly answered questions count as answered."
        action={<ExportButton table="daily" {...range_} />}
      >
        {t.questions ? (
          <>
            <QuestionsChart report={report} />
            <ChartKey />
          </>
        ) : (
          <Empty>No questions in these days yet.</Empty>
        )}
      </Panel>

      <Panel
        title="Content gaps"
        description={
          report.logging.mode === "metadata"
            ? "Questions are logged without their text here, so they can't be grouped."
            : `Questions the documents couldn't answer, grouped by meaning each week (groups under ${report.thresholds.gap_min_size} questions aren't shown). Sample questions never say who asked.`
        }
        action={<ExportButton table="gaps" {...range_} />}
      >
        {report.gaps.length ? (
          <div className="divide-y rounded-xl border">
            {report.gaps.map((g) => (
              <div key={g.id} className="flex flex-col gap-1.5 px-4 py-3 text-sm">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <span className="font-medium">
                    {g.hidden ? "Questions about documents your role can't see" : (g.label ?? "Unnamed topic")}
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {g.size} questions · {g.not_found} not found · {g.general_knowledge} general knowledge
                  </span>
                </div>
                {g.samples.length > 0 && (
                  <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
                    {g.samples.slice(0, 3).map((s, i) => (
                      <li key={i} className="truncate" title={s}>
                        “{s}”
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        ) : (
          <Empty>No gaps found yet. They're grouped once a week from unanswered questions.</Empty>
        )}
      </Panel>

      <Panel
        title="Most cited documents"
        description="The documents answers cited most in these days."
        action={<ExportButton table="sources" {...range_} />}
      >
        {report.top_sources.length ? (
          <DataTable
            rows={report.top_sources.slice(0, 10)}
            rowKey={(r) => r.doc_key}
            columns={[
              { label: "Document", cell: (r) => <DocTitle title={r.title} url={r.url} docKey={r.doc_key} /> },
              { label: "Source", cell: (r) => r.provider ?? "—" },
              {
                label: "Access",
                cell: (r) => (r.access === "public" ? "Everyone" : <Badge variant="outline">HR / Admin</Badge>),
              },
              { label: "Cited", cell: (r) => <span className="tabular-nums">{r.cited}</span>, className: "text-right" },
            ]}
          />
        ) : (
          <Empty>No answer cited a document in these days.</Empty>
        )}
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel
          title="Never cited"
          description={`Searchable for over ${report.thresholds.never_cited_days} days and never cited: candidates to update or remove.${report.never_cited_count > report.never_cited.length ? ` Showing ${report.never_cited.length} of ${report.never_cited_count}.` : ""}`}
          action={<ExportButton table="never_cited" {...range_} />}
        >
          {report.never_cited.length ? (
            <DataTable
              rows={report.never_cited.slice(0, 10)}
              rowKey={(r) => r.doc_key}
              columns={[
                { label: "Document", cell: (r) => <DocTitle title={r.title} url={r.url} docKey={r.doc_key} /> },
                { label: "Source", cell: (r) => r.provider ?? "—" },
                { label: "Since", cell: (r) => <span className="whitespace-nowrap">{day(r.first_seen)}</span> },
              ]}
            />
          ) : (
            <Empty>Every document has been cited at least once.</Empty>
          )}
        </Panel>
        <Panel
          title="Possibly stale"
          description={`Not changed in ${report.thresholds.stale_days} days but cited at least ${report.thresholds.stale_min_cited} times here: worth checking they're still right.`}
          action={<ExportButton table="stale" {...range_} />}
        >
          {report.stale.length ? (
            <DataTable
              rows={report.stale.slice(0, 10)}
              rowKey={(r) => r.doc_key}
              columns={[
                { label: "Document", cell: (r) => <DocTitle title={r.title} url={r.url} docKey={r.doc_key} /> },
                { label: "Changed", cell: (r) => <span className="whitespace-nowrap">{day(r.modified)}</span> },
                { label: "Cited", cell: (r) => <span className="tabular-nums">{r.cited}</span>, className: "text-right" },
              ]}
            />
          ) : (
            <Empty>No old document is cited often.</Empty>
          )}
        </Panel>
      </div>

      <Panel
        title="Connectors"
        description="How each connected source last synced."
        action={<ExportButton table="connectors" {...range_} />}
      >
        {report.connectors.length ? (
          <DataTable
            rows={report.connectors}
            rowKey={(r) => r.provider}
            columns={[
              { label: "Source", cell: (r) => r.name },
              {
                label: "Last sync",
                cell: (r) => (
                  <span className={r.last_status === "failed" ? "text-destructive" : undefined}>
                    {r.last_status === "failed"
                      ? `Failed ${timeAgo(r.last_run_at)}${r.last_error ? ` (${r.last_error})` : ""}`
                      : timeAgo(r.last_success_at)}
                  </span>
                ),
              },
              { label: "Documents", cell: (r) => <span className="tabular-nums">{count(r.items)}</span> },
              { label: "Skipped", cell: (r) => <span className="tabular-nums">{count(r.skipped)}</span> },
              {
                label: "Failures in a row",
                cell: (r) => <span className="tabular-nums">{r.failures}</span>,
                className: "text-right",
              },
            ]}
          />
        ) : (
          <Empty>No connectors yet. Uploaded files are counted above.</Empty>
        )}
      </Panel>

      <QuestionLogSettings
        key={`${report.logging.mode}-${report.logging.days}`}
        settings={report.logging}
        canEdit={canEdit}
      />
    </div>
  )
}

// /admin/insights: how the assistant is used and where it falls short (HR and admins,
// viewing as HR; only admins change the logging or the test set).
export function InsightsView({
  tab,
  canEdit,
  onTabChange,
}: {
  tab: InsightsTab
  canEdit: boolean
  onTabChange: (tab: InsightsTab) => void
}) {
  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <BarChart3 className="size-6" /> Insights
          </h2>
          <p className="text-sm text-muted-foreground">
            Questions, answers and documents, in aggregate. No one's chats are shown here.
          </p>
        </div>
        <Tabs value={tab} onValueChange={(v) => onTabChange(v as InsightsTab)}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="feedback">Feedback</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div className="mt-6">
        {tab === "overview" ? <Overview canEdit={canEdit} /> : <InsightsFeedback canEdit={canEdit} />}
      </div>
    </div>
  )
}
