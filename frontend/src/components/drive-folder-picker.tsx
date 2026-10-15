import { useEffect, useState } from "react"
import { toast } from "sonner"
import {
  ChevronRight,
  FileSpreadsheet,
  FileText,
  FileType,
  Folder,
  HardDrive,
  Loader2,
  X,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type DriveFolder } from "@/lib/api"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

export function DriveItemIcon({ item, className = "size-4" }: { item: DriveFolder; className?: string }) {
  const cls = `${className} shrink-0 text-muted-foreground`
  if (item.kind === "drive") return <HardDrive className={cls} />
  if (item.kind === "folder") return <Folder className={cls} />
  if (item.mimeType?.includes("spreadsheet")) return <FileSpreadsheet className={cls} />
  if (item.mimeType === "application/pdf") return <FileType className={cls} />
  return <FileText className={cls} />
}

// Pick what to sync from Google Drive: whole folders / Shared Drives (their
// sub-folders and new files included automatically) and/or single files.
export function DriveFolderPicker({
  open,
  onOpenChange,
  initial,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial: DriveFolder[]
  onSaved: () => void
}) {
  const [path, setPath] = useState<DriveFolder[]>([]) // breadcrumb; [] = top level
  // What's inside the folder being shown, tagged with that folder so a stale
  // listing never shows while the next one loads (null = loading).
  const [listing, setListing] = useState<{ parent?: string; items: DriveFolder[] } | null>(null)
  const [selected, setSelected] = useState<DriveFolder[]>(initial)
  const [saving, setSaving] = useState(false)

  // Each time the dialog opens: start from the saved picks, at the top level.
  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setSelected(initial)
      setPath([])
      setListing(null)
    }
  }

  const parent = path.at(-1)?.id
  const items = listing && listing.parent === parent ? listing.items : null
  useEffect(() => {
    if (!open) return
    let cancelled = false
    api
      .driveFolders(parent)
      .then(
        ({ drives, folders, files }) =>
          !cancelled && setListing({ parent, items: [...drives, ...folders, ...files] }),
      )
      .catch((err) => {
        toast.error(errorText(err))
        if (!cancelled) setListing({ parent, items: [] })
      })
    return () => {
      cancelled = true
    }
  }, [open, parent])

  const isPicked = (f: DriveFolder) => selected.some((s) => s.id === f.id)
  // Already covered because a folder above it in the breadcrumb is picked.
  const coveredByParent = path.some((p) => selected.some((s) => s.id === p.id))

  function toggle(f: DriveFolder) {
    setSelected((cur) => (isPicked(f) ? cur.filter((s) => s.id !== f.id) : [...cur, f]))
  }

  async function save() {
    setSaving(true)
    try {
      await api.setDriveFolders(selected)
      toast.success(
        selected.length
          ? `Syncing ${selected.length} item${selected.length === 1 ? "" : "s"}…`
          : "Nothing selected. Drive files were removed from search.",
      )
      onSaved()
      onOpenChange(false)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Choose what to sync</DialogTitle>
          <DialogDescription>
            Tick whole folders (sub-folders and new files included) or single files. Everyone
            in this workspace can search what you pick.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-1 text-sm">
          <button
            onClick={() => setPath([])}
            className="rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            My Drive
          </button>
          {path.map((p, i) => (
            <span key={p.id} className="flex items-center gap-1">
              <ChevronRight className="size-3.5 text-muted-foreground" />
              <button
                onClick={() => setPath(path.slice(0, i + 1))}
                className="max-w-40 truncate rounded px-1.5 py-0.5 hover:bg-muted"
              >
                {p.name}
              </button>
            </span>
          ))}
        </div>

        <div className="h-72 overflow-y-auto rounded-xl border">
          {items === null ? (
            <div className="space-y-2 p-3">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-2/3" />
            </div>
          ) : items.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              No folders or supported files here (Docs, Sheets, PDFs, text).
            </p>
          ) : (
            items.map((f) => (
              <div
                key={f.id}
                className="flex items-center gap-3 border-b px-3 py-2 last:border-b-0 hover:bg-muted/50"
              >
                <Checkbox
                  checked={coveredByParent || isPicked(f)}
                  disabled={coveredByParent}
                  onCheckedChange={() => toggle(f)}
                  aria-label={`Sync ${f.name}`}
                />
                <DriveItemIcon item={f} />
                {f.kind === "file" ? (
                  <button
                    onClick={() => !coveredByParent && toggle(f)}
                    className="min-w-0 flex-1 truncate text-left text-sm"
                  >
                    {f.name}
                  </button>
                ) : (
                  <button
                    onClick={() => setPath([...path, f])}
                    className="flex min-w-0 flex-1 items-center justify-between gap-2 text-left text-sm"
                    title="Open folder"
                  >
                    <span className="truncate">
                      {f.name}
                      {f.kind === "drive" && (
                        <span className="ml-1.5 text-xs text-muted-foreground">Shared Drive</span>
                      )}
                    </span>
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  </button>
                )}
              </div>
            ))
          )}
        </div>

        <div className="min-h-8">
          {selected.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing selected — nothing will be synced.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {selected.map((s) => (
                <span
                  key={s.id}
                  className="flex max-w-60 items-center gap-1 rounded-full bg-muted py-1 pl-2 pr-1 text-xs"
                >
                  <DriveItemIcon item={s} className="size-3" />
                  <span className="truncate">{s.name}</span>
                  <button
                    onClick={() => toggle(s)}
                    className="rounded-full p-0.5 hover:bg-background"
                    aria-label={`Remove ${s.name}`}
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving && <Loader2 className="animate-spin" />}
            Save and sync
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
