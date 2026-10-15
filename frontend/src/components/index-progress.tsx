import { Check, Loader2, TriangleAlert } from "lucide-react"

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useIndexStatus } from "@/lib/index-status-context"
import { cn } from "@/lib/utils"

// Small status text for the navbar: "Indexing · Embedding 12 chunks", then
// "Index up to date" for a few seconds, or an error.
export function IndexStatusText() {
  const { status, justFinished } = useIndexStatus()
  if (!status) return null

  if (status.status === "indexing") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        <span className="hidden sm:inline">
          Indexing · {status.message}
          {status.pending && " (more changes queued)"}
        </span>
        <span className="sm:hidden">Indexing</span>
      </span>
    )
  }
  if (status.status === "error") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex cursor-default items-center gap-1.5 text-xs text-destructive">
            <TriangleAlert className="size-3.5" />
            Index failed
          </span>
        </TooltipTrigger>
        <TooltipContent>{status.error}</TooltipContent>
      </Tooltip>
    )
  }
  if (justFinished) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground animate-in fade-in">
        <Check className="size-3.5 text-emerald-600" />
        <span className="hidden sm:inline">
          Index up to date
          {status.summary && ` · ${status.summary.documents} docs, ${status.summary.chunks} chunks`}
        </span>
      </span>
    )
  }
  return null
}

// Thin bar along the bottom edge of the navbar.
export function IndexProgressBar() {
  const { status, justFinished } = useIndexStatus()
  const indexing = status?.status === "indexing"
  const visible = indexing || justFinished
  // Never sit at 0%: show a sliver as soon as indexing starts.
  const pct = indexing ? Math.max(8, (status.step / status.total) * 100) : 100

  return (
    <div
      className={cn(
        "pointer-events-none absolute inset-x-0 -bottom-px h-0.5 overflow-hidden transition-opacity duration-700",
        visible ? "opacity-100" : "opacity-0",
      )}
    >
      <div
        className={cn(
          "h-full transition-[width] duration-500 ease-out",
          status?.status === "error" ? "bg-destructive" : "bg-foreground",
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
