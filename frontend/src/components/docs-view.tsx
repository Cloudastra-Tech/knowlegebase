import { useCallback, useEffect, useRef, useState, type FormEvent } from "react"
import { toast } from "sonner"
import {
  Download,
  FilePlus2,
  FileText,
  Globe,
  Loader2,
  Lock,
  RefreshCw,
  Save,
  Trash2,
  Upload,
} from "lucide-react"

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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { api, type Access, type Doc } from "@/lib/api"
import { useIndexStatus } from "@/lib/index-status"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const sameDoc = (a: Doc | null, b: Doc | null) =>
  !!a && !!b && a.access === b.access && a.name === b.name

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function AccessBadge({ access }: { access: Access }) {
  return access === "restricted" ? (
    <Badge variant="destructive" className="gap-1">
      <Lock className="size-3" /> HR only
    </Badge>
  ) : (
    <Badge variant="secondary" className="gap-1">
      <Globe className="size-3" /> Public
    </Badge>
  )
}

function AccessPicker({
  value,
  onChange,
  idPrefix,
}: {
  value: Access
  onChange: (v: Access) => void
  idPrefix: string
}) {
  return (
    <RadioGroup
      value={value}
      onValueChange={(v) => onChange(v as Access)}
      className="flex gap-6"
    >
      <div className="flex items-center gap-2">
        <RadioGroupItem value="public" id={`${idPrefix}-public`} />
        <Label htmlFor={`${idPrefix}-public`} className="font-normal">
          Public (everyone)
        </Label>
      </div>
      <div className="flex items-center gap-2">
        <RadioGroupItem value="restricted" id={`${idPrefix}-restricted`} />
        <Label htmlFor={`${idPrefix}-restricted`} className="font-normal">
          Restricted (HR only)
        </Label>
      </div>
    </RadioGroup>
  )
}

function NewDocDialog({ onCreated }: { onCreated: (d: Doc) => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [access, setAccess] = useState<Access>("public")
  const [content, setContent] = useState("")
  const [saving, setSaving] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const created = await api.createDoc(name, access, content)
      toast.success(`Added ${created.name}`)
      onCreated({ ...created, size: content.length })
      setOpen(false)
      setName("")
      setContent("")
      setAccess("public")
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
            <Label>Who can see it?</Label>
            <AccessPicker value={access} onChange={setAccess} idPrefix="new" />
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
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="animate-spin" />}
              Add document
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function UploadDialog({ onUploaded }: { onUploaded: () => void }) {
  const [open, setOpen] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [access, setAccess] = useState<Access>("public")
  const [uploading, setUploading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!files.length) return
    setUploading(true)
    try {
      const { saved } = await api.uploadDocs(files, access)
      toast.success(`Uploaded ${saved.join(", ")}`)
      onUploaded()
      setOpen(false)
      setFiles([])
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setUploading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
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
              TXT or PDF. A file with the same name is replaced.
            </DialogDescription>
          </DialogHeader>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              setFiles(Array.from(e.dataTransfer.files))
            }}
            className="flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-sm text-muted-foreground transition-colors hover:bg-muted/50"
          >
            <Upload className="size-6" />
            {files.length
              ? files.map((f) => f.name).join(", ")
              : "Click to choose files, or drop them here"}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".txt,.pdf"
            multiple
            hidden
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
          <div className="grid gap-2">
            <Label>Who can see them?</Label>
            <AccessPicker value={access} onChange={setAccess} idPrefix="upload" />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={uploading || !files.length}>
              {uploading && <Loader2 className="animate-spin" />}
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
  const isText = doc.name.toLowerCase().endsWith(".txt")
  const [original, setOriginal] = useState("")
  const [content, setContent] = useState("")
  const [loading, setLoading] = useState(isText)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!isText) return
    let cancelled = false
    setLoading(true)
    api
      .readDoc(doc)
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
  }, [doc, isText])

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
    <Card className="flex h-full flex-col">
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="min-w-0 space-y-1.5">
          <CardTitle className="truncate text-base">{doc.name}</CardTitle>
          <CardDescription className="flex items-center gap-2">
            <AccessBadge access={doc.access} />
            <span>{formatSize(doc.size)}</span>
          </CardDescription>
        </div>
        <div className="flex shrink-0 gap-2">
          {isText ? (
            <Button size="sm" onClick={save} disabled={!dirty || saving}>
              {saving ? <Loader2 className="animate-spin" /> : <Save />}
              Save
            </Button>
          ) : (
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
      <CardContent className="flex flex-1 flex-col">
        {!isText ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            <FileText className="size-8" />
            PDFs can't be edited here. Download it, change it, then upload it again with the
            same name to replace it.
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
            className="min-h-80 flex-1 resize-none font-mono text-sm"
          />
        )}
        {dirty && <p className="mt-2 text-xs text-muted-foreground">Unsaved changes</p>}
      </CardContent>
    </Card>
  )
}

export function DocsView() {
  const [docs, setDocs] = useState<Doc[]>([])
  const { status: indexStatus, refresh: refreshIndex } = useIndexStatus()
  const indexing = indexStatus?.status === "indexing"
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<"all" | Access>("all")
  const [selected, setSelected] = useState<Doc | null>(null)

  const refresh = useCallback(async (select?: { access: Access; name: string }) => {
    try {
      const data = await api.listDocs()
      setDocs(data.docs)
      refreshIndex()                    // a change just started a re-index
      setSelected((current) => {
        const wanted = select ?? current
        return data.docs.find((d) => sameDoc(d, wanted as Doc)) ?? null
      })
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setLoading(false)
    }
  }, [refreshIndex])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function reindex() {
    try {
      await api.reindex()
      refreshIndex()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const visible = docs.filter((d) => filter === "all" || d.access === filter)

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col gap-4 px-4 py-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Documents</h2>
          <p className="text-sm text-muted-foreground">
            Add, edit or remove what the assistant answers from. Changes are indexed
            automatically.
          </p>
        </div>
        <Button variant="outline" onClick={reindex} disabled={indexing || !docs.length}>
          {indexing ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          {indexing ? "Indexing…" : "Re-index now"}
        </Button>
      </div>


      <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-[320px_1fr]">
        <Card className="flex min-h-0 flex-col gap-3 py-4">
          <div className="flex items-center justify-between gap-2 px-4">
            <Tabs value={filter} onValueChange={(v) => setFilter(v as typeof filter)}>
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="public">Public</TabsTrigger>
                <TabsTrigger value="restricted">HR</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          <div className="flex gap-2 px-4">
            <NewDocDialog onCreated={(d) => refresh(d)} />
            <UploadDialog onUploaded={() => refresh()} />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2">
            {loading ? (
              <div className="space-y-2 px-2">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : visible.length === 0 ? (
              <p className="px-2 py-6 text-center text-sm text-muted-foreground">
                No documents here yet.
              </p>
            ) : (
              visible.map((d) => (
                <button
                  key={`${d.access}/${d.name}`}
                  onClick={() => setSelected(d)}
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

        {selected ? (
          <DocEditor
            key={`${selected.access}/${selected.name}`}
            doc={selected}
            onSaved={() => refresh()}
            onDeleted={() => {
              setSelected(null)
              refresh()
            }}
          />
        ) : (
          <Card className="flex items-center justify-center p-10 text-center text-sm text-muted-foreground">
            Pick a document on the left to view or edit it.
          </Card>
        )}
      </div>
    </div>
  )
}
