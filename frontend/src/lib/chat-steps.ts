// What the assistant did, as the chat's Trace shows it (components/chat-steps.tsx):
// each step's status, its title in the right tense, and the one line the steps fold
// into once the answer starts. Pure, so it is tested on its own (chat-steps.test.ts).
//
// The stream (kb/routers/chat.py) sends step_start {id, tool, label} and step_end
// {id, tool, result}; the label is present tense ("Searching company docs"), the
// result a short summary ("found 2 relevant documents"). There is no status field:
// it is read from the result, which is what agent.step_result writes.
import type { AnswerState, StepLine } from "@/lib/api"

// One line of the trace. startedAt / ms are the browser's clock (performance.now()),
// on answers written on this screen only: saved steps have no timing. `ended`: it
// was still running when the answer was stopped or failed (chat-view.tsx endSteps).
export interface LiveStep extends StepLine {
  done: boolean
  ended?: "stopped" | "error"
  startedAt?: number
  ms?: number
}

export type StepStatus = "running" | "done" | "blocked" | "error" | "cancelled"

// What each tool is, for its icon and its title once done. Tools not listed keep
// the server's label.
export type ToolKind =
  | "docs"
  | "wiki"
  | "wiki-page"
  | "wikipedia"
  | "currency"
  | "calculator"
  | "verified"
  | "cache"
  | "other"

const KINDS: Record<string, ToolKind> = {
  search_docs: "docs",
  search_outline: "wiki",
  read_outline_doc: "wiki-page",
  wikipedia: "wikipedia",
  convert_currency: "currency",
  calculator: "calculator",
  verified_answer: "verified",
  answer_cache: "cache",
}

export const kindOf = (tool?: string): ToolKind => (tool && KINDS[tool]) || "other"

export function statusOf(
  step: Pick<LiveStep, "result"> & Partial<LiveStep>,
  { streaming = false, state }: { streaming?: boolean; state?: AnswerState } = {},
): StepStatus {
  const result = step.result
  if (step.ended) return step.ended === "error" ? "error" : "cancelled"
  if (result === undefined) {
    // No step_end: still running, or the answer ended before it (Stop, a failure).
    if (streaming) return "running"
    return state === "error" ? "error" : "cancelled"
  }
  if (result.startsWith("blocked")) return "blocked"
  if (result === "failed") return "error"
  return "done"
}

const quoted = (label: string) => label.match(/“([^”]*)”/)?.[1]

// The title of a finished step, in the past tense.
function doneTitle(step: LiveStep): string {
  switch (kindOf(step.tool)) {
    case "docs":
      return "Searched company docs"
    case "wiki":
      return "Searched the Outline wiki"
    case "wiki-page":
      return step.result && step.result !== "read" ? `Read “${step.result}”` : "Read a wiki page"
    case "wikipedia": {
      const topic = quoted(step.label)
      return topic ? `Looked up “${topic}” on Wikipedia` : "Looked it up on Wikipedia"
    }
    case "currency": {
      const m = step.label.match(/ ([A-Z]{3}) to ([A-Z]{3})$/)
      return m ? `Converted ${m[1]} to ${m[2]}` : "Converted the amount"
    }
    case "calculator":
      return "Calculated"
    case "verified":
      return "Matched a verified answer"
    case "cache":
      return "Reused a recent answer"
    default:
      return step.label
  }
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)

export function titleOf(step: LiveStep, status: StepStatus): string {
  switch (status) {
    case "running":
      return step.label
    case "done":
      return doneTitle(step)
    case "blocked":
      return `Blocked while ${lower(step.label)}`
    case "error":
      return `Failed while ${lower(step.label)}`
    case "cancelled":
      return `Stopped while ${lower(step.label)}`
  }
}

// The description after the title: what it found (a wiki page's title is already in
// its own), and how long it took.
export function detailOf(step: LiveStep, status: StepStatus): string {
  const parts: string[] = []
  const result = step.result
  if (result && status !== "error" && !(kindOf(step.tool) === "wiki-page" && status === "done"))
    parts.push(status === "blocked" ? result.replace(/^blocked:?\s*/, "") : result)
  if (step.ms !== undefined && status !== "running") parts.push(formatMs(step.ms))
  return parts.join(" · ")
}

// "0.8s" under ten seconds, then whole seconds.
export function formatMs(ms: number): string {
  const s = ms / 1000
  return s < 10 ? `${s.toFixed(1)}s` : `${Math.round(s)}s`
}

// How many documents the company-docs search found, from its result.
export function documentsFound(steps: LiveStep[]): number | null {
  let total: number | null = null
  for (const st of steps) {
    if (kindOf(st.tool) !== "docs" || !st.result) continue
    const m = st.result.match(/found (\d+) relevant document/)
    total = (total ?? 0) + (m ? Number(m[1]) : 0)
  }
  return total
}

const VERBS: Partial<Record<ToolKind, string>> = {
  docs: "searched company docs",
  wiki: "searched the wiki",
  "wiki-page": "read the wiki",
  wikipedia: "looked it up on Wikipedia",
  currency: "converted an amount",
  calculator: "calculated",
  verified: "matched a verified answer",
  cache: "reused a recent answer",
}

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

export interface StepsSummary {
  status: StepStatus
  text: string
}

// The one line the steps fold into: what was done, what it found, how long it took
// ("Searched company docs and the wiki · 2 documents · 1.2s"). A step still running
// makes the line live; a blocked, failed or stopped one says so instead.
export function summarize(
  steps: LiveStep[],
  opts: { streaming?: boolean; state?: AnswerState } = {},
): StepsSummary {
  const statuses = steps.map((st) => statusOf(st, opts))
  const running = steps.findLast((_, i) => statuses[i] === "running")
  if (running) return { status: "running", text: running.label }
  const verbs: string[] = []
  for (const st of steps) {
    const v = VERBS[kindOf(st.tool)] ?? lower(st.label)
    if (!verbs.includes(v)) verbs.push(v)
  }
  // "searched company docs and the wiki", not "... and searched the wiki".
  const docs = verbs.indexOf(VERBS.docs!)
  const wiki = verbs.indexOf(VERBS.wiki!)
  if (docs >= 0 && wiki >= 0) {
    verbs[docs] = "searched company docs and the wiki"
    verbs.splice(wiki, 1)
  }
  const what =
    verbs.length <= 1 ? (verbs[0] ?? "") : `${verbs.slice(0, -1).join(", ")} and ${verbs.at(-1)}`
  const blocked = statuses.includes("blocked")
  const failed = statuses.includes("error")
  const stopped = statuses.includes("cancelled")
  const status: StepStatus = blocked ? "blocked" : failed ? "error" : stopped ? "cancelled" : "done"
  const head = blocked
    ? "Blocked by access rules"
    : failed
      ? "A step failed"
      : stopped
        ? "Stopped"
        : sentence(what || "Done")
  const parts = [head]
  if (status === "done") {
    const n = documentsFound(steps)
    if (n !== null) parts.push(`${n} document${n === 1 ? "" : "s"}`)
    else if (steps.length > 1) parts.push(`${steps.length} steps`)
  }
  const ms = totalMs(steps)
  if (ms !== null) parts.push(formatMs(ms))
  return { status, text: parts.join(" · ") }
}

// From the first step's start to the last one's end (steps can run side by side).
export function totalMs(steps: LiveStep[]): number | null {
  const timed = steps.filter((st) => st.startedAt !== undefined && st.ms !== undefined)
  if (!timed.length) return null
  const start = Math.min(...timed.map((st) => st.startedAt!))
  const end = Math.max(...timed.map((st) => st.startedAt! + st.ms!))
  return end - start
}

// What the thinking line says while there is no answer text yet: the question being
// read (the server's "status", before any step), the step running now, then the
// writing.
export function thinkingText(steps: LiveStep[], status?: string): string {
  const running = steps.findLast((st) => st.result === undefined && !st.done)
  if (running) return running.label
  if (steps.length) return "Writing the answer"
  return !status || status === "Thinking" || status === "Checking your question"
    ? "Reading the question"
    : status
}

// Where each line sits in its burst, for the 40ms stagger: steps that started
// together (search_docs and search_outline run side by side) enter one after the
// other, at most three deep; a step on its own enters at once.
export function staggerOf(steps: LiveStep[], windowMs = 120, max = 3): number[] {
  const out: number[] = []
  for (let i = 0; i < steps.length; i++) {
    const prev = steps[i - 1]
    const t = steps[i].startedAt
    const together = prev?.startedAt !== undefined && t !== undefined && t - prev.startedAt < windowMs
    out.push(together ? Math.min(out[i - 1] + 1, max) : 0)
  }
  return out
}
