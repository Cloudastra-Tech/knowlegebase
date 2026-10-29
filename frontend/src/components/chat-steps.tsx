import { useState, type CSSProperties } from "react"
import {
  ArrowLeftRight,
  BadgeCheck,
  BookOpen,
  BookOpenText,
  Ban,
  Calculator,
  Check,
  CircleDashed,
  FileSearch,
  Globe,
  History,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react"

import "@/components/chat-motion.css"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { AsciiThinking } from "@/components/ascii/ascii-thinking"
import { BrandMark } from "@/components/brand-mark"
import { CodeBlock, CodeBlockContent, CodeBlockHeader, CodeBlockTitle } from "@/components/ui/code-block"
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace"
import type { AnswerState, Source, Step } from "@/lib/api"
import { BRANDS, brandsOf } from "@/lib/brands"
import {
  detailOf,
  kindOf,
  staggerOf,
  statusOf,
  summarize,
  thinkingText,
  titleOf,
  type LiveStep,
  type StepStatus,
  type ToolKind,
} from "@/lib/chat-steps"
import { cn } from "@/lib/utils"

// What the assistant did, as a live trace (shadcn's Trace, driven by our stream
// through lib/chat-steps.ts). While it works: the ASCII thinking line saying what it
// is doing now, and a line per step as it starts, running (shimmering), then done,
// blocked, failed or stopped. Once the answer starts, the lines fold into one summary
// ("Searched company docs · 2 documents · 1.2s"); anyone can open them again, and
// their own choice wins from then on. The raw tool input and output ("Technical
// details") come from the server for admins only.

const ICONS: Record<ToolKind, LucideIcon> = {
  docs: FileSearch,
  wiki: BookOpen,
  "wiki-page": BookOpenText,
  wikipedia: Globe,
  currency: ArrowLeftRight,
  calculator: Calculator,
  verified: BadgeCheck,
  cache: History,
  other: Wrench,
}

// The platform a tool works in: Outline's own tools are Outline's; the company-docs
// search is wherever the documents it found came from (the answer's sources).
function platformsOf(step: LiveStep, found: string[]): string[] {
  const kind = kindOf(step.tool)
  if (kind === "wiki" || kind === "wiki-page") return ["outline"]
  if (kind === "docs") return found
  return []
}

// Small one-colour marks of the platforms a step searched, named for screen readers.
function PlatformMarks({ slugs, animate }: { slugs: string[]; animate: boolean }) {
  if (!slugs.length) return null
  const names = slugs.map((s) => BRANDS[s].title)
  return (
    <span className={cn("flex shrink-0 items-center gap-1 text-muted-foreground", animate && "kb-glyph-in")}>
      {slugs.map((s) => (
        <BrandMark key={s} slug={s} className="size-3" />
      ))}
      <span className="sr-only">in {names.join(", ")}</span>
    </span>
  )
}

// The status as an icon (and a word for screen readers): never colour alone.
const GLYPHS: Record<Exclude<StepStatus, "running">, { icon: LucideIcon; word: string; tone: string }> = {
  done: { icon: Check, word: "Done:", tone: "text-muted-foreground" },
  blocked: { icon: Ban, word: "Blocked:", tone: "text-warning" },
  error: { icon: X, word: "Failed:", tone: "text-destructive" },
  cancelled: { icon: CircleDashed, word: "Stopped:", tone: "text-muted-foreground" },
}

// Trace's own line states: running shimmers, error is red, pending is dimmed.
const TRACE_STATUS: Record<StepStatus, "running" | "error" | "pending" | undefined> = {
  running: "running",
  done: undefined,
  blocked: undefined,
  error: "error",
  cancelled: "pending",
}

function StatusGlyph({ status, animate }: { status: StepStatus; animate: boolean }) {
  if (status === "running")
    return (
      <span className="flex w-3.5 shrink-0 justify-center text-signal">
        <AsciiSpinner size="xs" label="" />
        <span className="sr-only">Running:</span>
      </span>
    )
  const g = GLYPHS[status]
  return (
    <span
      // A new key per status, so a change crossfades in (live answers only).
      key={status}
      className={cn("flex w-3.5 shrink-0 justify-center", g.tone, animate && "kb-glyph-in")}
    >
      <g.icon aria-hidden className="size-3.5" strokeWidth={2} />
      <span className="sr-only">{g.word}</span>
    </span>
  )
}

function StepLineRow({
  step,
  status,
  animate,
  delay,
  found,
}: {
  step: LiveStep
  status: StepStatus
  animate: boolean
  delay: number
  found: string[]
}) {
  const Icon = ICONS[kindOf(step.tool)]
  const detail = detailOf(step, status)
  const platforms = platformsOf(step, found)
  // A tool of one platform wears its mark; a search across several lists them after.
  const own = kindOf(step.tool) !== "docs" && platforms.length === 1 ? platforms[0] : null
  return (
    <div className={cn(animate && "kb-line-in")} style={animate ? ({ "--i": delay } as CSSProperties) : undefined}>
      <TraceLine status={TRACE_STATUS[status]} className="font-mono text-xs data-[status=pending]:opacity-70">
        <StatusGlyph status={status} animate={animate} />
        <TraceIcon className="[&_svg:not([class*='size-'])]:size-3.5">
          {own ? <BrandMark slug={own} className="size-3.5" /> : <Icon />}
        </TraceIcon>
        <TraceTitle className={cn(status === "done" ? "text-foreground/80" : status === "running" && "text-foreground")}>
          {titleOf(step, status)}
        </TraceTitle>
        {!own && status !== "running" && <PlatformMarks slugs={platforms} animate={animate} />}
        {detail && <TraceDescription className="min-w-0 shrink-[3] truncate">{detail}</TraceDescription>}
      </TraceLine>
    </div>
  )
}

// Admins: each tool's raw input and output, as plain code blocks.
function TechnicalDetails({ trace }: { trace: Step[] }) {
  return (
    <TraceCollapsible>
      <TraceCollapsibleTrigger className="font-mono text-xs">
        <TraceLine>
          <TraceTitle>Technical details</TraceTitle>
        </TraceLine>
      </TraceCollapsibleTrigger>
      <TraceCollapsibleContent className="space-y-2">
        {trace.map((step, i) => (
          <CodeBlock key={i} size="sm">
            <CodeBlockHeader>
              <CodeBlockTitle className="font-mono">
                {i + 1}. {step.tool}
              </CodeBlockTitle>
            </CodeBlockHeader>
            <CodeBlockContent wrap>
              {`input: ${JSON.stringify(step.input ?? {}, null, 2)}${step.output ? `\n\noutput:\n${step.output}` : ""}`}
            </CodeBlockContent>
          </CodeBlock>
        ))}
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

export interface StepsTraceProps {
  steps: LiveStep[]
  streaming: boolean
  writing: boolean // the answer's text has started
  state?: AnswerState
  status?: string // the server's status line, before any step ("Checking your question")
  trace?: Step[] // admins: the raw steps
  standalone?: string | null // a follow-up, as it was searched
  hideSteps?: boolean // members, when an admin turned the steps off
  live: boolean // written on this screen (not history): lines may animate
  sources?: Source[] // what the answer cites: the platforms the docs search found in
}

export function StepsTrace({ steps, streaming, writing, state, status, trace, standalone, hideSteps, live, sources }: StepsTraceProps) {
  const thinking = streaming && !writing
  // Open while the assistant works; folded once the answer starts, for good (a later
  // step makes the folded line live instead). Someone's own toggle wins after that.
  const [folded, setFolded] = useState(!streaming || writing)
  if (writing && !folded) setFolded(true)
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const open = userOpen ?? !folded

  const details = (trace ?? []).filter((t) => t.input !== undefined)
  const cached = (trace ?? []).find((t) => t.tool === "answer_cache")
  const thinkingLine = thinking ? (
    <AsciiThinking status={thinkingText(steps, status)} className="kb-fade min-w-0 py-0.5" />
  ) : null

  if (hideSteps || (!steps.length && streaming)) return thinkingLine
  if (!steps.length) {
    // Admins: the answer came from the answer cache (its first raw step says so).
    return cached ? (
      <Trace className="kb-trace">
        <TraceLine className="font-mono text-xs" title={cached.output}>
          <StatusGlyph status="done" animate={false} />
          <TraceIcon className="[&_svg:not([class*='size-'])]:size-3.5">
            <History />
          </TraceIcon>
          <TraceTitle className="text-foreground/80">Reused a recent answer</TraceTitle>
          <TraceDescription className="min-w-0 shrink truncate">same access · no new search</TraceDescription>
        </TraceLine>
      </Trace>
    ) : null
  }

  const opts = { streaming, state }
  const statuses = steps.map((st) => statusOf(st, opts))
  const delays = staggerOf(steps)
  const summary = summarize(steps, opts)
  const animate = live && streaming
  const found = brandsOf((sources ?? []).filter((s) => s.kind !== "general_knowledge").map((s) => s.provider))
  const platforms = brandsOf(steps.flatMap((st) => platformsOf(st, found)))

  return (
    <TraceCollapsible
      open={open}
      onOpenChange={(next) => setUserOpen(next)}
      className="kb-trace gap-0"
    >
      {thinkingLine ? (
        <div className="flex min-w-0 items-center gap-1">
          {thinkingLine}
          <TraceCollapsibleTrigger aria-label={open ? "Hide the steps" : "Show the steps"} className="p-0.5" />
        </div>
      ) : (
        <TraceCollapsibleTrigger className={cn("font-mono text-xs", live && "kb-fade")}>
          <TraceLine status={TRACE_STATUS[summary.status]} className="data-[status=pending]:opacity-70">
            <StatusGlyph status={summary.status} animate={animate} />
            <TraceTitle className={summary.status === "running" ? "text-foreground" : undefined}>{summary.text}</TraceTitle>
            {summary.status !== "running" && <PlatformMarks slugs={platforms} animate={animate} />}
          </TraceLine>
        </TraceCollapsibleTrigger>
      )}
      <TraceCollapsibleContent className="border-dashed">
        <Trace>
          {steps.map((st, i) => (
            <StepLineRow key={st.id} step={st} status={statuses[i]} animate={animate} delay={delays[i]} found={found} />
          ))}
          {!streaming && standalone && (
            <p className="font-mono text-xs text-muted-foreground">
              Searched for: <span className="text-foreground">{standalone}</span>
            </p>
          )}
          {!streaming && details.length > 0 && <TechnicalDetails trace={details} />}
        </Trace>
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}
