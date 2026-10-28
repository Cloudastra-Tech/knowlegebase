import { useId, type ReactNode } from "react"
import { MessageSquare } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { ConversationSummary } from "@/lib/api"
import { timeAgo } from "@/lib/utils"

// Recent chats on the new chat's home: five (four on a phone).
export const RECENT = 5

// What sits under the composer on the empty new chat (chat-view.tsx), quietly, as on
// Claude.ai: one column, the composer's width, one left edge. Someone with chats sees
// their last few in one glass card (the composer's tier, so the two read as one
// surface over the art, which runs at full ink under both); someone without sees the starter questions as chips instead.
// Spaces and folders aren't here: the composer's space chip and the sidebar have them.
// `data-drift`: the art's lines run on under all of it, faint under the words
// ("quiet") and frosted under the card ("glass").
export function ChatHome({
  starters,
  firstRun,
  onAsk,
  extra,
  chats,
  onOpen,
  onViewAll,
}: {
  starters: string[]
  firstRun: boolean // nothing asked yet: a line says where the starters come from
  onAsk: (question: string) => void
  extra?: ReactNode // under the starters: the sample documents, on a first run
  chats: ConversationSummary[] | undefined // newest first, as the API lists them; undefined while loading
  onOpen: (id: string) => void
  onViewAll: () => void
}) {
  const recentId = useId()
  const recent = (chats ?? []).slice(0, RECENT)
  // Not until the chats are in, so the chips never show and then go.
  const offer = chats !== undefined && recent.length === 0 && starters.length > 0

  return (
    <div data-drift="below" data-drift-blank className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 pb-4">
      {(offer || extra) && (
        <div data-drift="quiet" className="flex flex-col items-start gap-2">
          {offer && firstRun && (
            <p className="px-1 text-xs text-muted-foreground">
              Try one of these, from your workspace's documents, or type your own.
            </p>
          )}
          {offer && (
            <div role="group" aria-label="Suggested questions" className="flex flex-wrap gap-2">
              {starters.map((s) => (
                <Button
                  key={s}
                  variant="outline"
                  size="sm"
                  onClick={() => onAsk(s)}
                  className="h-auto min-h-7 max-w-full rounded-full bg-transparent px-3 py-1 text-left font-normal whitespace-normal text-muted-foreground dark:bg-transparent"
                >
                  {s}
                </Button>
              ))}
            </div>
          )}
          {extra}
        </div>
      )}

      {recent.length > 0 && (
        <section
          aria-labelledby={recentId}
          data-drift="glass"
          className="glass flex flex-col gap-0.5 rounded-2xl p-2"
        >
          <div className="flex h-8 items-center justify-between pl-3">
            <h3 id={recentId} className="text-xs font-medium text-muted-foreground">
              Recent chats
            </h3>
            <Button variant="ghost" size="xs" onClick={onViewAll} className="font-normal text-muted-foreground">
              View all
            </Button>
          </div>
          <ul className="flex flex-col max-sm:[&>li:nth-child(n+5)]:hidden">
            {recent.map((c) => (
              <li key={c.id}>
                <Button
                  variant="ghost"
                  onClick={() => onOpen(c.id)}
                  className="h-9 w-full justify-start gap-3 px-3 font-normal pointer-coarse:h-10"
                >
                  <MessageSquare className="text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-left">{c.title}</span>
                  <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{timeAgo(c.updated_at)}</span>
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
