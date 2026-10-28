import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { flushSync } from "react-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useLocation, useMatch, useNavigate, useSearchParams } from "react-router"
import {
  ArrowDown,
  CornerUpLeft,
  Download,
  Info,
  MoreHorizontal,
  Pencil,
  Printer,
  Share2,
  SquarePen,
  Trash2,
} from "lucide-react"
import { toast } from "sonner"

import { PhoneMenuButton } from "@/components/app-sidebar"
import { AttachButton, AttachmentChips } from "@/components/attachment-chips"
import {
  AssistantMessage,
  BranchNav,
  type AnswerActions,
  type LiveStep,
  type Message,
} from "@/components/chat-answer"
import { composerSignal } from "@/components/ascii/composer-signal"
import { ChatBreadcrumbs } from "@/components/breadcrumbs"
import { ChatBackdrop } from "@/components/chat-backdrop"
import { Composer } from "@/components/chat-composer"
import { ChatHome } from "@/components/chat-home"
import { LoggingNotice } from "@/components/logging-notice"
import { SampleDocsToggle } from "@/components/onboarding-checklist"
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
import { Bubble, BubbleContent } from "@/components/ui/bubble"
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
import { Message as MessageRow } from "@/components/ui/message"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  api,
  ApiError,
  saveFile,
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
import { chatCrumbs } from "@/lib/breadcrumbs"
import { useChatPort, type ChatActions } from "@/lib/chat-bridge"
import { getEffects } from "@/lib/ascii-loop"
import { CHOOSE_SPACE, FOCUS_COMPOSER, OPEN_SEARCH, setThinking, type SearchDetail } from "@/lib/chat-events"
import { startersFrom } from "@/lib/chat-starters"
import { dur, ease, usePrefersReducedMotion } from "@/lib/motion"
import { queries } from "@/lib/queries"
import { spaceLabel } from "@/lib/spaces"
import { cn } from "@/lib/utils"

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
      steps: endSteps(
        (m.steps ?? []).map((s) => ({ ...s, done: s.result != null })),
        m.state === "stopped" || m.state === "error" ? m.state : null,
      ),
    }
  })
}

// An answer's steps once it has ended: a step still running when it was stopped or
// failed shows that (not a done step); otherwise they're all done.
function endSteps(steps: LiveStep[], ended: "stopped" | "error" | null): LiveStep[] {
  return steps.map((st) => (st.done ? st : { ...st, done: true, ...(ended ? { ended } : {}) }))
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

// How much of the turn before stays in sight above a question pinned to the top (px).
const PEEK = 64

// The scroller holds a pinned question in place as its answer grows (putting it back
// when the page above it changes), and lets go when the reader scrolls with the wheel,
// touch or scroll keys. Scrolled another way, it would be undone on the next token, so
// these let go too: a press on the scrollbar, and the focus moving into the transcript
// (Tab to a link, which scrolls to it). Told as a wheel turn, the one way in it has.
const letGo = (viewport: HTMLElement) =>
  viewport.dispatchEvent(new WheelEvent("wheel", { bubbles: true }))

const typing = (el: Element | null) =>
  el instanceof HTMLElement &&
  (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))

// The chat. /chat is a new conversation; /chat/:conversationId a saved one, loaded
// from the server (so a reload shows it again). The first question of a new one
// saves it and moves the address to its id. Its history is in the app's sidebar
// (app-sidebar.tsx), which reaches it through lib/chat-bridge.tsx.
export function ChatView({ role }: { role: Role }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const atId = useMatch("/chat/:conversationId")?.params.conversationId ?? null
  const onChat = location.pathname === "/chat" || location.pathname.startsWith("/chat/")
  // On another screen (App keeps the chat mounted), it's still the conversation it was
  // showing: the transcript and where it was scrolled to are there on the way back.
  const [keptId, setKeptId] = useState(atId)
  if (onChat && keptId !== atId) setKeptId(atId)
  const urlId = onChat ? atId : keptId
  const [session, setSession] = useState<Session | null>(null)
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<string | null>(null) // a question's key
  const [sharing, setSharing] = useState<{ messageId: string | null } | null>(null)
  const [announce, setAnnounce] = useState("")
  // The last question is the scroller's anchor (see the transcript, below), except
  // after switching versions, which never moves the view.
  const [pinLast, setPinLast] = useState(true)
  const abort = useRef<AbortController | null>(null)
  const nextKey = useRef(1)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const lastAnswerRef = useRef<HTMLDivElement>(null)
  // The composer's row, and where it was (its top, px) as the first question went:
  // from the middle of the empty chat it moves down to its place (the FLIP below).
  const composerRow = useRef<HTMLDivElement>(null)
  const flipFrom = useRef<number | null>(null)
  const reduced = usePrefersReducedMotion()
  const knowledge = useQuery({ ...queries.knowledge(), meta: { silent: true } })
  // Someone who hasn't asked anything yet: "ask your first question" (the server's
  // suggestions, from titles they may read), and HR the sample documents.
  const welcome = useQuery({ ...queries.onboarding(), meta: { silent: true } }).data
  const list = useQuery(queries.conversations())
  // The workspace's admin may hide the steps panel from members (HR and admins keep it).
  const account = useUser()
  const assistantInfo = useQuery(queries.assistantInfo()).data
  const hideSteps = assistantInfo?.show_steps === false && account.role === "public"
  const spaceList = useQuery({ ...queries.spaces(), meta: { silent: true } })
  const limits = useQuery({ ...queries.attachmentLimits(), meta: { silent: true } }).data
  const [chosenSpace, setChosenSpace] = useState<string | null>(storedSpace)
  const [uploading, setUploading] = useState<string[]>([])
  // The empty chat's art fading out as the first question goes (ChatBackdrop).
  const [artLeaving, setArtLeaving] = useState(false)
  useEffect(() => {
    if (!artLeaving) return
    const t = setTimeout(() => setArtLeaving(false), dur.driftExit * 1000)
    return () => clearTimeout(t)
  }, [artLeaving])

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
    if (saved.isError && !live && onChat) navigate("/chat", { replace: true })
  }, [saved.isError, live, onChat, navigate])

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

  // Another conversation gets a scroller of its own, so it opens at its last question
  // (the scroller's defaultScrollPosition). A new chat that's just been saved, and so
  // has an address now, keeps the one it has.
  const [scroller, setScroller] = useState({ urlId, key: urlId ?? "new" })
  if (scroller.urlId !== urlId) {
    setScroller({ urlId, key: live && scroller.urlId === null ? scroller.key : (urlId ?? "new") })
    setPinLast(true)
  }
  // A chat opened from history is drawn as it is (history never animates): until the
  // person scrolls it or asks in it, Jump to latest appears without its entrance.
  const [touched, setTouched] = useState<string | null>(null)
  const quiet = !live && touched !== scroller.key
  const touch = () => quiet && setTouched(scroller.key)

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

  // The app's sidebar: what it shows of the chat, and what it asks of it
  // (lib/chat-bridge.ts).
  const port = useChatPort()
  const forSidebar: ChatActions = {
    open: (id) => open(`/chat/${id}`),
    newChat,
    chooseSpace,
    // The chat here was deleted from the sidebar: a new chat instead, without leaving
    // the screen that's open.
    deleted: (id) => {
      if (id !== urlId) return
      if (onChat) open("/chat")
      else {
        abort.current?.abort()
        setSession(null)
        setKeptId(null)
      }
    },
  }
  useLayoutEffect(() => {
    port?.setActions(forSidebar)
  })
  useEffect(() => {
    port?.setState({ conversationId: urlId, spaceId, busy: loading })
  }, [port, urlId, spaceId, loading])

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
    if (!messages.length) {
      setArtLeaving(true)
      flipFrom.current = composerRow.current?.getBoundingClientRect().top ?? null
    }
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
    // The question goes in after what stays, as a new row the scroller pins to the top
    // of the view, with the answer growing below it. Asked again, what makes way goes
    // first (its own render), so the scroller sees the question arrive, not a swap:
    // a retried or rewritten answer's question comes back as a new row too.
    const at = (m: Message) => messages.findIndex((x) => x.key === m.key)
    const before = target ? messages[at(target) - 1] : undefined
    const reasked = before?.role === "user" ? before : undefined // the question asked again
    const from = target
      ? at(target) - (reasked ? 1 : 0)
      : again.edit
        ? at(again.edit)
        : messages.length
    const kept = messages.slice(0, from)
    const asked: Message[] = target
      ? reasked
        ? [{ ...reasked, key: `draft-${nextKey.current++}` }]
        : []
      : [{ key: questionKey, role: "user", text: q }]
    if (from < messages.length) flushSync(() => edit(() => kept))
    edit(() => [...kept, ...asked, draft])
    setPinLast(true)
    setLoading(true)
    setThinking(true)
    setAnnounce("Writing an answer…")
    const change = (fn: (s: Session) => Session) => setSession((s) => (s ? fn(s) : s))
    const update = (fn: (m: Message) => Message) =>
      change((s) => ({ ...s, messages: s.messages.map((m) => (m.key === key ? fn(m) : m)) }))
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
      else if (e.type === "step_start") {
        // Timed on this screen's clock, for the trace ("0.8s"). Each step ripples the
        // background field again.
        const startedAt = performance.now()
        update((m) => ({
          ...m,
          steps: [...(m.steps ?? []), { id: e.id, tool: e.tool, label: e.label, done: false, startedAt }],
        }))
        setThinking(true)
      } else if (e.type === "step_end") {
        const now = performance.now()
        update((m) => ({
          ...m,
          steps: (m.steps ?? []).map((st) =>
            st.id === e.id
              ? { ...st, done: true, result: e.result, ms: st.startedAt === undefined ? undefined : now - st.startedAt }
              : st,
          ),
        }))
      }
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
        steps: endSteps(
          m.steps ?? [],
          reply.state === "stopped" || reply.state === "error" ? reply.state : null,
        ),
      }))
      // The versions beside it ("‹ 2 / 3 ›") come from the saved conversation.
      if (savedIn) reload(savedIn).catch(() => {})
      if (welcome?.first_run) void queryClient.invalidateQueries({ queryKey: ["onboarding"] })
    } catch (err) {
      if (isAbort(err) || controller.signal.aborted) {
        // Stopped: keep what was written (the server saves it the same way), and
        // take the focus to it.
        ended = "stopped"
        update((m) => ({
          ...m,
          state: "stopped",
          streaming: false,
          steps: endSteps(m.steps ?? [], "stopped"),
        }))
        requestAnimationFrame(() => lastAnswerRef.current?.focus({ preventScroll: true }))
      } else if (err instanceof ApiError && (err.status === 429 || err.status === 402)) {
        // Nothing was asked: give the question back to send again in a moment (a
        // rate limit), or once the plan allows it (402: the plan's limit, BILLING=on).
        update((m) => ({
          ...m,
          limited: err.message,
          limitedBy: err.status === 402 ? "plan" : "rate",
          streaming: false,
        }))
        if (!target && !again.edit) setInput((current) => current || q)
      } else {
        ended = "error"
        update((m) => ({
          ...m,
          text: err instanceof Error ? err.message : "Something went wrong.",
          state: "error",
          streaming: false,
          steps: endSteps(m.steps ?? [], "error"),
        }))
      }
    } finally {
      if (abort.current === controller) abort.current = null
      setLoading(false)
      setThinking(false)
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
      setPinLast(false)
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
    // Not found in a space: the same question, ready to ask across the workspace.
    onAskEverywhere: spaceId
      ? (question) => {
          chooseSpace(null)
          requestAnimationFrame(() => {
            setInput(question)
            inputRef.current?.focus()
          })
        }
      : undefined,
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
  const firstRun = !space && !!welcome?.first_run
  const starters = space
    ? []
    : firstRun && welcome.suggestions.length
      ? welcome.suggestions
      : startersFrom(knowledge.data?.items)
  const offerSample = firstRun && role === "hr" && account.role !== "public"
  const outline = knowledge.data?.outline ?? false
  const empty = messages.length === 0 && !opening
  const lastUser = [...messages].reverse().find((m) => m.role === "user")

  // The first question sent: the composer goes from the middle of the empty chat down
  // to its place in one short move (FLIP: drawn there at once, then eased in from where
  // it was), at once with reduced motion or "Reduce effects". Only then: a saved chat
  // opened from the empty one, or New chat, changes the layout without moving anything.
  useLayoutEffect(() => {
    const from = flipFrom.current
    flipFrom.current = null
    const row = composerRow.current
    if (from === null || empty || !row) return
    const dy = from - row.getBoundingClientRect().top
    if (Math.abs(dy) < 1 || reduced || getEffects() === "reduced") return
    row.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], {
      duration: dur.reveal * 1000,
      easing: `cubic-bezier(${ease.out.join(", ")})`,
    })
  }, [empty, reduced])

  // Search chats (the palette, chats alone), or a folder's chats in it.
  const openSearch = (detail?: SearchDetail) =>
    window.dispatchEvent(new CustomEvent<SearchDetail | undefined>(OPEN_SEARCH, { detail }))

  return (
    <div className="relative flex h-full min-h-0 w-full" data-print="flow">
      <div className="relative isolate flex min-w-0 flex-1 flex-col" data-print="flow">
        <ChatBackdrop empty={empty} leaving={artLeaving} />
        <ChatHeader
          conversation={conversation}
          title={title}
          space={space && { id: space.id, label: spaceLabel(space) }}
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
        {/* The transcript. A question just asked is pinned to the top of the view and
            its answer grows below it: nothing scrolls while it streams (no following).
            When there's more below, "Jump to latest" goes there. The last question is
            the anchor; a chat opens with it at the top (or at its end, if it fits). */}
        <MessageScrollerProvider
          key={scroller.key}
          defaultScrollPosition="last-anchor"
          scrollPreviousItemPeek={PEEK}
        >
          <MessageScroller className="flex-[1_1_0%]" data-print="flow">
            {/* No browser scroll anchoring: the scroller keeps the question in place
                itself, and the two together nudged the view on every token. */}
            <MessageScrollerViewport
              ref={viewportRef}
              aria-label="Chat messages"
              onPointerDown={(e) => {
                touch()
                if (e.target === e.currentTarget) letGo(e.currentTarget)
              }}
              onWheel={touch}
              onTouchMove={touch}
              onKeyDown={touch}
              // Not focus from a portal (the passage sheet), which React bubbles through here.
              onFocus={(e) => e.currentTarget.contains(e.target) && letGo(e.currentTarget)}
              // Empty, it holds nothing to read: no stop for Tab.
              tabIndex={empty ? -1 : undefined}
              data-print="flow"
              className="outline-none [overflow-anchor:none] focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset"
            >
              <MessageScrollerContent
                // Room under the last answer for "Jump to latest", so it never covers
                // the answer's last row (Stopped · Try again, the actions).
                className="mx-auto w-full max-w-3xl px-4 pt-6 pb-16"
                // The spacer under a pinned question's answer is sized to let the view
                // reach the question and no further, and only a frame after the answer
                // changes. An answer getting shorter as it streams (its steps folding
                // away once the text starts) would leave the view past the end for that
                // frame: the browser pulls it back, the question drops by as much, and
                // the scroller puts it back. 12rem more room under the spacer (only
                // while there is one) takes the shrinking, so nothing moves.
                spacerClassName="box-content pb-48"
                aria-label="Conversation"
                aria-busy={loading}
              >
                {/* The empty chat's heading, starters and recent chats sit round the
                    composer, below. Here a row with nothing in it stands in, so the
                    scroller sees the first question arrive as it always has: the
                    transcript's rows replacing one (the pin-to-top logic counts them). */}
                {empty && <MessageScrollerItem aria-hidden className="hidden" />}

                {forkedFrom && (
                  <MessageScrollerItem>
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
                  </MessageScrollerItem>
                )}

                {shown?.notice && (
                  <MessageScrollerItem>
                    <p className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Info className="size-3.5 shrink-0" />
                      {shown.notice}
                    </p>
                  </MessageScrollerItem>
                )}

                {messages.map((m) => (
                  <MessageScrollerItem
                    key={m.key}
                    messageId={m.key}
                    scrollAnchor={pinLast && m === lastUser}
                    // Drawn even off screen: a row skipped by content-visibility counts
                    // as 10rem tall, so a question just asked was pinned against a
                    // guess and the view jumped once it was drawn.
                    className="[content-visibility:visible]"
                  >
                    {m.role === "user" ? (
                      <UserMessage
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
                        message={m}
                        actions={actions}
                        last={m === lastAnswer}
                        ref={m === lastAnswer ? lastAnswerRef : undefined}
                      />
                    )}
                  </MessageScrollerItem>
                ))}
              </MessageScrollerContent>
            </MessageScrollerViewport>
            {/* The view moves; the focus goes to the transcript, as the button goes
                away once there's nothing further down. */}
            <MessageScrollerButton
              variant="outline"
              size="sm"
              behavior={reduced ? "auto" : "smooth"}
              onClick={() => viewportRef.current?.focus({ preventScroll: true })}
              data-print="hide"
              className={cn(
                "rounded-full border-(--glass-border) bg-glass shadow-sm backdrop-blur-md hover:bg-muted data-[active=true]:duration-(--dur-jump) data-[active=true]:ease-out-strong data-[active=false]:duration-(--dur-jump-exit) data-[active=false]:ease-out-strong motion-reduce:data-[active=false]:translate-y-0 motion-reduce:data-[active=false]:scale-100 pointer-coarse:h-10",
                quiet && "transition-none",
              )}
            >
              <ArrowDown data-icon="inline-start" />
              Jump to latest
            </MessageScrollerButton>
          </MessageScroller>
        </MessageScrollerProvider>
        {/* The empty new chat, centred as on Claude.ai: the heading just over the
            composer, the two in the middle of the column, and the home's recent chats
            (or starters) under it, then who you're searching as at the foot. The
            space round them is the art's: a click there decodes a line
            (redacted-drift.tsx). One short heading; how answers work is behind the
            (i). data-drift: the art's lines run faint under these words. */}
        {empty && (
          <div
            data-drift="content"
            data-drift-blank
            className="mx-auto flex w-full max-w-3xl shrink-0 items-center justify-center gap-1 px-4 text-center"
          >
            <h2 className="text-2xl font-semibold tracking-tight text-balance">
              {space ? `Ask in ${spaceLabel(space)}` : firstRun ? "Ask your first question" : "Ask your company docs"}
            </h2>
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label="How answers work" className="text-muted-foreground">
                  <Info />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-72 text-sm text-muted-foreground">
                {space?.kind === "personal"
                  ? "Answers come from the files you keep in your space and the sources you added to it, "
                  : space
                    ? "Answers come only from this space's sources that you can see, "
                    : `Answers come from your company's documents${outline ? " and the Outline wiki" : ""}, `}
                with the sources cited under every answer. Attach a file to ask about it too.
              </PopoverContent>
            </Popover>
          </div>
        )}
        {/* Said once an answer ends, not word by word as it streams. */}
        <p className="sr-only" role="status" aria-live="polite">
          {announce}
        </p>

        {/* The composer's row. Its focus and typing reach the empty chat's art through
            composerSignal (no state, so no render per key). */}
        <div
          ref={composerRow}
          className="pointer-events-none relative pt-6"
          data-print="hide"
          onFocus={(e) => (e.target as Element) === inputRef.current && composerSignal.focus(true)}
          onBlur={(e) => (e.target as Element) === inputRef.current && composerSignal.focus(false)}
          onInput={() => composerSignal.typed()}
        >
          {/* The fade over the field behind the composer: none on the empty chat, where
              the art runs round it (and its own frost is behind the composer). Its own
              layer, so no opacity sits on an ancestor of the composer's glass. */}
          <div
            aria-hidden
            className={cn(
              "absolute inset-0 -z-10 bg-gradient-to-t from-background via-background to-transparent",
              empty && "opacity-0",
              artLeaving && "transition-opacity duration-(--dur-drift-exit) ease-out-strong",
            )}
          />
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
        {/* Under the composer: the home's recent chats (or starters), scrolling on their
            own if they don't fit. The scrollbar's room is kept on both sides, so they
            stay centred under the composer and never move sideways when they start to
            scroll. */}
        {empty && (
          <div
            data-drift-blank
            data-print="hide"
            className="flex min-h-0 flex-[1.45_1_0%] flex-col overflow-y-auto overscroll-contain [scrollbar-gutter:stable_both-edges] max-sm:[scrollbar-gutter:auto]"
          >
            <ChatHome
              starters={starters}
              firstRun={firstRun}
              onAsk={(s) => ask(s)}
              extra={
                offerSample &&
                (welcome.sample.available || welcome.sample.on) && (
                  <div className="mt-2 flex max-w-xl flex-col items-start gap-2">
                    <p className="px-1 text-xs text-muted-foreground">
                      {welcome.sample.on
                        ? "The sample HR handbook is in. Remove it before colleagues ask."
                        : "No documents yet? Try it on a made-up HR handbook first."}
                    </p>
                    <SampleDocsToggle sample={welcome.sample} />
                  </div>
                )
              }
              chats={list.isError ? [] : list.data?.conversations}
              onOpen={(id) => open(`/chat/${id}`)}
              onViewAll={() => openSearch()}
            />
          </div>
        )}
        {/* The fine print, one line at the foot of the page as on Claude.ai: who
            you're searching as, and whether questions are logged. On the column's
            left edge, under the art (faint there), clear of a phone's home bar. */}
        {empty && (
          <p
            data-slot="chat-fine-print"
            data-drift="quiet"
            data-print="hide"
            className="mx-auto w-full max-w-3xl shrink-0 px-9 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))] text-xs text-pretty text-muted-foreground"
          >
            Searching as <span className="font-medium">{role === "hr" ? "HR / Admin" : "Public user"}</span>
            <LoggingNotice inline />
          </p>
        )}
      </div>
      {sharing && urlId && (
        <ShareDialog
          open
          onOpenChange={(o) => !o && setSharing(null)}
          conversationId={urlId}
          messageId={sharing.messageId}
        />
      )}
    </div>
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
    <MessageRow align="end" className="group flex-col items-end gap-1 data-[align=end]:flex-col">
      {/* Glass, tinted with the signal: the question, set apart from the answer's
          opaque reading column. */}
      <Bubble variant="outline" align="end" className="max-w-[85%]">
        <BubbleContent className="kb-user-bubble whitespace-pre-wrap">{message.text}</BubbleContent>
      </Bubble>
      <div className="flex items-center gap-0.5 text-muted-foreground" data-print="hide">
        <BranchNav message={message} onBranch={onBranch} disabled={busy} />
        {/* Its room is kept from the start (hidden until the question is saved and has
            an id), so the row doesn't arrive under a question just asked and push the
            answer down. */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              ref={editButton}
              variant="ghost"
              size="icon-sm"
              aria-label="Edit question"
              disabled={busy || !message.id}
              onClick={onEdit}
              className={cn(
                "opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 pointer-coarse:size-10 pointer-coarse:opacity-100",
                !message.id && "invisible",
              )}
            >
              <Pencil />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Edit (↑ in an empty box edits the last one)</TooltipContent>
        </Tooltip>
      </div>
    </MessageRow>
  )
}

// Above the chat: the space it's asked in and its title, as breadcrumbs (the space
// only from sm up), and its menu (Share / Download / Print / Rename / Delete);
// New chat is in the app's sidebar, rail or not. On a phone, the top bar: the menu
// (the sidebar's sheet) and New chat too. Printed, only the title is left (index.css,
// data-print).
function ChatHeader({
  conversation,
  title,
  space,
  busy,
  onRenamed,
  onShare,
  onNewChat,
  onDeleted,
}: {
  conversation: Conversation | ConversationSummary | null
  title: string
  space: { id: string; label: string } | null
  busy: boolean
  onRenamed: (title: string) => void
  onShare: () => void
  onNewChat: () => void
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState("")
  const spaceId = space?.id
  const spaceName = space?.label
  const crumbs = useMemo(
    () => chatCrumbs(spaceId && spaceName ? { id: spaceId, label: spaceName } : null, title),
    [spaceId, spaceName, title],
  )
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

  // The chat as shown, as markdown: the server writes it, leaving out answers you
  // can't see now (C25).
  async function download() {
    if (!conversation) return
    try {
      saveFile(await api.exportConversation(conversation.id))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't download it.")
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
    <div className="flex h-11 w-full shrink-0 items-center gap-1 px-2 sm:px-3 max-md:glass max-md:z-20 max-md:h-12 max-md:rounded-none max-md:border-x-0 max-md:border-t-0 max-md:pt-[env(safe-area-inset-top)]">
      <PhoneMenuButton data-print="hide" />
      <div className="mx-auto flex min-w-0 max-w-3xl flex-1 px-2">
        <ChatBreadcrumbs crumbs={crumbs} />
      </div>
      {/* On a phone only: the sidebar's New chat is a tap away anywhere else. */}
      <Button
        variant="ghost"
        size="icon"
        className="size-10 text-muted-foreground md:hidden"
        aria-label="New chat"
        data-print="hide"
        onClick={onNewChat}
      >
        <SquarePen />
      </Button>
      {conversation && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Chat options"
              data-print="hide"
              className="text-muted-foreground pointer-coarse:size-10"
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onShare}>
              <Share2 /> Share chat
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={download}>
              <Download /> Download as Markdown
            </DropdownMenuItem>
            {/* After the menu has closed, so it isn't in the printout. */}
            <DropdownMenuItem onSelect={() => window.setTimeout(() => window.print(), 100)}>
              <Printer /> Print
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
