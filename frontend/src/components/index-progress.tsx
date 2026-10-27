import { Check, Loader2, TriangleAlert } from "lucide-react"
import { Link } from "react-router"

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { indexProblem } from "@/lib/index-reasons"
import { useIndexStatus } from "@/lib/index-status-context"
import { cn } from "@/lib/utils"

// Indexing stopped: a few plain words in the header (only the icon on a phone), never
// the exception itself. Its popover says why in a sentence; an admin also gets the
// error's text and a link to the workspace's jobs (Documents).
function IndexProblemStatus({ admin }: { admin: boolean }) {
  const { status } = useIndexStatus()
  const problem = indexProblem(status)
  if (!problem) return null
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex max-w-40 min-w-0 items-center gap-1.5 rounded-md text-xs text-destructive outline-none focus-visible:ring-2 focus-visible:ring-ring/50 md:max-w-56"
        >
          <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
          <span className="sr-only sm:not-sr-only sm:truncate">{problem.short}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" collisionPadding={16} className="w-80 max-w-[calc(100vw-2rem)]">
        <p className="font-medium">{problem.sentence}</p>
        {admin ? (
          <>
            {status?.error && (
              <p className="line-clamp-4 font-mono text-xs break-words text-muted-foreground">
                {status.error}
              </p>
            )}
            <Link
              to="/documents?jobs=1"
              className="text-sm font-medium underline underline-offset-2"
            >
              See the background jobs
            </Link>
          </>
        ) : (
          <p className="text-muted-foreground">
            Answers still come from the documents indexed before. Your admin can fix it.
          </p>
        )}
      </PopoverContent>
    </Popover>
  )
}

// Small status text for the navbar: "Indexing · Embedding 12 chunks", then
// "Index up to date" for a few seconds, or why it stopped.
export function IndexStatusText({ admin = false }: { admin?: boolean }) {
  const { status, justFinished } = useIndexStatus()
  if (!status) return null

  if (indexProblem(status)) return <IndexProblemStatus admin={admin} />
  if (status.status === "indexing") {
    return (
      <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 shrink-0 animate-spin" />
        <span className="hidden max-w-56 truncate sm:inline lg:max-w-72">
          Indexing · {status.message}
          {status.pending && " (more changes queued)"}
        </span>
        <span className="sm:hidden">Indexing</span>
      </span>
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
