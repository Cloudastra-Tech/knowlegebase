import { use, useEffect, useRef, type CSSProperties } from "react"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink, FileText, Lock, Paperclip } from "lucide-react"

import "@/components/chat-motion.css"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import type { Source } from "@/lib/api"
import {
  CitationsContext,
  citationLabel,
  placeOf,
  previewOf,
  updatedAgo,
  updatedOn,
  whyOf,
} from "@/lib/citations"
import { queries } from "@/lib/queries"

// Inline citations (chat.md C13): an answer's [S#] markers as small numbered chips.
// Hovering or focusing one previews the passage; clicking opens the source viewer
// with the cited span highlighted. The plumbing is in lib/citations.ts.

// Mono, like the rest of the answer's machinery: [S1] reads as "1" in a small pill.
const CHIP =
  "mx-0.5 inline-flex h-4 min-w-4 -translate-y-px items-center justify-center rounded-full px-1 align-middle font-mono text-[0.6875rem] leading-none font-medium tabular-nums"

// One [S#] marker in the answer. Until its source is known (an answer still being
// written) it is a plain number; once known, a button in the signal mint that pops in
// (on a live answer only, never on one loaded from history), 40ms apart by number.
export function CitationChip({ n }: { n: number }) {
  const citations = use(CitationsContext)
  const source = citations?.byNumber.get(n)
  if (!citations || !source) return <span className={`${CHIP} bg-muted text-muted-foreground`}>{n}</span>
  return (
    <HoverCard openDelay={150} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          aria-label={citationLabel(n, source)}
          onClick={() => citations.open(source)}
          style={citations.live ? ({ "--i": Math.min(n - 1, 5) } as CSSProperties) : undefined}
          className={`${CHIP} ${citations.live ? "kb-pop" : ""} cursor-pointer bg-signal/15 text-signal transition-colors duration-(--dur-hover) ease-(--ease-hover) outline-none hover:bg-signal hover:text-signal-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=open]:bg-signal data-[state=open]:text-signal-foreground`}
        >
          {n}
        </button>
      </HoverCardTrigger>
      <HoverCardContent side="top" align="start" className="w-80 max-w-[calc(100vw-2rem)]">
        <SourcePreview source={source} />
      </HoverCardContent>
    </HoverCard>
  )
}

function RestrictedBadge() {
  return (
    <Badge variant="outline" className="shrink-0">
      <Lock />
      Restricted
    </Badge>
  )
}

// Who may read a source: a file the asker attached (or keeps in their space) is
// theirs alone, which is what "restricted" means for it.
function AccessBadge({ source }: { source: Source }) {
  if (source.attachment)
    return (
      <Badge variant="outline" className="shrink-0">
        <Paperclip />
        {source.attachment === "chat" ? "Attached" : "Your file"}
      </Badge>
    )
  return source.access === "restricted" ? <RestrictedBadge /> : null
}

// The hover card: which document, where in it, the passage, and why it was found.
function SourcePreview({ source }: { source: Source }) {
  const place = placeOf(source)
  const why = whyOf(source)
  const meta = [source.provider, updatedAgo(source.modified)].filter(Boolean).join(" · ")
  return (
    <div className="space-y-2">
      <div className="flex items-start gap-2">
        <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{source.title}</p>
          <p className="truncate text-xs text-muted-foreground">{meta}</p>
        </div>
        <AccessBadge source={source} />
      </div>
      {place && <p className="text-xs font-medium text-muted-foreground">{place}</p>}
      {source.snippet && (
        <p className="line-clamp-3 text-sm leading-snug">{previewOf(source.snippet)}</p>
      )}
      {why && <p className="text-xs text-muted-foreground">Why this source: {why}</p>}
    </div>
  )
}

// The source viewer: the passage with the document's text around it, the cited span
// highlighted and scrolled into view, and "Open original" when there is a link.
// Full screen on a phone, a side panel from `sm`.
export function SourceSheet({
  source,
  onOpenChange,
}: {
  source: Source | null
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet open={source !== null} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="kb-source-sheet gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-lg"
      >
        {source && <SourceSheetBody source={source} />}
      </SheetContent>
    </Sheet>
  )
}

function SourceSheetBody({ source }: { source: Source }) {
  const chunkId = source.chunk_id ?? ""
  const passage = useQuery({ ...queries.passage(chunkId), enabled: chunkId !== "" })
  const mark = useRef<HTMLElement>(null)
  const place = placeOf(passage.data ?? source)
  const updated = updatedOn(source.modified)
  const why = whyOf(source)
  const text = passage.data?.text ?? source.snippet ?? ""

  useEffect(() => {
    if (passage.data) mark.current?.scrollIntoView({ block: "center" })
  }, [passage.data])

  return (
    <>
      <SheetHeader className="border-b border-border/70 pr-12">
        <SheetTitle className="flex items-center gap-2">
          {source.n !== undefined && (
            <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-medium tabular-nums">
              {source.n}
            </span>
          )}
          <span className="min-w-0 truncate">{source.title}</span>
        </SheetTitle>
        <SheetDescription className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
          <span>{source.provider}</span>
          {place && <span>· {place}</span>}
          {updated && <span>· {updated}</span>}
          <AccessBadge source={source} />
        </SheetDescription>
      </SheetHeader>

      <div
        className="min-h-0 flex-1 overflow-y-auto p-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset"
        tabIndex={0}
        aria-label="Passage"
      >
        {passage.isPending && chunkId ? (
          <div className="space-y-2" aria-busy="true">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-4/5" />
          </div>
        ) : (
          <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
            {passage.data?.before && (
              <span className="text-muted-foreground">{passage.data.before}</span>
            )}
            <mark
              ref={mark}
              className="rounded-sm bg-signal/15 px-0.5 text-foreground [box-decoration-break:clone]"
            >
              {text}
            </mark>
            {passage.data?.after && (
              <span className="text-muted-foreground">{passage.data.after}</span>
            )}
          </p>
        )}
        {passage.isError && (
          <p className="mt-3 text-xs text-muted-foreground">
            This is the passage as it was cited: the document has changed since, or your role can't
            open it now.
          </p>
        )}
        {why && <p className="mt-4 text-xs text-muted-foreground">Why this source: {why}</p>}
      </div>

      {source.url && (
        <SheetFooter className="border-t border-border/70">
          <Button variant="outline" asChild>
            <a href={source.url} target="_blank" rel="noreferrer">
              <ExternalLink />
              Open original
            </a>
          </Button>
        </SheetFooter>
      )}
    </>
  )
}
