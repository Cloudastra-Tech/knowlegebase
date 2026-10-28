import { useMemo, useState, type CSSProperties, type ReactNode, type Ref } from "react"
import {
  BadgeCheck,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  CornerDownRight,
  ExternalLink,
  FileText,
  Gauge,
  GitBranch,
  Globe,
  Info,
  Lock,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  SearchX,
  Share2,
  ShieldAlert,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
} from "lucide-react"
import { toast } from "sonner"

import "@/components/chat-motion.css"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { Markdown } from "@/components/chat-markdown"
import { StepsTrace } from "@/components/chat-steps"
import { SourceSheet } from "@/components/citations"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Message as MessageRow, MessageContent, MessageFooter } from "@/components/ui/message"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
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
  type VerifiedBadge,
} from "@/lib/api"
import { branchOf } from "@/lib/chat-history"
import type { LiveStep } from "@/lib/chat-steps"
import { CitationsContext, placeOf } from "@/lib/citations"
import { cn } from "@/lib/utils"

// One line of the steps, live while the assistant works (lib/chat-steps.ts).
export type { LiveStep }

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
  limitedBy?: "rate" | "plan" // a rate limit or spend cap (429), or the plan's (402)
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
  onAskEverywhere?: (question: string) => void // set when the chat is in a space
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

// The sources under an answer, kept quiet: one small pill per document, its cited
// passages as numbered buttons (named like its [S#] chips) that open the source
// viewer; a page read whole links out instead.
function sourceLabel(source: Source) {
  return source.kind === "general_knowledge" ? `General knowledge · ${source.provider}` : source.provider
}

function SourceGroup({
  sources,
  onOpen,
}: {
  sources: { source: Source; n: number }[]
  onOpen: (source: Source) => void
}) {
  const first = sources[0].source
  const general = first.kind === "general_knowledge"
  const Icon = general ? Globe : first.provider.toLowerCase().includes("outline") ? BookOpen : FileText
  const label = sourceLabel(first)
  const linkOnly = !first.chunk_id && !first.snippet && first.url
  const title = (
    <>
      <Icon className="size-3.5 shrink-0" />
      <span className="max-w-[16rem] truncate">{first.title}</span>
    </>
  )
  return (
    <div className="inline-flex h-7 max-w-full min-w-0 items-center gap-1.5 rounded-full bg-foreground/5 pr-1 pl-2.5 text-xs text-muted-foreground">
      {linkOnly ? (
        <a
          href={first.url!}
          target="_blank"
          rel="noreferrer"
          aria-label={`${first.title}, ${label} (opens in a new tab)`}
          className="flex min-w-0 items-center gap-1.5 rounded-sm outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          {title}
          <ExternalLink className="size-3 shrink-0" />
        </a>
      ) : (
        <span className="flex min-w-0 items-center gap-1.5" title={`${first.title} · ${label}`}>
          {title}
        </span>
      )}
      {!linkOnly &&
        sources.map(({ source, n }) => {
          const place = placeOf(source)
          return (
            <Tooltip key={source.chunk_id ?? n}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => onOpen(source)}
                  // All its visible text is in its name (WCAG 2.5.3), the provider too.
                  aria-label={`Open source ${n}: ${source.title}${place ? `, ${place}` : ""}, ${label}`}
                  className="flex size-5 shrink-0 items-center justify-center rounded-full font-mono text-[11px] tabular-nums transition-colors duration-(--dur-hover) ease-(--ease-hover) outline-none hover:bg-foreground/10 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  {n}
                </button>
              </TooltipTrigger>
              <TooltipContent side="top">{place || label}</TooltipContent>
            </Tooltip>
          )
        })}
    </div>
  )
}

// Sources in citation order, grouped by document (first appearance wins the order).
function groupSources(sources: Source[]) {
  const groups = new Map<string, { source: Source; n: number }[]>()
  sources.forEach((source, i) => {
    const key = `${source.provider}\u0000${source.title}\u0000${source.url ?? ""}`
    const list = groups.get(key) ?? []
    list.push({ source, n: source.n ?? i + 1 })
    groups.set(key, list)
  })
  return [...groups.values()]
}

// Too many questions in the last minute: a pause, not a failure, so no red.
function LimitNotice({ text, plan = false }: { text: string; plan?: boolean }) {
  const Icon = plan ? Gauge : Clock
  return (
    <div className="glass-subtle flex items-start gap-2.5 rounded-xl px-3 py-2.5 text-sm">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 space-y-0.5">
        <p className="font-medium">{plan ? "Your plan's limit" : "Give it a moment"}</p>
        <p className="text-muted-foreground">{text}</p>
      </div>
    </div>
  )
}

// A mono line above an answer that is only partly from the documents, or not from
// them at all: a glyph, the icon and the words, in the state's colour.
function AnswerNote({
  icon: Icon,
  tone,
  children,
}: {
  icon: typeof Info
  tone: string
  children: string
}) {
  return (
    <p className={cn("mb-3 flex items-center gap-1.5 font-mono text-xs", tone)}>
      <Icon className="size-3.5 shrink-0" aria-hidden />
      {children}
    </p>
  )
}

// The reading column: opaque, so the background field never moves under the words,
// with mono ┌ ┐ └ ┘ marks at its corners.
function ReadingColumn({ children, className }: { children: ReactNode; className?: string }) {
  const mark = "pointer-events-none absolute font-mono text-[0.625rem] leading-none text-muted-foreground/50 select-none"
  return (
    <div data-slot="reading-column" className={cn("relative rounded-xl border border-border/70 bg-background px-4 py-3.5 sm:px-5", className)}>
      <span aria-hidden className={cn(mark, "top-1 left-1.5")}>┌</span>
      <span aria-hidden className={cn(mark, "top-1 right-1.5")}>┐</span>
      <span aria-hidden className={cn(mark, "bottom-1 left-1.5")}>└</span>
      <span aria-hidden className={cn(mark, "right-1.5 bottom-1")}>┘</span>
      {children}
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
            <Button size="sm" loading={saving} onClick={() => send(-1)}>
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
// are notices, each its own shape, so they can't be mistaken for an answer from the
// documents; an answer is in the opaque reading column. `live`: written on this
// screen, so a notice fades in as it arrives (never on history).
function AnswerBody({ message, actions, live }: { message: Message; actions: AnswerActions; live: boolean }) {
  const { state, text } = message
  const arrive = live ? "kb-fade-slow" : undefined
  if (message.withheld) {
    // Decision D1: the whole answer is withheld, not only its sources: its text was
    // written from them.
    const ask = actions.onAskYourself
    return (
      <Alert className={cn("px-3 py-2.5", arrive)}>
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
    // A guard refused: a warning edge, not an error.
    return (
      <Alert className={cn("border-warning/40 border-l-2 border-l-warning px-3 py-2.5 *:[svg]:text-warning", arrive)}>
        <ShieldAlert />
        <AlertTitle>Can't answer that</AlertTitle>
        <AlertDescription>{text}</AlertDescription>
      </Alert>
    )
  }
  if (state === "not_found") {
    // Nothing in the documents: a dashed outline, the shape of something missing.
    return (
      <Alert className={cn("border-dashed border-border bg-background px-3 py-2.5 *:[svg]:text-muted-foreground", arrive)}>
        <SearchX />
        <AlertTitle>Your documents don't cover this</AlertTitle>
        <AlertDescription>
          <Markdown text={text} className="text-sm text-muted-foreground" />
          <div className="mt-2 flex flex-wrap gap-2">
            {message.question && actions.onAskEverywhere && (
              <Button variant="outline" size="sm" onClick={() => actions.onAskEverywhere!(message.question!)}>
                Ask in All company
              </Button>
            )}
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
      <Alert variant="destructive" className={cn("border-destructive/40 px-3 py-2.5", arrive)}>
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
      <p className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
        <AsciiSpinner size="xs" label="" className="text-signal" />
        <span className="shimmer motion-reduce:shimmer-none">Still being written. Reload in a moment to see it.</span>
      </p>
    )
  }
  if (message.verified) {
    // C18: an approved answer, in a panel that says who verified it and when.
    const v = message.verified
    const date = new Date(v.at * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" })
    return (
      <ReadingColumn className="border-signal/35 p-0 sm:p-0">
        <div aria-label="Verified answer">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 border-b border-border/70 px-4 py-2 font-mono text-xs sm:px-5">
            <BadgeCheck className="size-4 text-signal" aria-hidden />
            <span className="font-medium text-foreground">
              Verified by {v.by} · {date}
            </span>
            <span className="min-w-0 truncate text-muted-foreground">{v.title}</span>
          </div>
          <div className="px-4 py-3.5 sm:px-5">
            <Markdown text={text} streaming={message.streaming} />
          </div>
        </div>
      </ReadingColumn>
    )
  }
  // An answer: written, still streaming in, partly from the documents, from general
  // knowledge, or stopped part way. One element throughout, so the final, checked
  // text replaces the streamed draft in place.
  return (
    <ReadingColumn
      className={cn(
        message.streaming && live && "kb-fade",
        state === "partial" && "border-warning/30",
        state === "stopped" && "border-dashed",
      )}
    >
      {state === "partial" && (
        <AnswerNote icon={Info} tone="text-warning">
          Partly answered: the documents don't cover all of it
        </AnswerNote>
      )}
      {state === "general_knowledge" && (
        <AnswerNote icon={Globe} tone="text-muted-foreground">
          General knowledge, not from your documents
        </AnswerNote>
      )}
      {text && <Markdown text={text} streaming={message.streaming} />}
      {state === "stopped" && (
        <div className={cn("flex flex-wrap items-center gap-2", text && "mt-3 border-t border-dashed border-border/70 pt-3")}>
          <span className="font-mono text-xs text-muted-foreground">
            <span aria-hidden>■ </span>
            {text ? "Stopped before the end" : "Stopped"}
          </span>
          {message.id && actions.onRetry && <RetryButton message={message} onRetry={actions.onRetry} />}
        </div>
      )}
    </ReadingColumn>
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
function ActionRow({ message, actions, className }: { message: Message; actions: AnswerActions; className?: string }) {
  const state = message.state
  const shareable = !!state && SHAREABLE.includes(state) && !message.withheld
  const rateable = !!state && RATEABLE.includes(state) && !message.withheld
  return (
    <MessageFooter className={cn("flex-wrap gap-0.5 px-0 font-normal", className)} data-print="hide">
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
    </MessageFooter>
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
  // Written on this screen: it mounted while streaming. Only such an answer animates
  // what arrives (its sources, actions, follow-ups); history, a branch switch and an
  // edited question never do.
  const [live] = useState(!!message.streaming)
  const citations = useMemo(
    () => ({
      byNumber: new Map(sources.filter((s) => s.n !== undefined).map((s) => [s.n!, s])),
      open: setOpened,
      live: !!message.streaming,
    }),
    [sources, message.streaming],
  )
  // Step timings are this screen's own (saved steps have none): kept by step id, so
  // they outlast the saved copy that replaces the streamed answer once it's done.
  const [timings] = useState(() => new Map<string, Pick<LiveStep, "startedAt" | "ms">>())
  const steps = (message.steps ?? []).map((st) => {
    if (st.startedAt !== undefined) {
      timings.set(st.id, { startedAt: st.startedAt, ms: st.ms })
      return st
    }
    return timings.has(st.id) ? { ...st, ...timings.get(st.id) } : st
  })
  const followups =
    finished && last && !actions.busy && actions.onFollowup && !message.withheld ? (message.followups ?? []) : []
  const rise = (i: number, delay = 0) =>
    live ? { className: "kb-rise", style: { "--i": Math.min(i, 5), "--d": `${delay}ms` } as CSSProperties } : {}
  return (
    <CitationsContext value={citations}>
      <MessageRow
        ref={ref}
        tabIndex={-1}
        className="gap-3 rounded-lg outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        data-state={message.state}
        aria-label="Answer"
      >
        <MessageContent className="gap-3 pt-1">
          <StepsTrace
            steps={steps}
            streaming={!!message.streaming}
            writing={!!message.text}
            state={message.state}
            status={message.status}
            trace={message.trace}
            standalone={message.standalone}
            hideSteps={actions.hideSteps}
            live={live}
            sources={sources}
          />
          {message.limited && (
            <LimitNotice text={message.limited} plan={message.limitedBy === "plan"} />
          )}
          {!message.limited && (!message.streaming || message.text) && (
            <AnswerBody message={message} actions={actions} live={live} />
          )}

          {sources.length > 0 && (
            <div className="flex min-w-0 flex-wrap items-center gap-1.5" role="group" aria-label="Sources">
              <span className="mr-0.5 text-xs text-muted-foreground">Sources</span>
              {groupSources(sources).map((group, i) => (
                <div key={`${group[0].source.provider}-${group[0].source.title}`} className="min-w-0 max-w-full" {...rise(i)}>
                  <SourceGroup sources={group} onOpen={setOpened} />
                </div>
              ))}
            </div>
          )}

          {finished && message.state && message.state !== "pending" && (
            <ActionRow message={message} actions={actions} className={live ? "kb-fade" : undefined} />
          )}

          {followups.length > 0 && (
            <div className="flex flex-col items-start gap-1.5" aria-label="Follow-up questions" role="group" data-print="hide">
              {followups.map((f, i) => (
                <Button
                  key={f}
                  variant="outline"
                  size="sm"
                  onClick={() => actions.onFollowup!(f)}
                  {...rise(i, 100)}
                  className={cn(
                    "glass-subtle h-auto max-w-full justify-start whitespace-normal py-1.5 text-left font-normal pointer-coarse:min-h-10",
                    live && "kb-rise",
                  )}
                >
                  <CornerDownRight data-icon="inline-start" className="text-signal" />
                  {f}
                </Button>
              ))}
            </div>
          )}
        </MessageContent>
        <SourceSheet source={opened} onOpenChange={(open) => !open && setOpened(null)} />
      </MessageRow>
    </CitationsContext>
  )
}
