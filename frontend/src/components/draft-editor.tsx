import { useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, FilePen, Globe, Loader2, Lock, Save, Send, Trash2, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { drafts, type Access, type Draft } from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn } from "@/lib/utils"

// The assistant's drafts on the Documents screen (platform.md PL-31): a source's or
// folder's summary, or a doc for a content gap. HR reviews and edits one here, then
// publishes it as a document (it's indexed then) or deletes it. A draft is never
// searchable, and can't be published to more people than its sources are for.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const STATUS: Record<Draft["status"], string> = {
  generating: "Writing…",
  draft: "Draft",
  failed: "Failed",
  published: "Published",
}

export function DraftStatusBadge({ draft }: { draft: Draft }) {
  return (
    <Badge variant={draft.status === "failed" ? "destructive" : "outline"} className="gap-1">
      {draft.status === "generating" && <Loader2 className="size-3 animate-spin" />}
      {STATUS[draft.status]}
    </Badge>
  )
}

// One row of the drafts list (the Documents screen's left column).
export function DraftRow({
  draft,
  selected,
  onPick,
}: {
  draft: Draft
  selected: boolean
  onPick: () => void
}) {
  return (
    <button
      onClick={onPick}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left text-sm transition-colors hover:bg-muted",
        selected && "bg-muted",
      )}
    >
      <FilePen className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate font-medium">{draft.title}</span>
      {draft.status === "generating" ? (
        <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
      ) : draft.status === "failed" ? (
        <TriangleAlert className="size-3.5 shrink-0 text-destructive" />
      ) : draft.max_access === "restricted" ? (
        <Lock className="size-3.5 shrink-0 text-destructive" />
      ) : (
        <Globe className="size-3.5 shrink-0 text-muted-foreground" />
      )}
    </button>
  )
}

function fileName(title: string) {
  const stem = title.replace(/[^A-Za-z0-9 _.-]/g, "").trim().replace(/\s+/g, "_")
  return `${stem || "draft"}.txt`
}

function PublishDialog({ draft, onPublished }: { draft: Draft; onPublished: (key: string) => void }) {
  const restricted = draft.max_access === "restricted"
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(() => fileName(draft.title))
  const [access, setAccess] = useState<Access>(restricted ? "restricted" : "public")
  const [saving, setSaving] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const done = await drafts.publish(draft.id, access, name)
      toast.success(`Published as ${done.published_as} · updating the index`)
      setOpen(false)
      onPublished(done.published_as ?? "")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" disabled={draft.status !== "draft"}>
          <Send /> Publish
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>Publish this draft</DialogTitle>
            <DialogDescription>
              It becomes a document the assistant answers from, with its sources listed at
              the end. Check every statement first.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <Field>
              <FieldLabel htmlFor="publish-name">File name</FieldLabel>
              <Input
                id="publish-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={120}
                required
              />
            </Field>
            <Field>
              <FieldLabel>Who can see it?</FieldLabel>
              <RadioGroup value={access} onValueChange={(v) => setAccess(v as Access)} className="gap-3">
                <div className="flex items-center gap-2">
                  <RadioGroupItem value="public" id="publish-public" disabled={restricted} />
                  <FieldLabel htmlFor="publish-public" className="font-normal">
                    Public (everyone)
                  </FieldLabel>
                </div>
                <div className="flex items-center gap-2">
                  <RadioGroupItem value="restricted" id="publish-restricted" />
                  <FieldLabel htmlFor="publish-restricted" className="font-normal">
                    Restricted (HR only)
                  </FieldLabel>
                </div>
              </RadioGroup>
              {restricted && (
                <FieldDescription>
                  It was written from HR-only documents, so it can only be published as HR only.
                </FieldDescription>
              )}
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving || !name.trim()}>
              {saving && <Loader2 className="animate-spin" data-icon="inline-start" />}
              Publish
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function DraftEditor({
  id,
  onPublished,
  onDeleted,
}: {
  id: string
  onPublished: (key: string) => void
  onDeleted: () => void
}) {
  // While it's being written, look again every two seconds.
  const { data: draft } = useQuery({
    ...queries.draft(id),
    refetchInterval: (q) => (q.state.data?.status === "generating" ? 2000 : false),
  })
  if (!draft) return <Skeleton className="h-96 rounded-2xl" />
  // A new status (written, failed) starts the editor again with the text as it is now;
  // after that, what's typed is kept while the draft is looked at again.
  return <LoadedDraft key={draft.status} draft={draft} onPublished={onPublished} onDeleted={onDeleted} />
}

function LoadedDraft({
  draft,
  onPublished,
  onDeleted,
}: {
  draft: Draft
  onPublished: (key: string) => void
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const id = draft.id
  const [content, setContent] = useState<string | null>(() =>
    draft.status === "draft" ? (draft.content ?? "") : null,
  )
  const [title, setTitle] = useState<string>(draft.title)
  const [saving, setSaving] = useState(false)

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["drafts"] })

  async function save() {
    if (content === null) return
    setSaving(true)
    try {
      await drafts.save(id, { content, title })
      toast.success("Draft saved.")
      await refresh()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    try {
      await drafts.remove(id)
      toast.success("Draft deleted.")
      await refresh()
      onDeleted()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const dirty = draft.status === "draft" && content !== null &&
    (content !== (draft.content ?? "") || title !== draft.title)

  return (
    <Card className="flex min-h-0 flex-col md:h-full">
      <CardHeader className="flex flex-col gap-3 space-y-0 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1.5">
          <CardTitle className="truncate text-base">{draft.title}</CardTitle>
          <CardDescription className="flex flex-wrap items-center gap-2">
            <DraftStatusBadge draft={draft} />
            <span>Not searchable until published</span>
          </CardDescription>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={save} disabled={!dirty || saving}>
            {saving ? <Loader2 className="animate-spin" /> : <Save />}
            Save
          </Button>
          <PublishDialog
            key={draft.status}
            draft={draft}
            onPublished={async (key) => {
              await refresh()
              onPublished(key)
            }}
          />
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" variant="outline" className="text-destructive">
                <Trash2 /> Delete
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete this draft?</AlertDialogTitle>
                <AlertDialogDescription>It isn't published, so nothing else changes.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={remove}>Delete</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-4">
        {draft.status === "generating" ? (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              The assistant is reading the documents and writing it. This takes a minute.
            </p>
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : draft.status === "failed" ? (
          <Alert variant="destructive" className="border-destructive/30 bg-destructive/5">
            <TriangleAlert />
            <AlertTitle>It couldn't be written</AlertTitle>
            <AlertDescription className="text-destructive/90">{draft.error}</AlertDescription>
          </Alert>
        ) : (
          <>
            <Alert>
              <TriangleAlert />
              <AlertTitle>Written by the assistant</AlertTitle>
              <AlertDescription>
                Check every statement against its sources before publishing. Lines starting
                “TO CONFIRM:” need someone who knows.
              </AlertDescription>
            </Alert>
            <Field>
              <FieldLabel htmlFor="draft-title">Title</FieldLabel>
              <Input
                id="draft-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={200}
              />
            </Field>
            <Textarea
              aria-label="Draft text"
              value={content ?? ""}
              onChange={(e) => setContent(e.target.value)}
              className="h-[50svh] min-h-0 flex-1 resize-none overflow-y-auto font-mono text-sm field-sizing-fixed md:h-auto md:min-h-64"
            />
            {dirty && <p className="text-xs text-muted-foreground">Unsaved changes</p>}
          </>
        )}
        <div>
          <h3 className="text-sm font-medium">Sources</h3>
          {draft.sources.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {draft.status === "generating" ? "Listed once it's written." : "None: no document covered this. Check every line."}
            </p>
          ) : (
            <ol className="mt-1 flex flex-col gap-1 text-sm">
              {draft.sources.map((s, n) => (
                <li key={s.doc_key} className="flex min-w-0 items-center gap-2">
                  <span className="w-6 shrink-0 text-muted-foreground tabular-nums">[{n + 1}]</span>
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-1 hover:underline">
                      <span className="truncate">{s.title ?? s.doc_key}</span>
                      <ExternalLink className="size-3 shrink-0 opacity-60" />
                    </a>
                  ) : (
                    <span className="truncate">{s.title ?? s.doc_key}</span>
                  )}
                  {s.access === "restricted" ? (
                    <Lock className="size-3.5 shrink-0 text-destructive" aria-label="HR only" />
                  ) : (
                    <Globe className="size-3.5 shrink-0 text-muted-foreground" aria-label="Public" />
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
