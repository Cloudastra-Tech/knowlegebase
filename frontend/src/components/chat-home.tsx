import { useId, type ReactNode } from "react"
import { Folder } from "lucide-react"

import { SpaceIcon } from "@/components/space-switcher"
import { Button } from "@/components/ui/button"
import type { ChatFolder, ConversationSummary, SpaceSummary } from "@/lib/api"
import { spaceLabel } from "@/lib/spaces"
import { cn, timeAgo } from "@/lib/utils"

// Recent chats on the new chat's home: five (four on a phone).
export const RECENT = 5

// What sits under the composer on the empty new chat (chat-view.tsx), quietly, as on
// Claude.ai or T3 chat's new chat: the starter questions as chips, the last few
// chats, then the spaces to ask in and the chat folders. Each part is left out when
// it has nothing to show. Rows and chips are ghost buttons: muted text, a soft
// background on hover, nothing else. `data-drift="below"`: the empty chat's art runs
// under this, never across it.
export function ChatHome({
  starters,
  firstRun,
  onAsk,
  extra,
  chats,
  folders,
  spaces,
  spaceId,
  onOpen,
  onViewAll,
  onChooseSpace,
  onOpenFolder,
}: {
  starters: string[]
  firstRun: boolean // nothing asked yet: a line says where the starters come from
  onAsk: (question: string) => void
  extra?: ReactNode // under the starters: the sample documents, on a first run
  chats: ConversationSummary[] // newest first, as the API lists them
  folders: ChatFolder[]
  spaces: SpaceSummary[] // yours: My space and team spaces ("All company" is implied)
  spaceId: string | null // the space new chats ask in
  onOpen: (id: string) => void
  onViewAll: () => void
  onChooseSpace: (id: string | null) => void
  onOpenFolder: (folder: ChatFolder) => void
}) {
  const recentId = useId()
  const recent = chats.slice(0, RECENT)
  const filed = folders
    .map((folder) => ({ folder, count: chats.filter((c) => c.folder_id === folder.id).length }))
    .filter((f) => f.count > 0)
  const mine = spaces.find((s) => s.kind === "personal")
  const teams = spaces.filter((s) => s.kind === "team")
  const spaceChoices: (SpaceSummary | null)[] = spaces.length ? [null, ...(mine ? [mine] : []), ...teams] : []

  return (
    <div data-drift="below" className="mx-auto flex w-full max-w-2xl flex-col gap-5 px-4 pt-1 pb-6">
      {(starters.length > 0 || extra) && (
        <div className="flex flex-col items-center gap-2">
          {firstRun && starters.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Try one of these, from your workspace's documents, or type your own.
            </p>
          )}
          {starters.length > 0 && (
            <div role="group" aria-label="Suggested questions" className="flex flex-wrap justify-center gap-2">
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
        <section aria-labelledby={recentId} className="flex flex-col gap-1">
          <div className="flex h-7 items-center justify-between pl-3">
            <h3 id={recentId} className="text-xs font-medium text-muted-foreground">
              Recent chats
            </h3>
            <Button variant="ghost" size="xs" onClick={onViewAll} className="text-muted-foreground">
              View all
            </Button>
          </div>
          <ul className="flex flex-col max-sm:[&>li:nth-child(n+5)]:hidden">
            {recent.map((c) => (
              <li key={c.id}>
                <Button
                  variant="ghost"
                  onClick={() => onOpen(c.id)}
                  className="h-9 w-full justify-between gap-4 px-3 font-normal pointer-coarse:h-10"
                >
                  <span className="min-w-0 truncate">{c.title}</span>
                  <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{timeAgo(c.updated_at)}</span>
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {(spaceChoices.length > 0 || filed.length > 0) && (
        <div className="flex flex-col gap-2">
          {spaceChoices.length > 0 && (
            <ChipRow label="Spaces">
              {spaceChoices.map((space) => {
                const current = (space?.id ?? null) === spaceId
                return (
                  <Button
                    key={space?.id ?? "all"}
                    variant="ghost"
                    size="sm"
                    aria-pressed={current}
                    onClick={() => onChooseSpace(space?.id ?? null)}
                    className={cn(
                      "max-w-48 rounded-full font-normal text-muted-foreground",
                      current && "bg-muted text-foreground dark:bg-muted/50",
                    )}
                  >
                    <SpaceIcon space={space} />
                    <span className="truncate">{spaceLabel(space)}</span>
                  </Button>
                )
              })}
            </ChipRow>
          )}
          {filed.length > 0 && (
            <ChipRow label="Folders">
              {filed.map(({ folder, count }) => (
                <Button
                  key={folder.id}
                  variant="ghost"
                  size="sm"
                  onClick={() => onOpenFolder(folder)}
                  aria-label={`${folder.name}, ${count} chat${count === 1 ? "" : "s"}`}
                  className="max-w-48 rounded-full font-normal text-muted-foreground"
                >
                  <Folder />
                  <span className="truncate">{folder.name}</span>
                  <span className="text-xs tabular-nums opacity-70">{count}</span>
                </Button>
              ))}
            </ChipRow>
          )}
        </div>
      )}
    </div>
  )
}

// A label and its chips, on one line that wraps.
function ChipRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1 pl-3">
      <span aria-hidden className="mr-2 w-14 shrink-0 text-xs font-medium text-muted-foreground">
        {label}
      </span>
      {children}
    </div>
  )
}
