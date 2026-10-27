import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useLocation, useMatch, useNavigate, useSearchParams } from "react-router"
import { ArrowDown, CornerUpLeft, Info, MoreHorizontal, Pencil, Share2, SquarePen, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { AttachButton, AttachmentChips } from "@/components/attachment-chips"
import { AssistantMessage, BranchNav, type AnswerActions, type Message } from "@/components/chat-answer"
import { Composer } from "@/components/chat-composer"
import { ChatSidebar } from "@/components/chat-sidebar"
import { LoggingNotice } from "@/components/logging-notice"
import { usePromptPicker } from "@/components/prompt-menu"
import { ShareDialog } from "@/components/share-dialog"
import { SpaceChip } from "@/components/space-switcher"
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { SidebarProvider, SidebarTrigger, useSidebar } from "@/components/ui/sidebar"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  api,
  ApiError,
  spaces,
  type AnswerState,
  type Attachment,
  type Conversation,
  type ConversationSummary,
  type Role,
  type SavedMessage,
  type StreamEvent,
} from "@/lib/api"
import { useUser } from "@/lib/auth-context"
import { CHOOSE_SPACE, FOCUS_COMPOSER, TOGGLE_SIDEBAR } from "@/lib/chat-events"
import { startersFrom } from "@/lib/chat-starters"
import { queries } from "@/lib/queries"
import { spaceLabel } from "@/lib/spaces"

// A saved conversation's messages as the chat shows them. `keys`: the React keys
// messages already on screen have, by id, so they aren't drawn anew.
function fromSaved(saved: SavedMessage[], keys: Map<string, string> = new Map()): Message[] {
  let question = ""
  return saved.map((m) => {
    const key = keys.get(m.id) ?? m.id
    if (m.author === "user") {
      question = m.content
      return { key, id: m.id, role: "user", text: m.content, siblings: m.siblings }
    }
    return {
      key,
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
      followups: m.followups,
      siblings: m.siblings,
      verified: m.verified,
      steps: (m.steps ?? []).map((s) => ({ ...s, done: true })),
    }
  })
}

const keysOf = (messages: Message[]) =>
  new Map(messages.filter((m) => m.id).map((m) => [m.id!, m.key]))

// The conversation being added to on this screen: what it shows until the person
// opens another one (or the role changes), instead of the saved copy.
interface Session {
  id: string | null // null: not saved yet (the first question is on its way)
  role: Role
  messages: Message[]
  conversation: Conversation | ConversationSummary | null
  notice: string | null
}

// How asking again relates to what's on screen.
interface Again {
  retry?: Message // a failed or stopped answer, asked again
  regenerate?: Message // a finished answer, written again beside it
  edit?: Message // a question, replaced by the new text
}

const isAbort = (err: unknown) => err instanceof DOMException && err.name === "AbortError"

// The space new chats are asked in, remembered on this device (chat.md C31).
const SPACE_KEY = "kb.space"
function storedSpace() {
  try {
    return localStorage.getItem(SPACE_KEY)
  } catch {
    return null
  }
}
function storeSpace(id: string | null) {
  try {
    if (id) localStorage.setItem(SPACE_KEY, id)
    else localStorage.removeItem(SPACE_KEY)
  } catch {
    /* private mode: just not remembered */
  }
}

// What the screen reader hears when an answer ends (not every word as it streams).
const ENDED: Partial<Record<AnswerState, string>> = {
  answered: "Answer ready.",
  partial: "Answer ready. Partly answered from your documents.",
  general_knowledge: "Answer ready, from general knowledge.",
  not_found: "Not found in your documents.",
  blocked: "The question can't be answered.",
  error: "Couldn't answer.",
  stopped: "Answer stopped.",
}

const typing = (el: Element | null) =>
  el instanceof HTMLElement &&
  (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))

// The palette's "Toggle sidebar" (the sidebar's own ⌘B works too).
function SidebarEvents() {
  const { toggleSidebar } = useSidebar()
  useEffect(() => {
    window.addEventListener(TOGGLE_SIDEBAR, toggleSidebar)
    return () => window.removeEventListener(TOGGLE_SIDEBAR, toggleSidebar)
  }, [toggleSidebar])
  return null
}

// The chat. /chat is a new conversation; /chat/:conversationId a saved one, loaded
// from the server (so a reload shows it again). The first question of a new one
// saves it and moves the address to its id. The history sidebar is on its left
// (a Sheet on phones).
export function ChatView({ role }: { role: Role }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const urlId = useMatch("/chat/:conversationId")?.params.conversationId ?? null
  const onChat = location.pathname === "/chat" || location.pathname.startsWith("/chat/")
  const [session, setSession] = useState<Session | null>(null)
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<string | null>(null) // a question's key
  const [sharing, setSharing] = useState<{ messageId: string | null } | null>(null)
  const [announce, setAnnounce] = useState("")
  const [atBottom, setAtBottom] = useState(true)
  const abort = useRef<AbortController | null>(null)
  const nextKey = useRef(1)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const lastAnswerRef = useRef<HTMLDivElement>(null)
  const knowledge = useQuery({ ...queries.knowledge(), meta: { silent: true } })
  const list = useQuery(queries.conversations())
  // The workspace's admin may hide the steps panel from members (HR and admins keep it).
  const account = useUser()
  const assistantInfo = useQuery(queries.assistantInfo()).data
  const hideSteps = assistantInfo?.show_steps === false && account.role === "public"
  const spaceList = useQuery({ ...queries.spaces(), meta: { silent: true } })
  const limits = useQuery({ ...queries.attachmentLimits(), meta: { silent: true } }).data
  const [chosenSpace, setChosenSpace] = useState<string | null>(storedSpace)
  const [uploading, setUploading] = useState<string[]>([])

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
  const listed = list.data?.conversations.find((c) => c.id === urlId)
  const title = listed?.title ?? conversation?.title ?? ""
  const forkedFrom = (conversation as Conversation | null)?.forked_from ?? null
  const opening = urlId !== null && !live && saved.isPending
  // The space this chat is asked in: a saved one's own; a new one's, as chosen
  // (while that space is still one of yours).
  const chosen =
    chosenSpace && (spaceList.isPending || spaceList.data?.spaces.some((s) => s.id === chosenSpace))
      ? chosenSpace
      : null
  const spaceId = urlId ? (listed?.space_id ?? conversation?.space_id ?? null) : chosen
  const space = spaceList.data?.spaces.find((s) => s.id === spaceId) ?? null
  const attached = useQuery({ ...queries.attachments(urlId ?? ""), enabled: urlId !== null })
  const files: Attachment[] = urlId ? (attached.data ?? []) : []
  const picker = usePromptPicker({ input, setInput, inputRef })

  // A conversation that isn't there (deleted, someone else's): back to a new chat.
  useEffect(() => {
    if (saved.isError && !live) navigate("/chat", { replace: true })
  }, [saved.isError, live, navigate])

  // /chat?q=...: a question to ask, filled in (a shared answer's "Ask it yourself").
  // Taken once per navigation (its location key), while rendering; then the
  // address drops it.
  const prefill = onChat ? params.get("q") : null
  const [prefilled, setPrefilled] = useState<string | null>(null)
  if (prefill !== null && prefilled !== location.key) {
    setPrefilled(location.key)
    setInput(prefill.slice(0, 2000))
  }
  useEffect(() => {
    if (prefill === null) return
    setParams({}, { replace: true })
    inputRef.current?.focus()
  }, [prefill, setParams])

  // Follow the answer down only while the reader is at the bottom; otherwise offer
  // "Jump to latest". No smooth scrolling for people who asked for less motion.
  const scrollToEnd = useCallback((smooth = true) => {
    const el = scrollRef.current
    if (!el) return
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    el.scrollTo({ top: el.scrollHeight, behavior: smooth && !reduce ? "smooth" : "auto" })
  }, [])
  const lastText = messages.at(-1)?.text
  useEffect(() => {
    if (atBottom) scrollToEnd()
  }, [messages.length, lastText, atBottom, scrollToEnd])
  // Another chat opens at its end (scrolling there sets atBottom again).
  useEffect(() => {
    scrollToEnd(false)
  }, [urlId, scrollToEnd])

  // Esc stops an answer being written, wherever the focus is.
  useEffect(() => {
    if (!loading) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") abort.current?.abort()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [loading])

  // "/" focuses the composer (when not typing elsewhere); ⌘⇧C copies the last answer.
  const lastAnswer = [...messages].reverse().find((m) => m.role === "assistant")
  useEffect(() => {
    if (!onChat) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !typing(document.activeElement)) {
        e.preventDefault()
        inputRef.current?.focus()
      } else if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "c" && lastAnswer?.id) {
        if (typing(document.activeElement) && window.getSelection()?.toString()) return
        e.preventDefault()
        api
          .answerMarkdown(lastAnswer.id)
          .then((text) => navigator.clipboard.writeText(text))
          .then(() => toast.success("Copied the last answer, with its sources."))
          .catch(() => toast.error("Couldn't copy the last answer."))
      }
    }
    const focus = () => inputRef.current?.focus()
    window.addEventListener("keydown", onKey)
    window.addEventListener(FOCUS_COMPOSER, focus)
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener(FOCUS_COMPOSER, focus)
    }
  }, [onChat, lastAnswer?.id])

  // Change the messages on screen; a saved conversation becomes this screen's session.
  function edit(fn: (all: Message[]) => Message[]) {
    setSession({ id: urlId, role, messages: fn(messages), conversation, notice: null })
  }

  // Leave for another conversation (or a new one), stopping an answer being written.
  function open(to: string) {
    abort.current?.abort()
    setSession(null)
    setEditing(null)
    setAtBottom(true)
    navigate(to)
  }

  function newChat() {
    open("/chat")
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  // Ask in another space: new chats go there; a chat already under way stays in
  // its own, so choosing another starts a new chat.
  function chooseSpace(id: string | null) {
    storeSpace(id)
    setChosenSpace(id)
    if (urlId && id !== spaceId) newChat()
  }

  // "Ask in this space" (the spaces screen).
  useEffect(() => {
    const onChoose = (e: Event) => {
      const id = (e as CustomEvent<string | null>).detail
      storeSpace(id)
      setChosenSpace(id)
    }
    window.addEventListener(CHOOSE_SPACE, onChoose)
    return () => window.removeEventListener(CHOOSE_SPACE, onChoose)
  }, [])

  // A new chat is saved (in the chosen space) before its first question or file.
  async function saveNewChat() {
    const created = await api.createConversation(chosen)
    setSession({ id: created.id, role, messages, conversation: created, notice: null })
    navigate(`/chat/${created.id}`, { flushSync: true })
    return created.id
  }

  // Attach files to this chat: read on the server into text for this chat only.
  async function attachFiles(list: File[]) {
    if (loading || uploading.length) return
    const room = (limits?.per_chat ?? 10) - files.length
    if (list.length > room) {
      toast.error(
        room > 0
          ? `You can attach ${room} more file${room === 1 ? "" : "s"} to this chat.`
          : "This chat has as many files as it can take.",
      )
      return
    }
    setUploading(list.map((f) => f.name))
    try {
      const id = urlId ?? (await saveNewChat())
      await spaces.attach(id, list)
      await queryClient.invalidateQueries({ queryKey: ["attachments", id] })
      setAnnounce(`Attached ${list.length === 1 ? list[0].name : `${list.length} files`}.`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't attach the files.")
    } finally {
      setUploading([])
      inputRef.current?.focus()
    }
  }

  async function detach(file: Attachment) {
    if (!urlId) return
    try {
      await spaces.detach(urlId, file.id)
      queryClient.invalidateQueries({ queryKey: ["attachments", urlId] })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't remove the file.")
    }
  }

  // The saved conversation again (after a new branch, or switching to another one),
  // keeping what's on screen drawn where it's the same.
  async function reload(id: string) {
    const fresh = await queryClient.fetchQuery({ ...queries.conversation(id), staleTime: 0 })
    setSession((s) =>
      s && s.id === id
        ? { ...s, messages: fromSaved(fresh.messages, keysOf(s.messages)), conversation: fresh }
        : s,
    )
  }

  // Ask a question. Or ask again (see Again): the answer, or the edited question,
  // and everything after it make way for the new branch.
  async function ask(text: string, again: Again = {}) {
    const target = again.retry ?? again.regenerate
    const q = (target ? (target.question ?? "") : text).trim()
    if (!q || loading) return
    if (!target && !again.edit) setInput("")
    setEditing(null)
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
    const cut = (all: Message[], m: Message) => all.slice(0, all.findIndex((x) => x.key === m.key))
    edit((all) =>
      target
        ? [...cut(all, target), draft]
        : again.edit
          ? [...cut(all, again.edit), { key: questionKey, role: "user", text: q }, draft]
          : [...all, { key: questionKey, role: "user", text: q }, draft],
    )
    setLoading(true)
    setAnnounce("Writing an answer…")
    setAtBottom(true)
    const change = (fn: (s: Session) => Session) => setSession((s) => (s ? fn(s) : s))
    const update = (fn: (m: Message) => Message) =>
      change((s) => ({ ...s, messages: s.messages.map((m) => (m.key === key ? fn(m) : m)) }))
    const allDone = (m: Message) => (m.steps ?? []).map((st) => ({ ...st, done: true }))
    // The address follows the session in the same render, so it never shows another.
    const go = (id: string) => navigate(`/chat/${id}`, { flushSync: true })
    let savedIn: string | null = urlId

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
          savedIn = e.id
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
      } else if (e.type === "title") {
        change((s) => ({ ...s, conversation: s.conversation && { ...s.conversation, title: e.title } }))
        queryClient.invalidateQueries({ queryKey: ["conversations"] })
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
      else if (e.type === "answer")
        // The passages its [S#] chips cite come along whenever they change.
        update((m) => ({ ...m, text: e.text, ...(e.sources ? { sources: e.sources } : {}) }))
    }

    const controller = new AbortController()
    abort.current = controller
    let ended: AnswerState | null = null
    try {
      let conversationId = urlId
      if (!conversationId) {
        const created = await api.createConversation(chosen)
        conversationId = created.id
        savedIn = created.id
        change((s) => ({ ...s, id: created.id, conversation: created }))
        go(created.id)
      }
      // Asked again: the server takes the question from the answer being replaced.
      const reply = await api.chatStream(target ? "" : q, onEvent, {
        conversationId,
        retryOf: again.retry?.id,
        regenerateOf: again.regenerate?.id,
        editOf: again.edit?.id,
        signal: controller.signal,
      })
      ended = reply.state
      update((m) => ({
        ...m,
        id: reply.message_id ?? m.id,
        text: reply.answer, // the final, checked answer replaces the streamed draft
        state: reply.state,
        sources: reply.sources,
        trace: reply.steps,
        standalone: reply.standalone_question,
        followups: reply.followups,
        verified: reply.verified,
        streaming: false,
        steps: allDone(m),
      }))
      // The versions beside it ("‹ 2 / 3 ›") come from the saved conversation.
      if (savedIn) reload(savedIn).catch(() => {})
    } catch (err) {
      if (isAbort(err) || controller.signal.aborted) {
        // Stopped: keep what was written (the server saves it the same way), and
        // take the focus to it.
        ended = "stopped"
        update((m) => ({ ...m, state: "stopped", streaming: false, steps: allDone(m) }))
        requestAnimationFrame(() => lastAnswerRef.current?.focus())
      } else if (err instanceof ApiError && err.status === 429) {
        // Nothing was asked: give the question back to send again in a moment.
        update((m) => ({ ...m, limited: err.message, streaming: false }))
        if (!target && !again.edit) setInput((current) => current || q)
      } else {
        ended = "error"
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
      setAnnounce(ended ? (ENDED[ended] ?? "Answer ready.") : "")
      queryClient.invalidateQueries({ queryKey: ["conversations"] })
    }
  }

  // Show another version of a question or an answer.
  async function showBranch(message: Message, to: string) {
    if (!urlId || loading) return
    try {
      await api.updateConversation(urlId, { active_leaf_id: to })
      const fresh = await queryClient.fetchQuery({ ...queries.conversation(urlId), staleTime: 0 })
      setSession({
        id: urlId,
        role,
        messages: fromSaved(fresh.messages, keysOf(messages)),
        conversation: fresh,
        notice: null,
      })
      setAnnounce(`Showing version ${(message.siblings ?? []).indexOf(to) + 1} of ${message.siblings?.length ?? 1}.`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't show that version.")
    }
  }

  async function fork(message: Message) {
    if (!urlId || !message.id) return
    try {
      const created = await api.forkConversation(urlId, message.id)
      queryClient.invalidateQueries({ queryKey: ["conversations"] })
      open(`/chat/${created.id}`)
      toast.success("Branched into a new chat.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't branch into a new chat.")
    }
  }

  const actions: AnswerActions = {
    onRetry: (m) => ask("", { retry: m }),
    onRegenerate: (m) => ask("", { regenerate: m }),
    onRephrase: (question) => {
      setInput(question)
      inputRef.current?.focus()
    },
    onFeedback: (message, feedback) =>
      edit((all) => all.map((m) => (m.key === message.key ? { ...m, feedback } : m))),
    onShare: (m) => setSharing({ messageId: m.id ?? null }),
    onFork: fork,
    onBranch: showBranch,
    onFollowup: (question) => ask(question),
    // HR and admins, viewing as HR: start a verified answer from this one (PL-27).
    onVerify:
      role === "hr" && account.role !== "public"
        ? (m) => m.id && navigate(`/admin/verified?from_message=${encodeURIComponent(m.id)}`)
        : undefined,
    busy: loading,
    hideSteps,
  }

  // Starters come from the whole workspace: not offered inside a space.
  const starters = space ? [] : startersFrom(knowledge.data?.items)
  const outline = knowledge.data?.outline ?? false
  const empty = messages.length === 0 && !opening
  const lastUser = [...messages].reverse().find((m) => m.role === "user")

  return (
    <SidebarProvider className="relative h-full min-h-0">
      <SidebarEvents />
      <ChatSidebar
        activeId={urlId}
        spaceId={spaceId}
        onSpace={chooseSpace}
        busy={loading}
        onOpen={(id) => open(`/chat/${id}`)}
        onNewChat={newChat}
        onDeleted={(id) => id === urlId && open("/chat")}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <ChatHeader
          conversation={conversation}
          title={title}
          busy={loading}
          onRenamed={(t) =>
            setSession({
              id: urlId,
              role,
              messages,
              conversation: conversation && { ...conversation, title: t },
              notice: null,
            })
          }
          onShare={() => setSharing({ messageId: null })}
          onNewChat={newChat}
          onDeleted={() => open("/chat")}
        />
        <div
          ref={scrollRef}
          className="relative flex-1 overflow-y-auto"
          onScroll={(e) => {
            const el = e.currentTarget
            setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
          }}
        >
          <div
            className="mx-auto max-w-3xl space-y-6 px-4 py-6"
            role="log"
            aria-label="Conversation"
            aria-busy={loading}
          >
            {empty && (
              <div className="flex flex-col items-center pt-8 text-center sm:pt-16">
                <h2 className="text-2xl font-semibold tracking-tight text-balance">
                  {space ? `Ask in ${spaceLabel(space)}` : "Ask your company docs"}
                </h2>
                <p className="mt-2 max-w-md text-sm text-muted-foreground">
                  {space?.kind === "personal"
                    ? "Answers come from the files you keep in your space and the sources you added to it, "
                    : space
                      ? "Answers come only from this space's sources that you can see, "
                      : `Answers come from your company's documents${outline ? " and the Outline wiki" : ""}, `}
                  with the sources cited under every answer. Attach a file to ask about it too.
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

            {forkedFrom && (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <CornerUpLeft className="size-3.5 shrink-0" />
                Branched from{" "}
                <Link
                  to={`/chat/${forkedFrom.conversation_id}`}
                  onClick={(e) => {
                    e.preventDefault()
                    open(`/chat/${forkedFrom.conversation_id}`)
                  }}
                  className="truncate font-medium text-foreground underline-offset-4 hover:underline"
                >
                  {forkedFrom.title}
                </Link>
              </p>
            )}

            {shown?.notice && (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <Info className="size-3.5 shrink-0" />
                {shown.notice}
              </p>
            )}

            {messages.map((m) =>
              m.role === "user" ? (
                <UserMessage
                  key={m.key}
                  message={m}
                  editing={editing === m.key}
                  busy={loading}
                  onEdit={() => setEditing(m.key)}
                  onCancel={() => setEditing(null)}
                  onSave={(text) => ask(text, { edit: m })}
                  onBranch={showBranch}
                />
              ) : (
                <AssistantMessage
                  key={m.key}
                  message={m}
                  actions={actions}
                  last={m === lastAnswer}
                  ref={m === lastAnswer ? lastAnswerRef : undefined}
                />
              ),
            )}
          </div>
          {!atBottom && messages.length > 0 && (
            <div className="pointer-events-none sticky bottom-2 flex justify-center">
              <Button
                variant="outline"
                size="sm"
                className="pointer-events-auto rounded-full shadow-sm"
                onClick={() => {
                  setAtBottom(true)
                  scrollToEnd()
                }}
              >
                <ArrowDown data-icon="inline-start" />
                Jump to latest
              </Button>
            </div>
          )}
        </div>
        {/* Said once an answer ends, not word by word as it streams. */}
        <p className="sr-only" role="status" aria-live="polite">
          {announce}
        </p>

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
            onEditLast={lastUser?.id && !loading ? () => setEditing(lastUser.key) : undefined}
            busy={loading}
            inputRef={inputRef}
            top={
              <>
                <SpaceChip value={spaceId} onChange={chooseSpace} disabled={loading} />
                <AttachmentChips files={files} uploading={uploading} onRemove={detach} />
              </>
            }
            leading={
              <AttachButton
                limits={limits}
                disabled={loading || uploading.length > 0}
                onFiles={attachFiles}
              />
            }
            above={picker.menu}
            intercept={picker.intercept}
            onFiles={attachFiles}
          />
          {picker.dialog}
        </div>
      </div>
      {sharing && urlId && (
        <ShareDialog
          open
          onOpenChange={(o) => !o && setSharing(null)}
          conversationId={urlId}
          messageId={sharing.messageId}
        />
      )}
    </SidebarProvider>
  )
}

// A question being edited: Enter (or Save) asks it in a new branch, Esc cancels.
function EditQuestion({
  original,
  busy,
  onSave,
  onCancel,
}: {
  original: string
  busy: boolean
  onSave: (text: string) => void
  onCancel: () => void
}) {
  const [text, setText] = useState(original)
  const editRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = editRef.current
    el?.focus()
    el?.setSelectionRange(el.value.length, el.value.length)
  }, [])
  const changed = text.trim().length > 0 && text.trim() !== original
  const save = () => changed && !busy && onSave(text)
  return (
    <div className="ml-auto w-full max-w-[85%] space-y-2">
      <Textarea
        ref={editRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            save()
          } else if (e.key === "Escape") {
            e.preventDefault()
            onCancel()
          }
        }}
        aria-label="Edit your question"
        maxLength={500}
        className="min-h-20"
      />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={save} disabled={busy || !changed}>
          Save and ask
        </Button>
      </div>
    </div>
  )
}

// A question: Edit turns it into a Textarea; saving asks it in a new branch (C10).
function UserMessage({
  message,
  editing,
  busy,
  onEdit,
  onCancel,
  onSave,
  onBranch,
}: {
  message: Message
  editing: boolean
  busy: boolean
  onEdit: () => void
  onCancel: () => void
  onSave: (text: string) => void
  onBranch: (message: Message, to: string) => void
}) {
  const editButton = useRef<HTMLButtonElement>(null)
  if (editing) {
    return (
      <EditQuestion
        original={message.text}
        busy={busy}
        onSave={onSave}
        onCancel={() => {
          onCancel()
          requestAnimationFrame(() => editButton.current?.focus())
        }}
      />
    )
  }
  return (
    <div className="group flex flex-col items-end gap-1">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-primary px-4 py-2.5 text-sm text-primary-foreground">
        {message.text}
      </div>
      <div className="flex items-center gap-0.5 text-muted-foreground">
        <BranchNav message={message} onBranch={onBranch} disabled={busy} />
        {message.id && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                ref={editButton}
                variant="ghost"
                size="icon-sm"
                aria-label="Edit question"
                disabled={busy}
                onClick={onEdit}
                className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 pointer-coarse:size-10 pointer-coarse:opacity-100"
              >
                <Pencil />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Edit (↑ in an empty box edits the last one)</TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  )
}

// Above the chat: the history button, its title, New chat, and Share / Rename / Delete.
function ChatHeader({
  conversation,
  title,
  busy,
  onRenamed,
  onShare,
  onNewChat,
  onDeleted,
}: {
  conversation: Conversation | ConversationSummary | null
  title: string
  busy: boolean
  onRenamed: (title: string) => void
  onShare: () => void
  onNewChat: () => void
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState("")
  const [deleting, setDeleting] = useState(false)

  async function rename() {
    if (!conversation || !name.trim()) return
    try {
      const renamed = await api.renameConversation(conversation.id, name)
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
      onDeleted()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't delete it.")
    }
  }

  return (
    <div className="flex h-11 w-full shrink-0 items-center gap-1 px-2 sm:px-3">
      <Tooltip>
        <TooltipTrigger asChild>
          <SidebarTrigger aria-label="Chat history" className="text-muted-foreground pointer-coarse:size-10" />
        </TooltipTrigger>
        <TooltipContent>Chat history</TooltipContent>
      </Tooltip>
      <p className="mx-auto min-w-0 max-w-3xl flex-1 truncate px-2 text-sm font-medium">{title}</p>
      <Button
        variant="ghost"
        size="sm"
        className="text-muted-foreground pointer-coarse:h-10"
        aria-label="New chat"
        onClick={onNewChat}
      >
        <SquarePen />
        <span className="hidden sm:inline">New chat</span>
      </Button>
      {conversation && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Chat options"
              className="text-muted-foreground pointer-coarse:size-10"
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onShare}>
              <Share2 /> Share chat
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                setName(title)
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
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-label="Chat name"
              maxLength={80}
              autoFocus
            />
            <DialogFooter className="mt-4">
              <Button type="submit" disabled={!name.trim()}>
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
              Its questions, answers, any feedback you gave on them and its share links are
              deleted for good.
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
