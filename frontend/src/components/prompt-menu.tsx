import { useCallback, useEffect, useState, type KeyboardEvent, type RefObject } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router"
import { Building2, MessageSquareText } from "lucide-react"

import { PromptVariablesDialog } from "@/components/prompt-variables-dialog"
import { Command, CommandGroup, CommandItem, CommandList } from "@/components/ui/command"
import type { SavedPrompt } from "@/lib/api"
import { INSERT_PROMPT } from "@/lib/chat-events"
import { slashQuery } from "@/lib/prompts"
import { queries } from "@/lib/queries"

const SHOWN = 8

// Saved prompts in the composer (chat.md C22): "/" at the start of the box lists
// them (↑ ↓ to move, Enter or Tab to choose, Esc to close); the command palette
// puts one in too (INSERT_PROMPT). A prompt with {{variables}} asks for each
// first. Returns the menu, the dialog, and the composer's key handler.
export function usePromptPicker({
  input,
  setInput,
  inputRef,
}: {
  input: string
  setInput: (text: string) => void
  inputRef: RefObject<HTMLTextAreaElement | null>
}) {
  const queryClient = useQueryClient()
  const query = slashQuery(input)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const open = query !== null && dismissed !== input
  const list = useQuery({ ...queries.prompts(), enabled: open, meta: { silent: true } })
  const words = (query ?? "").trim().toLowerCase()
  const matches = (list.data?.prompts ?? [])
    .filter((p) => !words || p.title.toLowerCase().includes(words))
    .slice(0, SHOWN)
  const [active, setActive] = useState(0)
  const [filling, setFilling] = useState<SavedPrompt | null>(null)
  const current = matches[Math.min(active, matches.length - 1)]

  // Put text in the box, the cursor at its end.
  const put = useCallback(
    (text: string) => {
      setInput(text)
      requestAnimationFrame(() => {
        const el = inputRef.current
        el?.focus()
        el?.setSelectionRange(text.length, text.length)
      })
    },
    [setInput, inputRef],
  )

  const choose = useCallback(
    (p: SavedPrompt) => {
      if (p.variables.length) setFilling(p)
      else put(p.body)
    },
    [put, setFilling],
  )

  // The palette's "Insert prompt" (it may arrive before the list is loaded).
  useEffect(() => {
    const onInsert = (e: Event) => {
      const id = (e as CustomEvent<string>).detail
      queryClient
        .fetchQuery(queries.prompts())
        .then((r) => {
          const p = r.prompts.find((x) => x.id === id)
          if (p) choose(p)
        })
        .catch(() => {})
    }
    window.addEventListener(INSERT_PROMPT, onInsert)
    return () => window.removeEventListener(INSERT_PROMPT, onInsert)
  }, [queryClient, choose])

  function intercept(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (!open) return false
    if (e.key === "Escape") {
      setDismissed(input)
    } else if (!matches.length) {
      return false
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const step = e.key === "ArrowDown" ? 1 : -1
      setActive((a) => (Math.min(a, matches.length - 1) + step + matches.length) % matches.length)
    } else if ((e.key === "Enter" && !e.shiftKey) || e.key === "Tab") {
      if (current) choose(current)
    } else {
      return false
    }
    e.preventDefault()
    return true
  }

  const menu = open ? (
    <div className="absolute inset-x-4 bottom-full z-20 mb-2 overflow-hidden rounded-2xl border border-border/70 bg-popover shadow-lg">
      {list.isPending ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">Loading saved prompts…</p>
      ) : matches.length ? (
        <Command shouldFilter={false} value={current?.id ?? ""} className="bg-transparent">
          <CommandList aria-label="Saved prompts" className="max-h-64">
            <CommandGroup heading="Saved prompts">
              {matches.map((p, i) => (
                <CommandItem
                  key={p.id}
                  value={p.id}
                  onMouseEnter={() => setActive(i)}
                  onSelect={() => choose(p)}
                  className="items-start"
                >
                  {p.workspace ? <Building2 className="mt-0.5" /> : <MessageSquareText className="mt-0.5" />}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{p.title}</span>
                    <span className="block truncate text-xs text-muted-foreground">{p.body}</span>
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      ) : (
        <p className="px-4 py-3 text-sm text-muted-foreground">
          {words ? "No saved prompt by that name. " : "No saved prompts yet. "}
          <Link to="/prompts" className="font-medium text-foreground underline-offset-4 hover:underline">
            Make one
          </Link>
        </p>
      )}
    </div>
  ) : null

  const dialog = (
    <PromptVariablesDialog
      prompt={filling}
      onOpenChange={(o) => !o && setFilling(null)}
      onUse={(text) => {
        setFilling(null)
        put(text)
      }}
    />
  )

  return { menu, dialog, intercept }
}
