import { Check, TriangleAlert } from "lucide-react"
import { Link } from "react-router"

import { AsciiProgress } from "@/components/ascii/ascii-progress"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { indexProblem } from "@/lib/index-reasons"
import { useIndexStatus } from "@/lib/index-status-context"
import { cn } from "@/lib/utils"

// Indexing stopped: a few plain words in the sidebar's foot (only the icon in the
// rail), never the exception itself. Its popover says why in a sentence; an admin also
// gets the error's text and a link to the workspace's jobs (Documents).
function IndexProblemStatus({ admin, compact }: { admin: boolean; compact: boolean }) {
  const { status } = useIndexStatus()
  const problem = indexProblem(status)
  if (!problem) return null
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex min-w-0 items-center gap-1.5 rounded-md text-xs text-destructive outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
          <span className={compact ? "sr-only" : "truncate"}>{problem.short}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent side={compact ? "right" : "top"} align="start" collisionPadding={16} className="w-80 max-w-[calc(100vw-2rem)]">
        <p className="font-medium">{problem.sentence}</p>
        {/* How far the run got before it stopped (four steps: read, split, embed, done). */}
        {status && status.total > 0 && status.step > 0 && (
          <AsciiProgress value={status.step} max={status.total} cells={8} label="How far indexing got" />
        )}
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

// Small status text for the sidebar's foot: "Indexing · Embedding 12 chunks", then
// "Index up to date" for a few seconds, or why it stopped. compact (the rail): the
// glyph alone, the words for assistive technology.
export function IndexStatusText({ admin = false, compact = false }: { admin?: boolean; compact?: boolean }) {
  const { status, justFinished } = useIndexStatus()
  if (!status) return null

  if (indexProblem(status)) return <IndexProblemStatus admin={admin} compact={compact} />
  if (status.status === "indexing") {
    return (
      <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
        <AsciiSpinner label="" size="xs" />
        <span className={compact ? "sr-only" : "truncate"}>
          Indexing · {status.message}
          {status.pending && " (more changes queued)"}
        </span>
      </span>
    )
  }
  if (justFinished) {
    return (
      <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground animate-in fade-in">
        <Check className="size-3.5 shrink-0 text-success" aria-hidden />
        <span className={compact ? "sr-only" : "truncate"}>
          Index up to date
          {status.summary && ` · ${status.summary.documents} docs, ${status.summary.chunks} chunks`}
        </span>
      </span>
    )
  }
  return null
}

// Thin signal-mint bar along the top edge of the content, beside the sidebar (the
// status in words is in the sidebar's foot). It grows with scaleX, not width (no
// layout per step), and fades once the index is up to date.
export function IndexProgressBar() {
  const { status, justFinished } = useIndexStatus()
  const indexing = status?.status === "indexing"
  const visible = indexing || justFinished
  // Never sit at 0%: show a sliver as soon as indexing starts.
  const pct = indexing ? Math.max(8, (status.step / status.total) * 100) : 100

  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-x-0 top-0 z-30 h-0.5 overflow-hidden transition-opacity duration-(--dur-reveal) ease-out-strong",
        visible ? "opacity-100" : "opacity-0",
      )}
    >
      <div
        className={cn(
          "h-full origin-left transition-transform duration-(--dur-reveal) ease-out-strong motion-reduce:transition-none",
          status?.status === "error" ? "bg-destructive" : "bg-signal",
        )}
        style={{ transform: `scaleX(${pct / 100})` }}
      />
    </div>
  )
}
