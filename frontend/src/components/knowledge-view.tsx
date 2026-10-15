import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"
import { BookOpen, ExternalLink, FileText, Globe, Lock, Search } from "lucide-react"
import { siGoogledrive, siNotion } from "simple-icons"

import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, type KnowledgeItem } from "@/lib/api"
import { useIndexStatus } from "@/lib/index-status-context"

function when(modified: KnowledgeItem["modified"]) {
  if (!modified) return "—"
  const d = typeof modified === "number" ? new Date(modified * 1000) : new Date(modified)
  return isNaN(d.getTime())
    ? "—"
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
}

function SourceIcon({ source }: { source: string }) {
  const brand = source === "Google Drive" ? siGoogledrive : source === "Notion" ? siNotion : null
  if (brand)
    return (
      <svg viewBox="0 0 24 24" className="size-4 shrink-0 fill-current text-muted-foreground" aria-hidden>
        <path d={brand.path} />
      </svg>
    )
  return <FileText className="size-4 shrink-0 text-muted-foreground" />
}

// Everything the assistant can search in this workspace, in one list.
export function KnowledgeView() {
  const [items, setItems] = useState<KnowledgeItem[] | null>(null)
  const [workspace, setWorkspace] = useState("")
  const [outline, setOutline] = useState(false)
  const [query, setQuery] = useState("")
  const [source, setSource] = useState("all")
  const { status: indexStatus } = useIndexStatus()

  // Reload when an index run finishes, so chunk counts stay current.
  const finishedAt = indexStatus?.finished_at
  useEffect(() => {
    api
      .knowledge()
      .then((d) => {
        setItems(d.items)
        setWorkspace(d.workspace)
        setOutline(d.outline)
      })
      .catch((err) => toast.error(err instanceof Error ? err.message : "Couldn't load"))
  }, [finishedAt])

  const sources = useMemo(() => [...new Set((items ?? []).map((i) => i.source))], [items])
  const visible = (items ?? []).filter(
    (i) =>
      (source === "all" || i.source === source) &&
      i.title.toLowerCase().includes(query.trim().toLowerCase()),
  )
  const totalChunks = (items ?? []).reduce((n, i) => n + i.chunks, 0)

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Knowledge base</h2>
          <p className="text-sm text-muted-foreground">
            Everything the assistant can search in the{" "}
            <span className="font-medium text-foreground">{workspace || "…"}</span> workspace.
          </p>
        </div>
        {items && (
          <div className="flex gap-6 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">Documents</p>
              <p className="font-semibold tabular-nums">{items.length}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Indexed chunks</p>
              <p className="font-semibold tabular-nums">{totalChunks}</p>
            </div>
          </div>
        )}
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <div className="relative min-w-60 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name…"
            className="pl-9"
          />
        </div>
        <Tabs value={source} onValueChange={setSource}>
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            {sources.map((s) => (
              <TabsTrigger key={s} value={s}>
                {s}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="mt-4 overflow-hidden rounded-2xl border">
        <div className="hidden grid-cols-[1fr_140px_100px_110px_110px] gap-4 border-b bg-muted/40 px-4 py-2.5 text-xs font-medium text-muted-foreground md:grid">
          <span>Name</span>
          <span>Source</span>
          <span>Access</span>
          <span>Updated</span>
          <span className="text-right">In search</span>
        </div>

        {items === null ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <p className="p-10 text-center text-sm text-muted-foreground">
            {items.length ? "Nothing matches your search." : "No documents yet. Upload files or connect Google Drive or Notion."}
          </p>
        ) : (
          visible.map((i) => (
            <div
              key={`${i.source}/${i.title}/${i.url ?? ""}`}
              className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 border-b px-4 py-3 text-sm last:border-b-0 md:grid-cols-[1fr_140px_100px_110px_110px]"
            >
              <div className="flex min-w-0 items-center gap-2.5">
                <SourceIcon source={i.source} />
                {i.url ? (
                  <a
                    href={i.url}
                    target="_blank"
                    rel="noreferrer"
                    className="group flex min-w-0 items-center gap-1.5 font-medium hover:underline"
                  >
                    <span className="truncate">{i.title}</span>
                    <ExternalLink className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-60" />
                  </a>
                ) : (
                  <span className="truncate font-medium">{i.title}</span>
                )}
              </div>
              <span className="hidden text-muted-foreground md:block">{i.source}</span>
              <span className="hidden md:block">
                {i.access === "restricted" ? (
                  <span className="flex items-center gap-1 text-destructive">
                    <Lock className="size-3.5" /> HR only
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-muted-foreground">
                    <Globe className="size-3.5" /> Everyone
                  </span>
                )}
              </span>
              <span className="hidden text-muted-foreground md:block">{when(i.modified)}</span>
              <span className="text-right">
                {i.chunks > 0 ? (
                  <Badge className="bg-emerald-600/10 font-normal text-emerald-700 hover:bg-emerald-600/10 dark:text-emerald-400">
                    {i.chunks} chunk{i.chunks === 1 ? "" : "s"}
                  </Badge>
                ) : (
                  <Badge variant="outline" className="font-normal text-muted-foreground">
                    Not indexed
                  </Badge>
                )}
              </span>
            </div>
          ))
        )}
      </div>

      {outline && (
        <p className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
          <BookOpen className="size-4" />
          The Outline wiki is searched live on every question, so its pages aren't listed here.
        </p>
      )}
    </div>
  )
}
