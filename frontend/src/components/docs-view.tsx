import { useCallback, useEffect, useRef, useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router"
import { toast } from "sonner"
import {
  Download,
  FilePlus2,
  FileText,
  Globe,
  Lock,
  RefreshCw,
  Save,
  Trash2,
  TriangleAlert,
  Upload,
  Users,
} from "lucide-react"

import { AccessPicker, EVERYONE, HR_GROUP, WhoCanSeeBadges } from "@/components/access-picker"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { ScreenHeader } from "@/components/screen-header"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { DraftEditor, DraftRow } from "@/components/draft-editor"
import { IndexAdminMenu } from "@/components/index-admin"
import { cn } from "@/lib/utils"
import { api, type Access, type Doc, type UploadLimits } from "@/lib/api"
import { useIndexStatus } from "@/lib/index-status-context"
import { queries } from "@/lib/queries"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const sameDoc = (a: Doc | null, b: Doc | null) =>
  !!a && !!b && a.access === b.access && a.name === b.name

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// Who can see a document: its ACL as the server labels it (an older list without
// one: its folder's).
function AccessBadge({ doc }: { doc: Doc }) {
  if (doc.who?.length) return <WhoCanSeeBadges who={doc.who} />
  return doc.access === "restricted" ? (
    <Badge variant="destructive" className="gap-1">
      <Lock className="size-3" /> HR only
    </Badge>
  ) : (
    <Badge variant="secondary" className="gap-1">
      <Globe className="size-3" /> Everyone
    </Badge>
  )
}

// Change who may see a document: its chunks are re-tagged, nothing is embedded again.
function AccessDialog({ doc, onSaved }: { doc: Doc; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [acl, setAcl] = useState<string[]>(doc.acl ?? [doc.access === "public" ? EVERYONE : HR_GROUP])
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true)
    try {
      await api.setDocAcl(doc, acl)
      toast.success(`Updated who can see ${doc.name} · updating the index`)
      setOpen(false)
      onSaved()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) setAcl(doc.acl ?? [doc.access === "public" ? EVERYONE : HR_GROUP])
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <Users /> Access
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Who can see {doc.name}?</DialogTitle>
          <DialogDescription>
            Only the people and groups picked here can ask about it, or see answers that quote
            it. Groups gate content; roles only decide who manages it.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          <Label htmlFor="doc-access">Who can see it</Label>
          <AccessPicker id="doc-access" value={acl} onChange={setAcl} />
        </div>
        <DialogFooter>
          <Button onClick={save} loading={saving} disabled={!acl.length}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function NewDocDialog({ onCreated }: { onCreated: (d: Doc) => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [acl, setAcl] = useState<string[]>([EVERYONE])
  const [content, setContent] = useState("")
  const [saving, setSaving] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const created = await api.createDoc(name, acl, content)
      toast.success(`Added ${created.name}`)
      onCreated({ ...created, size: content.length })
      setOpen(false)
      setName("")
      setContent("")
      setAcl([EVERYONE])
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <FilePlus2 /> New
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>New document</DialogTitle>
            <DialogDescription>Write a text document the assistant can answer from.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="new-name">File name</Label>
            <Input
              id="new-name"
              placeholder="e.g. leave_policy"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="new-access">Who can see it?</Label>
            <AccessPicker id="new-access" value={acl} onChange={setAcl} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="new-content">Content</Label>
            <Textarea
              id="new-content"
              rows={8}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              required
            />
          </div>
          <DialogFooter>
            <Button type="submit" loading={saving}>
              Add document
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

const MB = 1024 * 1024

// Why these files can't go, before sending them (the server checks again).
function tooBig(files: File[], limits: UploadLimits | null) {
  if (!limits) return null
  const big = files.filter((f) => f.size > limits.file_mb * MB)
  if (big.length)
    return `${big.map((f) => `${f.name} (${formatSize(f.size)})`).join(", ")} ${
      big.length === 1 ? "is" : "are"
    } over the ${limits.file_mb} MB limit for one file.`
  const total = files.reduce((sum, f) => sum + f.size, 0)
  if (total > limits.request_mb * MB)
    return `These files come to ${formatSize(total)}, over the ${limits.request_mb} MB limit for one upload. Upload them in smaller batches.`
  return null
}

function UploadDialog({
  onUploaded,
  limits,
}: {
  onUploaded: () => void
  limits: UploadLimits | null
}) {
  const [open, setOpen] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [acl, setAcl] = useState<string[]>([EVERYONE])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const problem = error ?? tooBig(files, limits)

  function pick(picked: File[]) {
    setFiles(picked)
    setError(null)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!files.length || problem) return
    setUploading(true)
    try {
      const { saved } = await api.uploadDocs(files, acl)
      toast.success(`Uploaded ${saved.join(", ")}`)
      onUploaded()
      setOpen(false)
      setFiles([])
    } catch (err) {
      setError(errorText(err))         // e.g. too big (413), or not what its name says (415)
    } finally {
      setUploading(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        setError(null)
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Upload /> Upload
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Upload files</DialogTitle>
            <DialogDescription>
              Text, PDF, Word, PowerPoint, Excel, Markdown or HTML
              {limits ? `, up to ${limits.file_mb} MB each` : ""}. A file with the same name is
              replaced.
            </DialogDescription>
          </DialogHeader>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              pick(Array.from(e.dataTransfer.files))
            }}
            className="flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-sm text-muted-foreground transition-colors hover:bg-muted/50"
          >
            <Upload className="size-6" />
            {files.length
              ? files.map((f) => f.name).join(", ")
              : "Click to choose files, or drop them here"}
          </button>
          <Input
            ref={inputRef}
            type="file"
            accept=".txt,.pdf,.md,.html,.docx,.pptx,.xlsx"
            multiple
            hidden
            className="hidden"
            onChange={(e) => pick(Array.from(e.target.files ?? []))}
          />
          {problem && (
            <Alert variant="destructive" className="border-destructive/30 bg-destructive/5">
              <TriangleAlert />
              <AlertDescription className="text-destructive/90">{problem}</AlertDescription>
            </Alert>
          )}
          <div className="grid gap-2">
            <Label htmlFor="upload-access">Who can see them?</Label>
            <AccessPicker id="upload-access" value={acl} onChange={setAcl} />
          </div>
          <DialogFooter>
            <Button type="submit" loading={uploading} disabled={!files.length || !!problem}>
              Upload
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function DocEditor({
  doc,
  onSaved,
  onDeleted,
}: {
  doc: Doc
  onSaved: () => void
  onDeleted: () => void
}) {
  // Not shared with the asker: they manage it (access, delete) but can't open it.
  const sealed = doc.readable === false
  const isText = doc.name.toLowerCase().endsWith(".txt") && !sealed
  const [original, setOriginal] = useState("")
  const [content, setContent] = useState("")
  const [loading, setLoading] = useState(isText)
  const [saving, setSaving] = useState(false)

  // Read once per document (it starts out loading, see above). Keyed on access +
  // name, not the object: a refreshed list hands us a new object for the same
  // file, and re-reading then would overwrite what's being typed.
  const { access, name } = doc
  useEffect(() => {
    if (!isText) return
    let cancelled = false
    api
      .readDoc({ access, name })
      .then(({ content }) => {
        if (cancelled) return
        setOriginal(content)
        setContent(content)
      })
      .catch((err) => toast.error(errorText(err)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [access, name, isText])

  async function save() {
    setSaving(true)
    try {
      await api.saveDoc(doc, content)
      setOriginal(content)
      toast.success(`Saved ${doc.name} · updating the index`)
      onSaved()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    try {
      await api.deleteDoc(doc)
      toast.success(`Deleted ${doc.name} · updating the index`)
      onDeleted()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const dirty = content !== original

  return (
    <Card className="flex min-h-0 flex-col md:h-full">
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="min-w-0 space-y-1.5">
          <CardTitle className="truncate text-base">{doc.name}</CardTitle>
          <CardDescription className="flex flex-wrap items-center gap-2">
            <AccessBadge doc={doc} />
            <span>{formatSize(doc.size)}</span>
          </CardDescription>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          <AccessDialog doc={doc} onSaved={onSaved} />
          {isText ? (
            <Button size="sm" onClick={save} loading={saving} disabled={!dirty}>
              {!saving && <Save />}
              Save
            </Button>
          ) : sealed ? null : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => api.downloadDoc(doc).catch((e) => toast.error(errorText(e)))}
            >
              <Download /> Download
            </Button>
          )}
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" variant="outline" className="text-destructive">
                <Trash2 /> Delete
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete {doc.name}?</AlertDialogTitle>
                <AlertDialogDescription>
                  The file is removed from the docs folder. After you re-index, the assistant
                  can no longer answer from it.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={remove}>Delete</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col">
        {sealed ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            <Lock className="size-8" />
            Only the people and groups it's shared with can open it. You can still change
            who can see it, or delete it.
          </div>
        ) : !isText ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            <FileText className="size-8" />
            Only text files can be edited here. Download it, change it, then upload it again
            with the same name to replace it.
          </div>
        ) : loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : (
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="h-[60svh] min-h-0 flex-1 resize-none overflow-y-auto font-mono text-sm field-sizing-fixed md:h-auto"
          />
        )}
        {dirty && <p className="mt-2 text-xs text-muted-foreground">Unsaved changes</p>}
      </CardContent>
    </Card>
  )
}

const NO_DOCS: Doc[] = []

// admin: also the index's admin tools (rebuild from scratch, background jobs).
export function DocsView({ admin = false }: { admin?: boolean }) {
  const queryClient = useQueryClient()
  const { data, isPending: loading } = useQuery(queries.docs())
  const docs = data?.docs ?? NO_DOCS
  const uploadLimits = data?.upload_limits ?? null
  const { status: indexStatus, refresh: refreshIndex } = useIndexStatus()
  const indexing = indexStatus?.status === "indexing"
  // ?draft=<id>: one of the assistant's drafts (from Knowledge or Insights).
  const [params, setParams] = useSearchParams()
  const pickedDraft = params.get("draft")
  const [filter, setFilter] = useState<"all" | Access | "drafts">(() =>
    pickedDraft ? "drafts" : "all",
  )
  // The picked document as the latest list has it: null until it's there, and
  // once it's gone.
  const [picked, setPicked] = useState<{ access: Access; name: string } | null>(null)
  const selected = docs.find((d) => sameDoc(d, picked as Doc)) ?? null
  // The drafts (HR); looked at again every few seconds while one is being written.
  const { data: draftList } = useQuery({
    ...queries.drafts(),
    refetchInterval: (q) =>
      q.state.data?.drafts.some((d) => d.status === "generating") ? 3000 : false,
  })
  const draftRows = draftList?.drafts ?? []
  const pickDraft = (id: string | null) =>
    setParams(id ? { draft: id } : {}, { replace: true })
  // ?jobs=1: the background jobs, opened (the header's "See the background jobs").
  const jobsOpen = admin && params.get("jobs") === "1"
  const setJobsOpen = (open: boolean) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p)
        if (open) next.set("jobs", "1")
        else next.delete("jobs")
        return next
      },
      { replace: true },
    )

  const refresh = useCallback(
    async (select?: { access: Access; name: string }) => {
      if (select) setPicked(select)
      await queryClient.invalidateQueries({ queryKey: queries.docs().queryKey })
      refreshIndex()                        // a change just started a re-index
    },
    [queryClient, refreshIndex],
  )

  async function reindex() {
    try {
      await api.reindex()
      refreshIndex()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const visible = docs.filter((d) => filter === "all" || d.access === filter)
  const showDrafts = filter === "drafts"

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6 md:h-full">
      <ScreenHeader
        title="Documents"
        description="Add, edit or remove what the assistant answers from. Changes are indexed automatically."
        actions={
          <>
            <Button variant="outline" onClick={reindex} loading={indexing} disabled={!docs.length}>
              {!indexing && <RefreshCw />}
              {indexing ? "Indexing…" : "Re-index now"}
            </Button>
            {admin && (
              <IndexAdminMenu
                documents={docs.length}
                indexing={indexing}
                jobsOpen={jobsOpen}
                onJobsOpenChange={setJobsOpen}
                onStarted={refreshIndex}
              />
            )}
          </>
        }
      />

      <div className="grid gap-4 md:min-h-0 md:flex-1 md:grid-cols-[320px_1fr] md:grid-rows-[minmax(0,1fr)]">
        <Card variant="glass" className="flex max-h-96 min-h-0 flex-col gap-3 py-4 md:max-h-none">
          <div className="flex items-center justify-between gap-2 px-4">
            <Tabs panels={false} value={filter} onValueChange={(v) => setFilter(v as typeof filter)}>
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="public">Everyone</TabsTrigger>
                <TabsTrigger value="restricted">Limited</TabsTrigger>
                <TabsTrigger value="drafts">
                  Drafts{draftRows.length ? ` (${draftRows.length})` : ""}
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          <div className="flex gap-2 px-4">
            <NewDocDialog onCreated={(d) => refresh(d)} />
            <UploadDialog onUploaded={() => refresh()} limits={uploadLimits} />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2">
            {showDrafts ? (
              draftRows.length === 0 ? (
                <AsciiEmptyState
                  art="inbox"
                  size="sm"
                  title="No drafts."
                  description="Summarise a source on the Knowledge screen, or draft a doc for a content gap in Insights."
                />
              ) : (
                draftRows.map((d) => (
                  <DraftRow
                    key={d.id}
                    draft={d}
                    selected={d.id === pickedDraft}
                    onPick={() => pickDraft(d.id)}
                  />
                ))
              )
            ) : loading ? (
              <div className="space-y-2 px-2">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : visible.length === 0 ? (
              <AsciiEmptyState art="folder" size="sm" title="No documents here yet." />
            ) : (
              visible.map((d) => (
                <button
                  key={`${d.access}/${d.name}`}
                  onClick={() => setPicked(d)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left text-sm transition-colors hover:bg-muted",
                    sameDoc(selected, d) && "bg-muted",
                  )}
                >
                  <FileText className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate font-medium">{d.name}</span>
                  {d.access === "restricted" ? (
                    <Lock className="size-3.5 shrink-0 text-destructive" />
                  ) : (
                    <Globe className="size-3.5 shrink-0 text-muted-foreground" />
                  )}
                </button>
              ))
            )}
          </div>
        </Card>

        {showDrafts ? (
          pickedDraft ? (
            <DraftEditor
              key={pickedDraft}
              id={pickedDraft}
              onPublished={(key) => {
                pickDraft(null)
                const [access, ...rest] = key.split("/")
                setFilter("all")
                refresh({ access: access as Access, name: rest.join("/") })
              }}
              onDeleted={() => pickDraft(null)}
            />
          ) : (
            <Card className="flex items-center justify-center p-6">
              <AsciiEmptyState art="inbox" title="No draft open" description="Pick a draft on the left to review it." />
            </Card>
          )
        ) : selected ? (
          <DocEditor
            key={`${selected.access}/${selected.name}`}
            doc={selected}
            onSaved={() => refresh()}
            onDeleted={() => {
              setPicked(null)
              refresh()
            }}
          />
        ) : (
          <Card className="flex items-center justify-center p-6">
            <AsciiEmptyState
              art="folder"
              title="No document open"
              description="Pick a document on the left to view or edit it."
            />
          </Card>
        )}
      </div>
    </div>
  )
}
