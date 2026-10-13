import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  ArrowUp,
  BookOpen,
  ChevronRight,
  ExternalLink,
  FileText,
  Sparkles,
  Wrench,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { api, type ChatReply, type Role, type Source } from "@/lib/api"

interface Message {
  id: number
  role: "user" | "assistant"
  text: string
  reply?: ChatReply
  error?: boolean
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

function AssistantMessage({ message }: { message: Message }) {
  const reply = message.reply
  return (
    <div className="flex gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Sparkles className="size-4" />
      </div>
      <div className="min-w-0 flex-1 space-y-3 pt-1">
        <div
          className={
            message.error
              ? "text-sm text-destructive"
              : "max-w-none text-sm leading-relaxed [&_a]:underline [&_li]:my-0.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5"
          }
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
        </div>

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

        {reply && reply.steps.length > 0 && (
          <Collapsible>
            <CollapsibleTrigger className="group flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
              <ChevronRight className="size-3.5 transition-transform group-data-[state=open]:rotate-90" />
              <Wrench className="size-3.5" />
              Agent steps ({reply.steps.length} tool call{reply.steps.length === 1 ? "" : "s"})
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 space-y-2">
              {reply.steps.map((step, i) => (
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, loading])

  async function ask(question: string) {
    const q = question.trim()
    if (!q || loading) return
    setInput("")
    setMessages((m) => [...m, { id: nextId.current++, role: "user", text: q }])
    setLoading(true)
    try {
      const reply = await api.chat(q)
      setMessages((m) => [
        ...m,
        { id: nextId.current++, role: "assistant", text: reply.answer, reply },
      ])
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          id: nextId.current++,
          role: "assistant",
          text: err instanceof Error ? err.message : "Something went wrong.",
          error: true,
        },
      ])
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
              <div className="mb-4 flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
                <Sparkles className="size-6" />
              </div>
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

          {loading && (
            <div className="flex gap-3">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                <Sparkles className="size-4 animate-pulse" />
              </div>
              <div className="flex-1 space-y-2 pt-2">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
                <p className="text-xs text-muted-foreground">Searching documents…</p>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t bg-background">
        <form onSubmit={onSubmit} className="mx-auto flex max-w-3xl items-end gap-2 px-4 py-3">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask a question… (Shift+Enter for a new line)"
            className="max-h-40 min-h-11 resize-none"
            rows={1}
          />
          <Button type="submit" size="icon" disabled={loading || !input.trim()} aria-label="Send">
            <ArrowUp />
          </Button>
        </form>
      </div>
    </div>
  )
}
