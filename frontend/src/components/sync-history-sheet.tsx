import { useQuery } from "@tanstack/react-query"
import { Check, TriangleAlert, X, type LucideIcon } from "lucide-react"

import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { Badge } from "@/components/ui/badge"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import type { Integration, SyncRun } from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// Why an item wasn't synced, in words (kb/services/sync.py, Skipped reasons).
const SKIP_REASONS: Record<string, string> = {
  too_large: "Too large to sync",
  unsupported: "A file type that can't be read",
  unreadable: "Couldn't be read",
  empty: "Has no text",
  not_found: "No longer exists",
  no_access: "The connected account can't open it",
}

const KIND: Record<SyncRun["kind"], string> = {
  full: "Full",
  incremental: "Changes only",
  imported: "Imported",
}

function counts(run: SyncRun, unit: string) {
  const parts = []
  if (run.upserted) parts.push(`${run.upserted} added or updated`)
  if (run.deleted) parts.push(`${run.deleted} removed`)
  if (run.skipped) parts.push(`${run.skipped} skipped`)
  if (run.unchanged) parts.push(`${run.unchanged} unchanged`)
  return parts.join(" · ") || `No ${unit} changed`
}

// A run's state as an icon, coloured, with its word for assistive technology.
const GLYPH: Record<Exclude<SyncRun["status"], "running">, { icon: LucideIcon; label: string; tone: string }> = {
  failed: { icon: X, label: "Failed", tone: "text-destructive" },
  held: { icon: TriangleAlert, label: "Held back", tone: "text-warning" },
  done: { icon: Check, label: "Done", tone: "text-success" },
}

// One run on the timeline: "├─ ✓ 5 minutes ago  Changes only  3 s".
function RunRow({ run, unit, last }: { run: SyncRun; unit: string; last: boolean }) {
  const mark = run.status === "running" ? null : (GLYPH[run.status] ?? GLYPH.done)
  const seconds = run.finished_at ? Math.max(0, Math.round(run.finished_at - run.started_at)) : null
  return (
    <li className="flex gap-2 py-2.5">
      <span aria-hidden className="font-mono text-sm leading-5 text-muted-foreground select-none">
        {last ? "└─" : "├─"}
      </span>
      <span className="flex h-5 w-4 shrink-0 items-center justify-center font-mono text-sm leading-5">
        {mark ? (
          <>
            <mark.icon aria-hidden className={`size-3.5 ${mark.tone}`} />
            <span className="sr-only">{mark.label}</span>
          </>
        ) : (
          <AsciiSpinner label="Running" className="text-signal" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-mono text-sm leading-5 tabular-nums">{timeAgo(run.started_at)}</span>
          <Badge variant="mono">{KIND[run.kind] ?? run.kind}</Badge>
          {seconds !== null && run.kind !== "imported" && (
            <span className="font-mono text-xs text-muted-foreground tabular-nums">{seconds} s</span>
          )}
        </div>
        <p className="mt-0.5 text-sm text-muted-foreground">{counts(run, unit)}</p>
        {run.held_deletes > 0 && (
          <p className="mt-1 text-sm text-warning">
            {run.held_deletes} removals held back for an admin to confirm.
          </p>
        )}
        {run.limit_reached > 0 && (
          <p className="mt-1 text-sm text-muted-foreground">
            Stopped at the limit of {run.limit_reached} {unit}.
          </p>
        )}
        {run.error && run.status === "failed" && (
          <p className="mt-1 text-sm text-destructive">{run.error.message}</p>
        )}
      </div>
    </li>
  )
}

// A connection's last syncs, newest first, and what it skipped (and why).
export function SyncHistorySheet({
  item,
  open,
  onOpenChange,
}: {
  item: Integration
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { data } = useQuery({
    ...queries.integrationRuns(item.id),
    enabled: open,
    refetchInterval: open && item.status === "syncing" ? 3000 : false,
  })
  const unit = item.unit ?? "items"

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle>{item.name}: sync history</SheetTitle>
          <SheetDescription>
            The last syncs of this connection, and anything it couldn't sync.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4">
          {!data ? (
            <div className="space-y-3 py-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : data.runs.length === 0 ? (
            <AsciiEmptyState art="inbox" size="sm" title="No syncs yet." />
          ) : (
            <ul aria-label="Syncs" className="py-2">
              {data.runs.map((run, i) => (
                <RunRow key={run.id} run={run} unit={unit} last={i === data.runs.length - 1} />
              ))}
            </ul>
          )}
          {data && data.skipped.length > 0 && (
            <>
              <AsciiDivider as="h3" label="Not synced" className="mt-2 pt-2" />
              <ul className="divide-y pb-4">
                {data.skipped.map((s, i) => (
                  <li key={`${s.title}-${i}`} className="flex flex-col py-2">
                    {s.url ? (
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noreferrer"
                        className="truncate text-sm underline-offset-4 hover:underline"
                      >
                        {s.title}
                      </a>
                    ) : (
                      <span className="truncate text-sm">{s.title}</span>
                    )}
                    <span className="text-xs text-muted-foreground">
                      {SKIP_REASONS[s.reason] ?? "Not synced"}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
