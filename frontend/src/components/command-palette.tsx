import { useCallback, useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import { Keyboard, Layers, MessageSquareText, PanelLeft, Settings, SquarePen } from "lucide-react"

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
import { screensFor } from "@/lib/app-screens"
import {
  FOCUS_COMPOSER,
  INSERT_PROMPT,
  MOD_KEY as MOD,
  OPEN_SEARCH,
  OPEN_SHORTCUTS,
  tellChat as tell,
  TOGGLE_SIDEBAR,
} from "@/lib/chat-events"
import { useChatPort } from "@/lib/chat-bridge"
import { splitSnippet } from "@/lib/chat-history"
import { markInstantNav } from "@/lib/instant-nav"
import { queries } from "@/lib/queries"

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: [MOD, "K"], label: "Search chats and go anywhere" },
  { keys: [MOD, "⇧", "O"], label: "New chat" },
  { keys: ["/"], label: "Type a question (when not typing elsewhere)" },
  { keys: ["Esc"], label: "Stop the answer being written" },
  { keys: ["↑"], label: "Edit your last question (in an empty box)" },
  { keys: [MOD, "⇧", "C"], label: "Copy the last answer" },
  { keys: [MOD, "B"], label: "Collapse or expand the sidebar" },
  { keys: [MOD, "/"], label: "These shortcuts" },
]

const isMod = (e: KeyboardEvent) => e.metaKey || e.ctrlKey

// ⌘K / Ctrl+K: search your chats (the same search as the sidebar, so an answer your
// role can't read now is never found), start a new one, or jump to a screen (C27).
// Also the app-wide shortcuts: ⌘⇧O new chat, ⌘/ the shortcuts list.
// chatAt: the conversation the chat was last at ("Go to Chat" goes back to it).
// The sidebar's Search chats opens it with chats alone (OPEN_SEARCH).
export function CommandPalette({ user, role, chatAt = "/chat" }: { user: User; role: Role; chatAt?: string }) {
  const navigate = useNavigate()
  const chatPort = useChatPort()
  const [open, setOpen] = useState(false)
  const [chatsOnly, setChatsOnly] = useState(false)
  const [help, setHelp] = useState(false)
  const [query, setQuery] = useState("")
  const [q, setQ] = useState("")
  const [picked, setPicked] = useState("") // the highlighted item, by its value
  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 200)
    return () => clearTimeout(t)
  }, [query])
  const recent = useQuery({ ...queries.conversations(), enabled: open })
  const found = useQuery({ ...queries.conversationSearch(q), enabled: open && q.length > 0 })
  const prompts = useQuery({ ...queries.prompts(), enabled: open, meta: { silent: true } })

  // A new chat through the chat (lib/chat-bridge.tsx), so an answer being written
  // stops; by its address before the chat's code has loaded.
  const newChat = useCallback(() => {
    const chat = chatPort?.actions()
    if (chat) chat.newChat()
    else {
      navigate("/chat")
      tell(FOCUS_COMPOSER)
    }
  }, [chatPort, navigate])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase()
      if (isMod(e) && !e.shiftKey && key === "k") {
        e.preventDefault()
        setChatsOnly(false)
        setOpen((o) => !o)
      } else if (isMod(e) && e.shiftKey && key === "o") {
        e.preventDefault()
        markInstantNav()
        newChat()
      } else if (isMod(e) && key === "/") {
        e.preventDefault()
        setHelp((h) => !h)
      }
    }
    const onSearch = () => {
      setChatsOnly(true)
      setOpen(true)
    }
    const onShortcuts = () => setHelp(true)
    window.addEventListener("keydown", onKey)
    window.addEventListener(OPEN_SEARCH, onSearch)
    window.addEventListener(OPEN_SHORTCUTS, onShortcuts)
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener(OPEN_SEARCH, onSearch)
      window.removeEventListener(OPEN_SHORTCUTS, onShortcuts)
    }
  }, [newChat])

  // Whatever the palette does lands at once: no screen entrance, no sliding nav line.
  // It's used a hundred times a day, from the keyboard (lib/instant-nav.ts).
  function run(fn: () => void) {
    setOpen(false)
    setQuery("")
    markInstantNav()
    fn()
  }

  // The sidebar's screens, as this account sees them (lib/app-screens.ts, so the two
  // never disagree on who sees what), with the personal ones before the admin group.
  const { main, admin } = screensFor(user, role, chatAt)
  const screens = [
    ...main,
    { to: "/spaces", label: "Spaces", icon: Layers },
    { to: "/settings/prompts", label: "My prompts", icon: MessageSquareText },
    { to: "/settings", label: "Settings", icon: Settings },
    ...admin,
  ]
  const words = q.toLowerCase()
  const matches = (label: string) => !words || label.toLowerCase().includes(words)
  // Saved prompts (C22): put one in the chat box, asking for its {{variables}}.
  // Search chats (the sidebar's) shows chats alone, and more of the recent ones.
  const savedPrompts = chatsOnly
    ? []
    : (prompts.data?.prompts ?? []).filter((p) => matches(p.title)).slice(0, 5)
  const chats = q
    ? (found.data ?? []).map((r) => ({ id: r.conversation_id, title: r.title, snippet: r.snippet }))
    : (recent.data?.conversations ?? [])
        .slice(0, chatsOnly ? 20 : 6)
        .map((c) => ({ id: c.id, title: c.title, snippet: "" }))
  const shownScreens = chatsOnly ? [] : screens.filter((s) => matches(s.label))
  const showActions = !chatsOnly && matches("New chat")
  // The highlighted item. cmdk keeps it by value and doesn't filter here, so after a
  // new search it would point at an item that's gone, and nothing would be lit: the
  // first item is, until the arrows or the pointer pick another.
  const items = [
    ...(showActions ? ["action-new-chat", "action-toggle-history", "action-shortcuts"] : []),
    ...chats.map((c) => `chat-${c.id}`),
    ...savedPrompts.map((p) => `prompt-${p.id}`),
    ...shownScreens.map((s) => `screen-${s.to}`),
  ]
  const active = items.includes(picked) ? picked : (items[0] ?? "")

  return (
    <>
      <CommandDialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o)
          if (!o) {
            setQuery("")
            setChatsOnly(false)
          }
        }}
        title={chatsOnly ? "Search chats" : "Search and go"}
        description={
          chatsOnly ? "Search your chats by their words." : "Search your chats, start a new one, or go to a screen."
        }
      >
        {/* Filtered here: chats by the server's search, the rest by their names. */}
        <Command shouldFilter={false} value={active} onValueChange={setPicked}>
        <CommandInput
          value={query}
          onValueChange={(v) => {
            setQuery(v)
            setPicked("")
          }}
          placeholder={chatsOnly ? "Search chats…" : "Search chats, or type a screen…"}
          aria-label={chatsOnly ? "Search chats" : "Search chats and screens"}
        />
        <CommandList>
          <CommandEmpty>
            {found.isFetching
              ? "Searching…"
              : chatsOnly && !q && recent.isSuccess
                ? "Your chats show up here."
                : "Nothing found."}
          </CommandEmpty>
          {showActions && (
            <CommandGroup heading="Actions">
              <CommandItem value="action-new-chat" onSelect={() => run(newChat)}>
                <SquarePen />
                New chat
                <CommandShortcut className="font-mono tracking-normal">{MOD}⇧O</CommandShortcut>
              </CommandItem>
              {/* At once, from the keyboard: markInstant (run) holds for two frames. */}
              <CommandItem
                value="action-toggle-history"
                onSelect={() => run(() => window.dispatchEvent(new CustomEvent(TOGGLE_SIDEBAR)))}
              >
                <PanelLeft />
                Collapse or expand the sidebar
                <CommandShortcut className="font-mono tracking-normal">{MOD}B</CommandShortcut>
              </CommandItem>
              <CommandItem value="action-shortcuts" onSelect={() => run(() => setHelp(true))}>
                <Keyboard />
                Keyboard shortcuts
                <CommandShortcut className="font-mono tracking-normal">{MOD}/</CommandShortcut>
              </CommandItem>
            </CommandGroup>
          )}
          {chats.length > 0 && (
            <CommandGroup heading={q ? "Chats" : "Recent chats"}>
              {chats.map((c) => (
                <CommandItem
                  key={c.id}
                  value={`chat-${c.id}`}
                  onSelect={() =>
                    run(() => {
                      const chat = chatPort?.actions()
                      if (chat) chat.open(c.id)
                      else navigate(`/chat/${c.id}`)
                    })
                  }
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
          {shownScreens.length > 0 && (
            <CommandGroup heading="Go to">
              {shownScreens.map((s) => (
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
