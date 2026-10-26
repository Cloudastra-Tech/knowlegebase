import { use, useEffect, useRef } from "react"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink, FileText, Lock } from "lucide-react"

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

const CHIP =
  "mx-0.5 inline-flex h-4 min-w-4 -translate-y-px items-center justify-center rounded-full bg-muted px-1 align-middle text-xs leading-none font-medium tabular-nums text-muted-foreground"

// One [S#] marker in the answer. Until its source is known (an answer still being
// written) it is a plain number.
export function CitationChip({ n }: { n: number }) {
  const citations = use(CitationsContext)
  const source = citations?.byNumber.get(n)
  if (!citations || !source) return <span className={CHIP}>{n}</span>
  return (
    <HoverCard openDelay={150} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          aria-label={citationLabel(n, source)}
          onClick={() => citations.open(source)}
          className={`${CHIP} cursor-pointer transition-colors outline-none hover:bg-foreground hover:text-background focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=open]:bg-foreground data-[state=open]:text-background`}
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
        {source.access === "restricted" && <RestrictedBadge />}
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
        className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-lg"
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
          {source.access === "restricted" && <RestrictedBadge />}
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
              className="rounded-sm bg-foreground/10 px-0.5 text-foreground [box-decoration-break:clone]"
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
