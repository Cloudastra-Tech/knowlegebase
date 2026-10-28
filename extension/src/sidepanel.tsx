import { StrictMode, useEffect, useRef, useState } from "react"
import { createRoot } from "react-dom/client"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ArrowUp, Loader2, LogOut, PanelRight, Square, TextQuote, TriangleAlert, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

import {
  ask,
  citeMarkers,
  disconnect,
  exchange,
  normalizeHost,
  sourceLink,
  type AskEvent,
  type AskState,
  type Connection,
  type Source,
} from "./api"
import "./panel.css"

// The side panel: connect to a Knowledge assistant, then ask it with cited answers.
// Stored in chrome.storage.local: the server's address and the connection (its API
// key). The text selected through the right-click menu waits in chrome.storage.session
// until it's used or dropped: it goes with one question at most, and only when ticked.

interface Stored {
  host?: string
  connection?: Connection
}
interface Selection {
  text: string
  at: number
}
interface Turn {
  question: string
  usedSelection: boolean
  status: string | null
  answer: string
  sources: Source[]
  state: AskState | null
  error: string | null
  busy: boolean
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const STATE_NOTE: Partial<Record<AskState, string>> = {
  not_found: "Your documents don't answer this.",
  general_knowledge: "Not from your documents: general knowledge.",
  blocked: "This question isn't one the assistant answers.",
  partial: "Your documents answer part of this.",
}

// Follow the system's light / dark setting, as the app does.
function useSystemTheme() {
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const apply = () => document.documentElement.classList.toggle("dark", media.matches)
    apply()
    media.addEventListener("change", apply)
    return () => media.removeEventListener("change", apply)
  }, [])
}

function Connect({ initialHost, onConnected }: { initialHost: string; onConnected: (s: Stored) => void }) {
  const [host, setHost] = useState(initialHost)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function connect(e: React.FormEvent) {
    e.preventDefault()
    setProblem(null)
    let origin: string
    try {
      origin = normalizeHost(host)
    } catch (err) {
      setProblem(errorText(err))
      return
    }
    setBusy(true)
    try {
      // Access to this one server only, asked for now (it's the click's doing).
      if (!(await chrome.permissions.request({ origins: [`${origin}/*`] })))
        throw new Error("The extension needs your permission to reach that address.")
      const redirect = chrome.identity.getRedirectURL()
      const back = await chrome.identity.launchWebAuthFlow({
        url: `${origin}/extension/connect?redirect=${encodeURIComponent(redirect)}`,
        interactive: true,
      })
      const params = new URL(back).searchParams
      const code = params.get("code")
      if (!code) throw new Error(params.get("error") === "cancelled" ? "Connecting was cancelled." : "Connecting didn't finish.")
      const connection = await exchange(origin, code)
      const stored = { host: origin, connection }
      await chrome.storage.local.set(stored)
      onConnected(stored)
    } catch (err) {
      setProblem(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={connect} className="mx-auto grid w-full max-w-sm gap-4 p-5">
      <div className="flex size-10 items-center justify-center rounded-xl bg-muted">
        <PanelRight className="size-5" />
      </div>
      <div>
        <h1 className="text-base font-semibold">Knowledge assistant</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Ask your company's documents from any tab. Connect to your workspace once: you sign in on
          its site, and the extension asks as you.
        </p>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="kb-host">Your Knowledge assistant's address</Label>
        <Input
          id="kb-host"
          value={host}
          onChange={(e) => setHost(e.target.value)}
          placeholder="https://knowledge.example.com"
          autoComplete="url"
          required
        />
      </div>
      {problem && (
        <p className="flex items-start gap-1.5 text-sm text-destructive">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          {problem}
        </p>
      )}
      <Button type="submit" disabled={busy}>
        {busy && <Loader2 className="size-4 animate-spin" />}
        Connect
      </Button>
      <p className="text-xs text-muted-foreground">
        It never reads the pages you visit. Text you select goes along only when you choose.
      </p>
    </form>
  )
}

function Answer({ turn, host }: { turn: Turn; host: string }) {
  const text = citeMarkers(turn.answer, turn.sources, host)
  const note = turn.state ? STATE_NOTE[turn.state] : undefined
  return (
    <div className="grid min-w-0 gap-2">
      <div className="ml-auto max-w-[85%] rounded-2xl bg-muted px-3 py-2 text-sm">
        {turn.question}
        {turn.usedSelection && (
          <span className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
            <TextQuote className="size-3" /> with the selected text
          </span>
        )}
      </div>
      {turn.busy && !turn.answer && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {turn.status ?? "Looking that up…"}
        </p>
      )}
      {turn.answer && (
        <div className="kb-answer min-w-0 text-sm leading-relaxed">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => (
                <a href={href} target="_blank" rel="noreferrer" className="text-primary underline-offset-2 hover:underline">
                  {children}
                </a>
              ),
            }}
          >
            {text}
          </ReactMarkdown>
        </div>
      )}
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
      {turn.sources.length > 0 && (
        <ol className="grid gap-1 border-t border-border/70 pt-2 text-xs">
          {turn.sources.map((s, i) => {
            const link = sourceLink(s, host)
            const label = s.title || "Untitled"
            return (
              <li key={`${s.n ?? "x"}-${i}`} className="flex min-w-0 gap-1.5">
                <span className="shrink-0 font-medium">{typeof s.n === "number" ? `[${s.n}]` : "·"}</span>
                <span className="min-w-0 truncate">
                  {link ? (
                    <a href={link} target="_blank" rel="noreferrer" className="hover:underline">
                      {label}
                    </a>
                  ) : (
                    label
                  )}
                  {s.provider && <span className="text-muted-foreground"> · {s.provider}</span>}
                </span>
              </li>
            )
          })}
        </ol>
      )}
      {turn.error && (
        <p className="flex items-start gap-1.5 text-sm text-destructive">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          {turn.error}
        </p>
      )}
    </div>
  )
}

function Ask({ stored, onDisconnected }: { stored: Required<Stored>; onDisconnected: () => void }) {
  const { host, connection } = stored
  const [question, setQuestion] = useState("")
  const [selection, setSelection] = useState<Selection | null>(null)
  const [useSelection, setUseSelection] = useState(true)
  const [turns, setTurns] = useState<Turn[]>([])
  const stop = useRef<AbortController | null>(null)
  const end = useRef<HTMLDivElement | null>(null)
  const busy = turns.some((t) => t.busy)

  // Text picked with the right-click menu, now and while the panel is open.
  useEffect(() => {
    chrome.storage.session.get("selection").then((got) => {
      if (got.selection) setSelection(got.selection as Selection)
    })
    const changed = (changes: Record<string, { newValue?: unknown }>, area: string) => {
      if (area !== "session" || !("selection" in changes)) return
      setSelection((changes.selection.newValue as Selection | undefined) ?? null)
      setUseSelection(true)
    }
    chrome.storage.onChanged.addListener(changed)
    return () => chrome.storage.onChanged.removeListener(changed)
  }, [])

  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [turns])

  function dropSelection() {
    setSelection(null)
    void chrome.storage.session.remove("selection")
  }

  function update(i: number, patch: Partial<Turn>) {
    setTurns((all) => all.map((t, j) => (j === i ? { ...t, ...patch } : t)))
  }

  async function submit(e?: React.FormEvent) {
    e?.preventDefault()
    const q = question.trim()
    if (!q || busy) return
    const context = selection && useSelection ? selection.text : null
    const i = turns.length
    setTurns((all) => [
      ...all,
      { question: q, usedSelection: !!context, status: null, answer: "", sources: [], state: null, error: null, busy: true },
    ])
    setQuestion("")
    if (context) dropSelection() // used once, with this question only
    const controller = new AbortController()
    stop.current = controller
    try {
      const done = await ask(host, connection.key, q, {
        context,
        signal: controller.signal,
        onEvent: (event: AskEvent) => {
          if (event.type === "status") update(i, { status: event.text ?? event.message ?? null })
          else if (event.type === "step_start" && event.label) update(i, { status: event.label })
          else if (event.type === "answer") update(i, { answer: event.text })
        },
      })
      update(i, { answer: done.answer, sources: done.sources ?? [], state: done.state, busy: false })
    } catch (err) {
      const stopped = controller.signal.aborted
      update(i, { busy: false, error: stopped ? "Stopped." : errorText(err) })
      if (err instanceof Error && "status" in err && err.status === 401) {
        await chrome.storage.local.remove("connection")
        onDisconnected()
      }
    } finally {
      stop.current = null
    }
  }

  async function signOut() {
    try {
      await disconnect(host, connection.key)
    } catch {
      // gone here either way; it can still be revoked in the app
    }
    await chrome.storage.local.remove("connection")
    onDisconnected()
  }

  return (
    <div className="flex h-dvh min-w-0 flex-col">
      <header className="flex items-center gap-2 border-b border-border/70 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">Knowledge assistant</p>
          <p className="truncate text-xs text-muted-foreground">
            {connection.name} · {new URL(host).host}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={signOut} title="Disconnect this browser">
          <LogOut className="size-4" />
          Disconnect
        </Button>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {turns.length === 0 ? (
          <p className="mt-6 text-center text-sm text-muted-foreground">
            Ask about your company's documents. To ask about text on a page, select it, right-click
            and choose Ask Knowledge about the selection.
          </p>
        ) : (
          <div className="grid gap-5">
            {turns.map((t, i) => (
              <Answer key={i} turn={t} host={host} />
            ))}
          </div>
        )}
        <div ref={end} />
      </main>

      <form onSubmit={submit} className="grid gap-2 border-t border-border/70 p-3">
        {selection && (
          <div className="grid gap-1.5 rounded-xl border border-border/70 bg-muted/40 p-2.5">
            <div className="flex items-center gap-2">
              <Checkbox
                id="kb-use-selection"
                checked={useSelection}
                onCheckedChange={(v) => setUseSelection(v === true)}
              />
              <Label htmlFor="kb-use-selection" className="flex-1 text-xs font-normal">
                Use the selected text with this question
              </Label>
              <Button type="button" variant="ghost" size="icon" className="size-6" onClick={dropSelection} aria-label="Drop the selected text">
                <X className="size-3.5" />
              </Button>
            </div>
            <p className="line-clamp-3 text-xs break-words text-muted-foreground">{selection.text}</p>
          </div>
        )}
        <div className="flex items-end gap-2">
          <Textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void submit()
              }
            }}
            placeholder="Ask a question"
            rows={2}
            maxLength={4000}
            className="max-h-40 min-h-10 resize-none"
            aria-label="Your question"
          />
          {busy ? (
            <Button type="button" size="icon" variant="outline" onClick={() => stop.current?.abort()} aria-label="Stop">
              <Square className="size-4" />
            </Button>
          ) : (
            <Button type="submit" size="icon" disabled={!question.trim()} aria-label="Ask">
              <ArrowUp className="size-4" />
            </Button>
          )}
        </div>
      </form>
    </div>
  )
}

function Panel() {
  useSystemTheme()
  const [stored, setStored] = useState<Stored | null>(null)

  useEffect(() => {
    chrome.storage.local.get(["host", "connection"]).then((got) => setStored(got as Stored))
  }, [])

  if (!stored) return null
  if (stored.host && stored.connection)
    return (
      <Ask
        stored={stored as Required<Stored>}
        onDisconnected={() => setStored({ host: stored.host })}
      />
    )
  return <Connect initialHost={stored.host ?? ""} onConnected={setStored} />
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Panel />
  </StrictMode>,
)
