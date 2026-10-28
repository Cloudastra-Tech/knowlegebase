import { format } from "date-fns"
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import type { QualityTrend } from "@/lib/api"

// The quality trend on Insights (pipeline.md P2-3): the share of questions answered
// and of thumbs up, per day, with the totals beside them. On the token palette, and
// the two lines differ by dash as well (as the questions chart does).

const config = {
  answer_rate: { label: "Answered", color: "var(--chart-1)" },
  thumbs_up_share: { label: "Thumbs up", color: "var(--chart-2)" },
} satisfies ChartConfig
const DASH: Record<keyof typeof config, string | undefined> = {
  answer_rate: undefined,
  thumbs_up_share: "6 4",
}

const pct = (rate: number | null) => (rate === null ? "—" : `${Math.round(rate * 1000) / 10}%`)

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="font-mono text-lg font-medium tracking-tight tabular-nums">{value}</p>
      <p className="text-xs text-muted-foreground">{note}</p>
    </div>
  )
}

export function QualityTrendView({ trend }: { trend: QualityTrend }) {
  const t = trend.totals
  const data = trend.daily.map((d) => ({
    day: d.day,
    answer_rate: d.answer_rate === null ? null : Math.round(d.answer_rate * 1000) / 10,
    thumbs_up_share: d.thumbs_up_share === null ? null : Math.round(d.thumbs_up_share * 1000) / 10,
  }))
  const label = (v: string) => format(new Date(`${v}T00:00:00`), "d MMM")
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Answered" value={pct(t.answer_rate)} note={`${t.answered} of ${t.questions}`} />
        <Stat
          label="Thumbs up"
          value={pct(t.thumbs_up_share)}
          note={`${t.thumbs_up} up · ${t.thumbs_down} down`}
        />
        <Stat
          label="Faithfulness flags"
          value={t.faithfulness_flags.toLocaleString()}
          note="A figure its sources don't show"
        />
        <Stat label="From the cache" value={t.cached.toLocaleString()} note="Answered again, no model call" />
      </div>
      <ChartContainer config={config} className="aspect-auto h-56 w-full">
        <LineChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="day" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} tickFormatter={label} />
          <YAxis domain={[0, 100]} unit="%" tickLine={false} axisLine={false} width={40} />
          <ChartTooltip cursor content={<ChartTooltipContent labelFormatter={(v) => label(String(v))} />} />
          {(Object.keys(config) as (keyof typeof config)[]).map((key) => (
            <Line
              key={key}
              dataKey={key}
              type="linear"
              stroke={`var(--color-${key})`}
              strokeWidth={2}
              strokeDasharray={DASH[key]}
              connectNulls
              // One day of data is a point with no line to draw: it shows as a dot.
              dot={
                data.filter((d) => d[key] !== null).length === 1
                  ? { r: 3.5, fill: `var(--color-${key})`, strokeWidth: 0 }
                  : false
              }
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ChartContainer>
      <ul className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {(Object.keys(config) as (keyof typeof config)[]).map((key) => (
          <li key={key} className="flex items-center gap-1.5">
            <svg width="20" height="6" aria-hidden="true">
              <line x1="0" y1="3" x2="20" y2="3" stroke={config[key].color} strokeWidth="2" strokeDasharray={DASH[key]} />
            </svg>
            {config[key].label} (% a day)
          </li>
        ))}
      </ul>
    </div>
  )
}
