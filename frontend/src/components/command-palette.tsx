import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import {
  BarChart3,
  BookOpen,
  FileText,
  Keyboard,
  Layers,
  MessageSquare,
  MessageSquareText,
  PanelLeft,
  Plug,
  ShieldCheck,
  SquarePen,
} from "lucide-react"

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import type { Role, User } from "@/lib/api"
import {
  FOCUS_COMPOSER,
  INSERT_PROMPT,
  MOD_KEY as MOD,
  tellChat as tell,
  TOGGLE_SIDEBAR,
} from "@/lib/chat-events"
import { splitSnippet } from "@/lib/chat-history"
import { queries } from "@/lib/queries"

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: [MOD, "K"], label: "Search chats and go anywhere" },
  { keys: [MOD, "⇧", "O"], label: "New chat" },
  { keys: ["/"], label: "Type a question (when not typing elsewhere)" },
  { keys: ["Esc"], label: "Stop the answer being written" },
  { keys: ["↑"], label: "Edit your last question (in an empty box)" },
  { keys: [MOD, "⇧", "C"], label: "Copy the last answer" },
  { keys: [MOD, "B"], label: "Show or hide the chat history" },
  { keys: [MOD, "/"], label: "These shortcuts" },
]

const isMod = (e: KeyboardEvent) => e.metaKey || e.ctrlKey

// ⌘K / Ctrl+K: search your chats (the same search as the sidebar, so an answer your
// role can't read now is never found), start a new one, or jump to a screen (C27).
// Also the app-wide shortcuts: ⌘⇧O new chat, ⌘/ the shortcuts list.
export function CommandPalette({ user, role }: { user: User; role: Role }) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [help, setHelp] = useState(false)
  const [query, setQuery] = useState("")
  const [q, setQ] = useState("")
  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 200)
    return () => clearTimeout(t)
  }, [query])
  const recent = useQuery({ ...queries.conversations(), enabled: open })
  const found = useQuery({ ...queries.conversationSearch(q), enabled: open && q.length > 0 })
  const prompts = useQuery({ ...queries.prompts(), enabled: open, meta: { silent: true } })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase()
      if (isMod(e) && !e.shiftKey && key === "k") {
        e.preventDefault()
        setOpen((o) => !o)
      } else if (isMod(e) && e.shiftKey && key === "o") {
        e.preventDefault()
        navigate("/chat")
        tell(FOCUS_COMPOSER)
      } else if (isMod(e) && key === "/") {
        e.preventDefault()
        setHelp((h) => !h)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [navigate])

  function run(fn: () => void) {
    setOpen(false)
    setQuery("")
    fn()
  }

  const screens = [
    { to: "/chat", label: "Chat", icon: MessageSquare },
    { to: "/knowledge", label: "Knowledge", icon: BookOpen },
    { to: "/spaces", label: "Spaces", icon: Layers },
    { to: "/prompts", label: "My prompts", icon: MessageSquareText },
    ...(role === "hr"
      ? [
          { to: "/documents", label: "Documents", icon: FileText },
          { to: "/integrations", label: "Integrations", icon: Plug },
          { to: "/admin/insights", label: "Insights", icon: BarChart3 },
        ]
      : []),
    ...(role === "hr" && user.role === "admin"
      ? [{ to: "/admin/sign-in", label: "Access", icon: ShieldCheck }]
      : []),
  ]
  const words = q.toLowerCase()
  const matches = (label: string) => !words || label.toLowerCase().includes(words)
  // Saved prompts (C22): put one in the chat box, asking for its {{variables}}.
  const savedPrompts = (prompts.data?.prompts ?? []).filter((p) => matches(p.title)).slice(0, 5)
  const chats = q
    ? (found.data ?? []).map((r) => ({ id: r.conversation_id, title: r.title, snippet: r.snippet }))
    : (recent.data?.conversations ?? []).slice(0, 6).map((c) => ({ id: c.id, title: c.title, snippet: "" }))

  return (
    <>
      <CommandDialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o)
          if (!o) setQuery("")
        }}
        title="Search and go"
        description="Search your chats, start a new one, or go to a screen."
      >
        {/* Filtered here: chats by the server's search, the rest by their names. */}
        <Command shouldFilter={false}>
        <CommandInput
          value={query}
          onValueChange={setQuery}
          placeholder="Search chats, or type a screen…"
          aria-label="Search chats and screens"
        />
        <CommandList>
          <CommandEmpty>{found.isFetching ? "Searching…" : "Nothing found."}</CommandEmpty>
          {matches("New chat") && (
            <CommandGroup heading="Actions">
              <CommandItem value="action-new-chat" onSelect={() => run(() => {
                navigate("/chat")
                tell(FOCUS_COMPOSER)
              })}>
                <SquarePen />
                New chat
                <CommandShortcut>{MOD}⇧O</CommandShortcut>
              </CommandItem>
              <CommandItem value="action-toggle-history" onSelect={() => run(() => {
                navigate("/chat")
                tell(TOGGLE_SIDEBAR)
              })}>
                <PanelLeft />
                Show or hide chat history
                <CommandShortcut>{MOD}B</CommandShortcut>
              </CommandItem>
              <CommandItem value="action-shortcuts" onSelect={() => run(() => setHelp(true))}>
                <Keyboard />
                Keyboard shortcuts
                <CommandShortcut>{MOD}/</CommandShortcut>
              </CommandItem>
            </CommandGroup>
          )}
          {chats.length > 0 && (
            <CommandGroup heading={q ? "Chats" : "Recent chats"}>
              {chats.map((c) => (
                <CommandItem
                  key={c.id}
                  value={`chat-${c.id}`}
                  onSelect={() => run(() => navigate(`/chat/${c.id}`))}
                  className="flex-col items-start gap-0.5"
                >
                  <span className="w-full truncate">{c.title}</span>
                  {c.snippet && (
                    <span className="line-clamp-1 w-full text-xs text-muted-foreground">
                      {splitSnippet(c.snippet).map((part, i) =>
                        part.match ? (
                          <mark key={i} className="bg-transparent font-medium text-foreground">
                            {part.text}
                          </mark>
                        ) : (
                          <span key={i}>{part.text}</span>
                        ),
                      )}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {savedPrompts.length > 0 && (
            <CommandGroup heading="Saved prompts">
              {savedPrompts.map((p) => (
                <CommandItem
                  key={p.id}
                  value={`prompt-${p.id}`}
                  onSelect={() => run(() => {
                    navigate("/chat")
                    tell(INSERT_PROMPT, p.id)
                  })}
                >
                  <MessageSquareText />
                  <span className="truncate">{p.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {screens.some((s) => matches(s.label)) && (
            <CommandGroup heading="Go to">
              {screens
                .filter((s) => matches(s.label))
                .map((s) => (
                  <CommandItem key={s.to} value={`screen-${s.to}`} onSelect={() => run(() => navigate(s.to))}>
                    <s.icon />
                    {s.label}
                  </CommandItem>
                ))}
            </CommandGroup>
          )}
        </CommandList>
        </Command>
      </CommandDialog>
      <ShortcutsDialog open={help} onOpenChange={setHelp} />
    </>
  )
}

export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>On Windows and Linux, Ctrl instead of ⌘.</DialogDescription>
        </DialogHeader>
        <dl className="divide-y text-sm">
          {SHORTCUTS.map((s) => (
            <div key={s.label} className="flex items-center justify-between gap-4 py-2">
              <dt className="text-muted-foreground">{s.label}</dt>
              <dd>
                <KbdGroup>
                  {s.keys.map((k) => (
                    <Kbd key={k}>{k}</Kbd>
                  ))}
                </KbdGroup>
              </dd>
            </div>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  )
}
