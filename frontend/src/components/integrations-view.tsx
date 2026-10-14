import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import { BookOpen, FileText, Folder, FolderCog, Loader2, RefreshCw, Unplug } from "lucide-react"
import {
  siConfluence,
  siDropbox,
  siGithub,
  siGoogledrive,
  siJira,
  siNotion,
  siZendesk,
  type SimpleIcon,
} from "simple-icons"

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
import { DriveFolderPicker } from "@/components/drive-folder-picker"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type Integration, type IntegrationStatus } from "@/lib/api"
import { useIndexStatus } from "@/lib/index-status"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

function BrandIcon({ icon }: { icon: SimpleIcon }) {
  return (
    <svg viewBox="0 0 24 24" className="size-5 fill-current" aria-hidden>
      <path d={icon.path} />
    </svg>
  )
}

function timeAgo(epoch?: number | null) {
  if (!epoch) return "never"
  const s = Math.max(0, Date.now() / 1000 - epoch)
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}

const STATUS: Record<IntegrationStatus, { label: string; tone: "on" | "busy" | "bad" | "off" }> = {
  connected: { label: "Connected", tone: "on" },
  syncing: { label: "Syncing", tone: "busy" },
  error: { label: "Needs attention", tone: "bad" },
  disconnected: { label: "Not connected", tone: "off" },
  not_configured: { label: "Not set up", tone: "off" },
  unavailable: { label: "Not available", tone: "off" },
}

function StatusBadge({ status }: { status: IntegrationStatus }) {
  const { label, tone } = STATUS[status]
  if (tone === "on")
    return (
      <Badge className="gap-1.5 bg-emerald-600/10 text-emerald-700 hover:bg-emerald-600/10 dark:text-emerald-400">
        <span className="size-1.5 rounded-full bg-emerald-500" /> {label}
      </Badge>
    )
  if (tone === "busy")
    return (
      <Badge variant="secondary" className="gap-1.5">
        <Loader2 className="size-3 animate-spin" /> {label}
      </Badge>
    )
  if (tone === "bad") return <Badge variant="destructive">{label}</Badge>
  return (
    <Badge variant="outline" className="font-normal text-muted-foreground">
      {label}
    </Badge>
  )
}

function Card({
  icon,
  title,
  subtitle,
  status,
  children,
}: {
  icon: React.ReactNode
  title: string
  subtitle: string
  status: IntegrationStatus
  children?: React.ReactNode
}) {
  return (
    <div className="flex flex-col rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted">
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium">{title}</p>
          <p className="text-sm text-muted-foreground">{subtitle}</p>
        </div>
        <StatusBadge status={status} />
      </div>
      {children && <div className="mt-4 flex flex-1 flex-col">{children}</div>}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="truncate text-sm font-medium">{value}</p>
    </div>
  )
}

function GoogleDriveCard({
  item,
  onChange,
  autoOpenPicker,
}: {
  item: Integration
  onChange: () => void
  autoOpenPicker?: boolean
}) {
  const [busy, setBusy] = useState<"connect" | "sync" | "disconnect" | null>(null)
  const folders = item.folders ?? []
  const [pickerOpen, setPickerOpen] = useState(false)
  useEffect(() => {
    if (autoOpenPicker && !folders.length) setPickerOpen(true)
  }, [autoOpenPicker, folders.length])

  async function connect() {
    setBusy("connect")
    try {
      const { url } = await api.connectDrive()
      window.location.href = url // off to Google's consent screen
    } catch (err) {
      toast.error(errorText(err))
      setBusy(null)
    }
  }

  async function sync() {
    setBusy("sync")
    try {
      await api.syncDrive()
      toast.success("Syncing Google Drive…")
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  async function disconnect() {
    setBusy("disconnect")
    try {
      await api.disconnectDrive()
      toast.success("Google Drive disconnected. Its files were removed from search.")
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  const connected = ["connected", "syncing", "error"].includes(item.status)

  return (
    <Card
      icon={<BrandIcon icon={siGoogledrive} />}
      title="Google Drive"
      subtitle="Docs, Sheets, PDFs and text files"
      status={item.status}
    >
      {item.status === "not_configured" && (
        <p className="text-sm text-muted-foreground">
          The server needs a Google OAuth client before Drive can be connected
          (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET). Ask your admin to set it up.
        </p>
      )}

      {item.status === "disconnected" && (
        <>
          <p className="text-sm text-muted-foreground">
            Read-only access. After connecting, you choose which folders to sync. Nothing else
            is touched. Synced files stay up to date every 30 minutes and are cited with a link
            back to Drive.
          </p>
          <Button className="mt-4 w-fit" onClick={connect} disabled={busy !== null}>
            {busy === "connect" && <Loader2 className="animate-spin" />}
            Connect Google Drive
          </Button>
        </>
      )}

      {connected && (
        <>
          {folders.length === 0 ? (
            <div className="mb-3 flex items-center justify-between gap-3 rounded-xl border border-dashed p-3">
              <p className="text-sm text-muted-foreground">
                Nothing is synced yet. Choose the folders your team should search.
              </p>
              <Button size="sm" onClick={() => setPickerOpen(true)}>
                <FolderCog /> Choose folders
              </Button>
            </div>
          ) : (
            <div className="mb-3">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">Syncing these folders</p>
                <button
                  onClick={() => setPickerOpen(true)}
                  className="text-xs font-medium underline-offset-4 hover:underline"
                >
                  Edit folders
                </button>
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {folders.map((f) => (
                  <span
                    key={f.id}
                    className="flex items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-xs"
                  >
                    <Folder className="size-3" /> {f.name}
                  </span>
                ))}
              </div>
            </div>
          )}
          <div className="grid grid-cols-3 gap-3 rounded-xl bg-muted/50 p-3">
            <Stat label="Account" value={item.account || "—"} />
            <Stat label="Files" value={item.doc_count ?? 0} />
            <Stat label="Last sync" value={item.status === "syncing" ? "now" : timeAgo(item.last_sync_at)} />
          </div>
          {item.status === "error" && item.last_error && (
            <p className="mt-3 text-sm text-destructive">{item.last_error}</p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={sync}
              disabled={busy !== null || item.status === "syncing" || !folders.length}
            >
              {busy === "sync" || item.status === "syncing" ? (
                <Loader2 className="animate-spin" />
              ) : (
                <RefreshCw />
              )}
              Sync now
            </Button>
            {item.status === "error" && (
              <Button size="sm" onClick={connect} disabled={busy !== null}>
                Reconnect
              </Button>
            )}
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" variant="ghost" className="text-destructive" disabled={busy !== null}>
                  <Unplug /> Disconnect
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Disconnect Google Drive?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Google access is revoked and every synced Drive file is deleted from this
                    workspace and its search index. Your files in Google Drive are not touched.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={disconnect}>Disconnect</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
          <DriveFolderPicker
            open={pickerOpen}
            onOpenChange={setPickerOpen}
            initial={folders}
            onSaved={onChange}
          />
        </>
      )}
    </Card>
  )
}

const SOON: { name: string; icon?: SimpleIcon; letter?: string }[] = [
  { name: "Notion", icon: siNotion },
  { name: "Confluence", icon: siConfluence },
  { name: "SharePoint", letter: "S" },
  { name: "Slack", letter: "#" },
  { name: "Jira", icon: siJira },
  { name: "GitHub", icon: siGithub },
  { name: "Dropbox", icon: siDropbox },
  { name: "Zendesk", icon: siZendesk },
]

export function IntegrationsView({
  onOpenDocuments,
  openDrivePicker,
}: {
  onOpenDocuments: () => void
  openDrivePicker?: boolean
}) {
  const [items, setItems] = useState<Integration[] | null>(null)
  const [workspace, setWorkspace] = useState("")
  const { refresh: refreshIndex } = useIndexStatus()

  const load = useCallback(async () => {
    try {
      const data = await api.integrations()
      setItems(data.integrations)
      setWorkspace(data.workspace)
    } catch (err) {
      toast.error(errorText(err))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // While Drive syncs, refresh every few seconds (and nudge the index bar).
  const syncing = items?.some((i) => i.status === "syncing")
  useEffect(() => {
    if (!syncing) return
    const t = window.setInterval(() => {
      load()
      refreshIndex()
    }, 3000)
    return () => window.clearInterval(t)
  }, [syncing, load, refreshIndex])

  const byId = Object.fromEntries((items ?? []).map((i) => [i.id, i])) as Record<
    Integration["id"],
    Integration | undefined
  >

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Integrations</h2>
        <p className="text-sm text-muted-foreground">
          Connect the tools your team already uses. Everything is private to the{" "}
          <span className="font-medium text-foreground">{workspace || "…"}</span> workspace.
        </p>
      </div>

      {!items ? (
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <Skeleton className="h-44 rounded-2xl" />
          <Skeleton className="h-44 rounded-2xl" />
        </div>
      ) : (
        <>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {byId.google_drive && (
              <GoogleDriveCard
                item={byId.google_drive}
                onChange={load}
                autoOpenPicker={openDrivePicker}
              />
            )}

            {byId.uploads && (
              <Card
                icon={<FileText className="size-5" />}
                title="Uploaded files"
                subtitle="PDF and text files you upload"
                status="connected"
              >
                <div className="grid grid-cols-3 gap-3 rounded-xl bg-muted/50 p-3">
                  <Stat label="Files" value={byId.uploads.doc_count ?? 0} />
                </div>
                <Button size="sm" variant="outline" className="mt-4 w-fit" onClick={onOpenDocuments}>
                  Manage documents
                </Button>
              </Card>
            )}

            {byId.outline && (
              <Card
                icon={<BookOpen className="size-5" />}
                title="Outline wiki"
                subtitle="Searched live on every question"
                status={byId.outline.status}
              >
                <p className="text-sm text-muted-foreground">
                  {byId.outline.status === "connected"
                    ? "Connected by the server admin. Answers link straight to the wiki page."
                    : "Not available for this workspace yet."}
                </p>
              </Card>
            )}
          </div>

          <div className="mt-10">
            <p className="text-sm font-medium">Coming soon</p>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {SOON.map((s) => (
                <div
                  key={s.name}
                  className="flex items-center gap-2.5 rounded-xl border border-dashed border-border px-3 py-2.5 text-sm text-muted-foreground"
                >
                  <span className="flex size-6 items-center justify-center">
                    {s.icon ? (
                      <BrandIcon icon={s.icon} />
                    ) : (
                      <span className="text-xs font-semibold">{s.letter}</span>
                    )}
                  </span>
                  {s.name}
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              Need something else? Most tools with an API can be connected.
            </p>
          </div>
        </>
      )}
    </div>
  )
}
