import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useMatch, useNavigate } from "react-router"
import { History, Info, MoreHorizontal, Pencil, SquarePen, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { AssistantMessage, type AnswerActions, type Message } from "@/components/chat-answer"
import { Composer } from "@/components/chat-composer"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { LoggingNotice } from "@/components/logging-notice"
import {
  api,
  ApiError,
  type ConversationSummary,
  type Role,
  type SavedMessage,
  type StreamEvent,
} from "@/lib/api"
import { startersFrom } from "@/lib/chat-starters"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// A saved conversation's messages as the chat shows them.
function fromSaved(saved: SavedMessage[]): Message[] {
  let question = ""
  return saved.map((m) => {
    if (m.author === "user") {
      question = m.content
      return { key: m.id, id: m.id, role: "user", text: m.content }
    }
    return {
      key: m.id,
      id: m.id,
      role: "assistant",
      text: m.content,
      state: m.state ?? "pending",
      question,
      sources: m.sources,
      trace: m.trace,
      standalone: m.standalone_question,
      withheld: m.withheld,
      feedback: m.feedback,
      steps: (m.steps ?? []).map((s) => ({ ...s, done: true })),
    }
  })
}

// The conversation being added to on this screen: what it shows until the person
// opens another one (or the role changes), instead of the saved copy.
interface Session {
  id: string | null // null: not saved yet (the first question is on its way)
  role: Role
  messages: Message[]
  conversation: ConversationSummary | null
  notice: string | null
}

const isAbort = (err: unknown) => err instanceof DOMException && err.name === "AbortError"

// The chat. /chat is a new conversation; /chat/:conversationId a saved one, loaded
// from the server (so a reload shows it again). The first question of a new one
// saves it and moves the address to its id.
export function ChatView({ role }: { role: Role }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const urlId = useMatch("/chat/:conversationId")?.params.conversationId ?? null
  const [session, setSession] = useState<Session | null>(null)
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const nextKey = useRef(1)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const knowledge = useQuery({ ...queries.knowledge(), meta: { silent: true } })

  // What's on screen: this screen's own session while it is the one at the address
  // (and the role it began under), else the saved conversation there, if any.
  const live = session !== null && session.id === urlId && session.role === role
  const saved = useQuery({
    ...queries.conversation(urlId ?? ""),
    enabled: urlId !== null && !live,
    staleTime: 0,
  })
  const shown = live ? session : null
  const messages = shown ? shown.messages : urlId && saved.data ? fromSaved(saved.data.messages) : []
  const conversation = shown ? shown.conversation : urlId ? (saved.data ?? null) : null
  const opening = urlId !== null && !live && saved.isPending

  // A conversation that isn't there (deleted, someone else's): back to a new chat.
  useEffect(() => {
    if (saved.isError && !live) navigate("/chat", { replace: true })
  }, [saved.isError, live, navigate])

  const lastText = messages.at(-1)?.text
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    bottomRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" })
  }, [messages.length, lastText])

  // Esc stops an answer being written, wherever the focus is.
  useEffect(() => {
    if (!loading) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") abort.current?.abort()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [loading])

  // Change the messages on screen; a saved conversation becomes this screen's session.
  function edit(fn: (all: Message[]) => Message[]) {
    setSession({ id: urlId, role, messages: fn(messages), conversation, notice: null })
  }

  // Leave for another conversation (or a new one), stopping an answer being written.
  function open(to: string) {
    abort.current?.abort()
    setSession(null)
    navigate(to)
  }

  // Ask a question, or (retry) ask a failed or stopped answer's question again in its place.
  async function ask(text: string, retry?: Message) {
    const q = text.trim()
    if (!q || loading) return
    if (!retry) setInput("")
    const key = `draft-${nextKey.current++}`
    const questionKey = `draft-${nextKey.current++}`
    const draft: Message = {
      key,
      role: "assistant",
      text: "",
      question: q,
      streaming: true,
      status: "Thinking",
      steps: [],
    }
    edit((all) =>
      retry
        ? all.map((m) => (m.key === retry.key ? draft : m))
        : [...all, { key: questionKey, role: "user", text: q }, draft],
    )
    setLoading(true)
    const change = (fn: (s: Session) => Session) => setSession((s) => (s ? fn(s) : s))
    const update = (fn: (m: Message) => Message) =>
      change((s) => ({ ...s, messages: s.messages.map((m) => (m.key === key ? fn(m) : m)) }))
    const allDone = (m: Message) => (m.steps ?? []).map((st) => ({ ...st, done: true }))
    // The address follows the session in the same render, so it never shows another.
    const go = (id: string) => navigate(`/chat/${id}`, { flushSync: true })

    function onEvent(e: StreamEvent) {
      if (e.type === "conversation") {
        update((m) => ({ ...m, id: e.message_id }))
        change((s) => ({
          ...s,
          // A new chat takes its first question as its title.
          conversation: s.conversation && { ...s.conversation, title: e.title ?? s.conversation.title },
          messages: s.messages.map((m) =>
            m.key === questionKey ? { ...m, id: e.user_message_id } : m,
          ),
        }))
        if (e.new_conversation) {
          // The role changed since this chat began: the question went to a new one.
          change((s) => ({
            ...s,
            id: e.id,
            conversation: null,
            notice:
              "You're viewing the app with a different role now, so this question started a new chat.",
            messages: [
              { key: questionKey, id: e.user_message_id, role: "user", text: q },
              ...s.messages.filter((m) => m.key === key),
            ],
          }))
          go(e.id)
        }
      } else if (e.type === "status") update((m) => ({ ...m, status: e.text }))
      else if (e.type === "step_start")
        update((m) => ({
          ...m,
          steps: [...(m.steps ?? []), { id: e.id, tool: e.tool, label: e.label, done: false }],
        }))
      else if (e.type === "step_end")
        update((m) => ({
          ...m,
          steps: (m.steps ?? []).map((st) =>
            st.id === e.id ? { ...st, done: true, result: e.result } : st,
          ),
        }))
      else if (e.type === "answer") update((m) => ({ ...m, text: e.text }))
    }

    const controller = new AbortController()
    abort.current = controller
    try {
      let conversationId = urlId
      if (!conversationId) {
        const created = await api.createConversation()
        conversationId = created.id
        change((s) => ({ ...s, id: created.id, conversation: created }))
        go(created.id)
      }
      const reply = await api.chatStream(retry ? "" : q, onEvent, {
        conversationId,
        retryOf: retry?.id,
        signal: controller.signal,
      })
      update((m) => ({
        ...m,
        id: reply.message_id ?? m.id,
        text: reply.answer, // the final, checked answer replaces the streamed draft
        state: reply.state,
        sources: reply.sources,
        trace: reply.steps,
        standalone: reply.standalone_question,
        streaming: false,
        steps: allDone(m),
      }))
    } catch (err) {
      if (isAbort(err) || controller.signal.aborted) {
        // Stopped: keep what was written (the server saves it the same way).
        update((m) => ({ ...m, state: "stopped", streaming: false, steps: allDone(m) }))
      } else if (err instanceof ApiError && err.status === 429) {
        // Nothing was asked: give the question back to send again in a moment.
        update((m) => ({ ...m, limited: err.message, streaming: false }))
        if (!retry) setInput((current) => current || q)
      } else {
        update((m) => ({
          ...m,
          text: err instanceof Error ? err.message : "Something went wrong.",
          state: "error",
          streaming: false,
          steps: allDone(m),
        }))
      }
    } finally {
      if (abort.current === controller) abort.current = null
      setLoading(false)
      queryClient.invalidateQueries({ queryKey: ["conversations"] })
    }
  }

  const actions: AnswerActions = {
    onRetry: (m) => ask(m.question ?? "", m),
    onRephrase: (question) => {
      setInput(question)
      inputRef.current?.focus()
    },
    onFeedback: (message, feedback) =>
      edit((all) => all.map((m) => (m.key === message.key ? { ...m, feedback } : m))),
  }

  const starters = startersFrom(knowledge.data?.items)
  const outline = knowledge.data?.outline ?? false
  const empty = messages.length === 0 && !opening

  return (
    <div className="flex h-full flex-col">
      <ChatHeader
        conversation={conversation}
        busy={loading}
        onRenamed={(title) =>
          setSession({
            id: urlId,
            role,
            messages,
            conversation: conversation && { ...conversation, title },
            notice: null,
          })
        }
        onOpen={(to) => {
          open(to)
          if (to === "/chat") inputRef.current?.focus()
        }}
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
          {empty && (
            <div className="flex flex-col items-center pt-8 text-center sm:pt-16">
              <h2 className="text-2xl font-semibold tracking-tight text-balance">
                Ask your company docs
              </h2>
              <p className="mt-2 max-w-md text-sm text-muted-foreground">
                Answers come from your company's documents
                {outline ? " and the Outline wiki" : ""}, with the sources cited under every
                answer.
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Searching as{" "}
                <span className="font-medium">{role === "hr" ? "HR / Admin" : "Public user"}</span>
              </p>
              <LoggingNotice />
              <div className="mt-8 grid w-full max-w-xl gap-2">
                {starters.map((s) => (
                  <Button
                    key={s}
                    variant="outline"
                    onClick={() => ask(s)}
                    className="h-auto justify-start whitespace-normal rounded-lg px-4 py-3 text-left font-normal"
                  >
                    {s}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {shown?.notice && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Info className="size-3.5 shrink-0" />
              {shown.notice}
            </p>
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.key} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                  {m.text}
                </div>
              </div>
            ) : (
              <AssistantMessage key={m.key} message={m} actions={actions} />
            ),
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      <div className="pointer-events-none bg-gradient-to-t from-background via-background to-transparent pt-6">
        {conversation?.role_changed && !loading && (
          <p className="pointer-events-auto mx-auto mb-2 flex max-w-3xl items-center gap-2 px-4 text-xs text-muted-foreground">
            <Info className="size-3.5 shrink-0" />
            This chat began with another role. Your next question starts a new chat.
          </p>
        )}
        <Composer
          value={input}
          onChange={setInput}
          onSend={() => ask(input)}
          onStop={() => abort.current?.abort()}
          busy={loading}
          inputRef={inputRef}
        />
      </div>
    </div>
  )
}

// Above the chat: its title, the recent chats, New chat, and Rename / Delete.
function ChatHeader({
  conversation,
  busy,
  onRenamed,
  onOpen,
}: {
  conversation: ConversationSummary | null
  busy: boolean
  onRenamed: (title: string) => void
  onOpen: (to: string) => void // another chat's address, or /chat for a new one
}) {
  const queryClient = useQueryClient()
  const [historyOpen, setHistoryOpen] = useState(false)
  const recent = useQuery({ ...queries.conversations(), enabled: historyOpen })
  const [renaming, setRenaming] = useState(false)
  const [title, setTitle] = useState("")
  const [deleting, setDeleting] = useState(false)

  async function rename() {
    if (!conversation || !title.trim()) return
    try {
      const renamed = await api.renameConversation(conversation.id, title)
      setRenaming(false)
      queryClient.invalidateQueries({ queryKey: ["conversations"] })
      onRenamed(renamed.title)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't rename it.")
    }
  }

  async function remove() {
    if (!conversation) return
    try {
      await api.deleteConversation(conversation.id)
      queryClient.invalidateQueries({ queryKey: ["conversations"] })
      toast.success("Chat deleted.")
      onOpen("/chat")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't delete it.")
    }
  }

  return (
    <div className="mx-auto flex h-11 w-full max-w-3xl shrink-0 items-center gap-1 px-4">
      <p className="min-w-0 flex-1 truncate text-sm font-medium">{conversation?.title ?? ""}</p>
      <DropdownMenu open={historyOpen} onOpenChange={setHistoryOpen}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="text-muted-foreground" aria-label="History">
            <History />
            <span className="hidden sm:inline">History</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
            Recent chats
          </DropdownMenuLabel>
          {recent.isPending && <DropdownMenuItem disabled>Loading…</DropdownMenuItem>}
          {recent.data?.length === 0 && <DropdownMenuItem disabled>No saved chats yet</DropdownMenuItem>}
          {recent.data?.slice(0, 15).map((c) => (
            <DropdownMenuItem key={c.id} onSelect={() => onOpen(`/chat/${c.id}`)}>
              <span className="min-w-0 flex-1 truncate">{c.title}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(c.updated_at)}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <Button
        variant="ghost"
        size="sm"
        className="text-muted-foreground"
        aria-label="New chat"
        onClick={() => onOpen("/chat")}
      >
        <SquarePen />
        <span className="hidden sm:inline">New chat</span>
      </Button>
      {conversation && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label="Chat options" className="text-muted-foreground">
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() => {
                setTitle(conversation.title)
                setRenaming(true)
              }}
            >
              <Pencil /> Rename
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" disabled={busy} onSelect={() => setDeleting(true)}>
              <Trash2 /> Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      <Dialog open={renaming} onOpenChange={setRenaming}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Rename chat</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              rename()
            }}
          >
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              aria-label="Chat name"
              maxLength={80}
              autoFocus
            />
            <DialogFooter className="mt-4">
              <Button type="submit" disabled={!title.trim()}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <AlertDialog open={deleting} onOpenChange={setDeleting}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this chat?</AlertDialogTitle>
            <AlertDialogDescription>
              Its questions, answers and any feedback you gave on them are deleted for good.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={remove}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
