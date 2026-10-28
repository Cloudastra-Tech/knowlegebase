import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Download, LifeBuoy, Package, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { Panel } from "@/components/glass/panel"
import { QuestionLogSettings } from "@/components/question-log-settings"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  api,
  saveFile,
  workspace as workspaceApi,
  type WorkspaceExport,
  type WorkspaceSettings,
} from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn, timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const when = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })

const CHAT_KEEP = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "forever", label: "Until people delete them" },
]

const years = (days: number) => `${Math.round(days / 365)} year${days >= 730 ? "s" : ""}`

// The danger zone keeps the destructive token on its edge and title, and nothing on it
// moves.
function DataPanel({
  title,
  description,
  children,
  danger = false,
}: {
  title: string
  description: string
  children: React.ReactNode
  danger?: boolean
}) {
  return (
    <Panel
      title={danger ? <span className="text-destructive">{title}</span> : title}
      description={description}
      className={danger ? "border border-destructive/40" : undefined}
    >
      {children}
    </Panel>
  )
}

// ---- Retention: chats, the audit log (the question log has its own panel below) ------
function Retention({ settings }: { settings: WorkspaceSettings }) {
  const queryClient = useQueryClient()
  const chat = useQuery(queries.chatSettings()).data
  const saveChat = useMutation({
    mutationFn: (keep: string) =>
      api.setChatSettings(keep === "forever" ? null : Number(keep), chat?.sharing_enabled ?? true),
    onSuccess: (saved) => {
      queryClient.setQueryData(["chat-settings"], saved)
      toast.success("Chat retention saved.")
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const saveAudit = useMutation({
    mutationFn: (keep: string) =>
      workspaceApi.setAuditRetention(keep === "forever" ? null : Number(keep)),
    onSuccess: (saved) => {
      queryClient.setQueryData(queries.workspace().queryKey, saved)
      toast.success("Audit log retention saved.")
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const locked = settings.support_session

  return (
    <DataPanel
      title="Retention"
      description="How long this workspace keeps what people do here. Nightly jobs delete what's older."
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field>
          <FieldLabel htmlFor="keep-chats">Chats</FieldLabel>
          {chat ? (
            <Select
              value={String(chat.retention_days ?? "forever")}
              onValueChange={(v) => saveChat.mutate(v)}
              disabled={saveChat.isPending}
            >
              <SelectTrigger id="keep-chats" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CHAT_KEEP.map((k) => (
                  <SelectItem key={k.value} value={k.value}>
                    {k.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Skeleton className="h-8 w-full" />
          )}
          <FieldDescription>
            Counted from a chat's last use, with its feedback and share links.
          </FieldDescription>
        </Field>
        <Field>
          <FieldLabel htmlFor="keep-audit">Audit log</FieldLabel>
          <Select
            value={String(settings.audit_days ?? "forever")}
            onValueChange={(v) => saveAudit.mutate(v)}
            disabled={saveAudit.isPending || locked}
          >
            <SelectTrigger id="keep-audit" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="forever">For good (recommended)</SelectItem>
              {settings.audit_day_choices.map((d) => (
                <SelectItem key={d} value={String(d)}>
                  {years(d)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <FieldDescription>
            At least a year. The hash chain still checks out after old events go.
          </FieldDescription>
        </Field>
      </div>
    </DataPanel>
  )
}

// ---- Export ------------------------------------------------------------------------
function exportNote(e: WorkspaceExport) {
  if (e.status === "queued" || e.status === "running") return "Being made…"
  if (e.status === "failed") return `Failed${e.error ? ` (${e.error})` : ""}`
  if (e.status === "expired") return "Expired"
  const size = e.size ?? 0
  const shown = size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${shown} · until ${when(e.expires_at ?? 0)}`
}

function ExportPanel({ settings }: { settings: WorkspaceSettings }) {
  const queryClient = useQueryClient()
  const start = useMutation({
    mutationFn: workspaceApi.startExport,
    onSuccess: () => {
      toast.success("Making the export. It's ready here in a moment.")
      void queryClient.invalidateQueries({ queryKey: queries.workspace().queryKey })
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const busy = settings.exports.some((e) => e.status === "queued" || e.status === "running")

  async function download(id: string) {
    try {
      saveFile(await workspaceApi.downloadExport(id))
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <DataPanel
      title="Export"
      description={`A ZIP of members, the audit log, uploaded documents, chats and settings (never secrets such as tokens or keys). Downloadable for ${settings.export_hours} hours.`}
    >
      <Button
        variant="outline"
        onClick={() => start.mutate()}
        loading={busy || start.isPending}
        disabled={settings.support_session}
      >
        {!(busy || start.isPending) && <Package data-icon="inline-start" />}
        Export workspace
      </Button>
      {settings.exports.length > 0 && (
        <div className="mt-4 divide-y rounded-xl border bg-card">
          {settings.exports.map((e) => (
            <div key={e.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
              <div className="min-w-0 flex-1 basis-48">
                <p className="truncate text-sm font-medium">
                  <span className="font-mono tabular-nums">{timeAgo(e.created_at)}</span> ·{" "}
                  {e.requested_by}
                </p>
                <p
                  className={cn(
                    "flex items-center gap-1.5 truncate text-xs text-muted-foreground",
                    e.status === "failed" && "text-destructive",
                  )}
                >
                  {(e.status === "queued" || e.status === "running") && (
                    <AsciiSpinner size="xs" label="" className="text-signal" />
                  )}
                  {exportNote(e)}
                </p>
              </div>
              {e.status === "done" && (
                <Button size="sm" variant="ghost" onClick={() => download(e.id)}>
                  <Download data-icon="inline-start" /> Download
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </DataPanel>
  )
}

// ---- Cloudastra support ------------------------------------------------------------
function SupportPanel({ settings }: { settings: WorkspaceSettings }) {
  const queryClient = useQueryClient()
  const [hours, setHours] = useState(String(settings.support_hours[1] ?? 72))
  const done = (saved: WorkspaceSettings, message: string) => {
    queryClient.setQueryData(queries.workspace().queryKey, saved)
    toast.success(message)
  }
  const grant = useMutation({
    mutationFn: () => workspaceApi.grantSupport(Number(hours)),
    onSuccess: (saved) => done(saved, "Support can open the workspace now."),
    onError: (err) => toast.error(errorText(err)),
  })
  const revoke = useMutation({
    mutationFn: workspaceApi.revokeSupport,
    onSuccess: (saved) => done(saved, "Support's access has ended."),
    onError: (err) => toast.error(errorText(err)),
  })

  return (
    <DataPanel
      title="Cloudastra support"
      description="Let support open this workspace as an admin for a while, to help with a problem. Everything they do is in the audit log, and they can't delete the workspace or anyone's data."
    >
      {settings.support_until ? (
        <div className="flex flex-wrap items-center gap-3">
          <Badge variant="signal">
            <LifeBuoy /> Allowed until {when(settings.support_until)}
          </Badge>
          <span className="text-xs text-muted-foreground">by {settings.support_granted_by}</span>
          <Button
            variant="outline"
            size="sm"
            className="sm:ml-auto"
            onClick={() => revoke.mutate()}
            loading={revoke.isPending}
          >
            End access now
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <Field className="sm:w-48">
            <FieldLabel htmlFor="support-hours">For</FieldLabel>
            <Select value={hours} onValueChange={setHours} disabled={settings.support_session}>
              <SelectTrigger id="support-hours" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {settings.support_hours.map((h) => (
                  <SelectItem key={h} value={String(h)}>
                    {h < 48 ? `${h} hours` : `${h / 24} days`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Button
            variant="outline"
            onClick={() => grant.mutate()}
            loading={grant.isPending}
            disabled={settings.support_session}
          >
            {!grant.isPending && <LifeBuoy data-icon="inline-start" />} Allow support
          </Button>
        </div>
      )}
    </DataPanel>
  )
}

// ---- Danger zone: delete the workspace, after a grace period ------------------------
function DangerZone({ settings }: { settings: WorkspaceSettings }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState("")
  const done = (saved: WorkspaceSettings, message: string) => {
    queryClient.setQueryData(queries.workspace().queryKey, saved)
    toast.success(message)
  }
  const remove = useMutation({
    mutationFn: () => workspaceApi.scheduleDeletion(typed.trim()),
    onSuccess: (saved) => {
      setOpen(false)
      done(saved, `${saved.name} will be deleted on ${when(saved.deleting_at ?? 0)}.`)
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const cancel = useMutation({
    mutationFn: workspaceApi.cancelDeletion,
    onSuccess: (saved) => done(saved, "The deletion is cancelled. Everyone can sign in again."),
    onError: (err) => toast.error(errorText(err)),
  })
  const matches = typed.trim() === settings.name

  if (settings.status === "deleting" && settings.deleting_at) {
    return (
      <DataPanel
        danger
        title="This workspace is being deleted"
        description={`On ${when(settings.deleting_at)} its documents, chats, members, settings and connections are deleted for good, and its keys destroyed. Until then only admins can sign in. ${settings.delete_requested_by ? `Asked by ${settings.delete_requested_by}.` : ""}`}
      >
        <Button onClick={() => cancel.mutate()} loading={cancel.isPending}>
          Cancel the deletion
        </Button>
      </DataPanel>
    )
  }

  return (
    <DataPanel
      danger
      title="Danger zone"
      description={`Delete this workspace and everything in it. People are signed out now; it's purged ${settings.grace_days} days later, and until then an admin can still cancel. Export it first if you need a copy.`}
    >
      <Button
        variant="destructive"
        onClick={() => {
          setTyped("")
          setOpen(true)
        }}
        disabled={!settings.can_delete || settings.support_session}
      >
        <TriangleAlert data-icon="inline-start" /> Delete workspace
      </Button>
      {!settings.can_delete && (
        <p className="mt-2 text-xs text-muted-foreground">
          The server's default workspace can't be deleted.
        </p>
      )}
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {settings.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Everyone but admins is signed out now. In {settings.grace_days} days every
              document, chat, member, connection and setting is deleted, connected apps are
              disconnected, and the workspace's encryption keys are destroyed. The admins are
              emailed, and can cancel until then.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Field>
            <FieldLabel htmlFor="confirm-delete">
              Type <span className="font-semibold">{settings.name}</span> to confirm
            </FieldLabel>
            <Input
              id="confirm-delete"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
            />
          </Field>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={!matches || remove.isPending}
              onClick={(e) => {
                e.preventDefault()
                remove.mutate()
              }}
            >
              Delete in {settings.grace_days} days
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </DataPanel>
  )
}

// The Access screen's "Data" tab (admins): retention for chats, questions and the audit
// log in one place, the workspace's export, support access, and deleting it.
export function WorkspaceDataView() {
  const settings = useQuery({
    ...queries.workspace(),
    // Poll while an export is being made.
    refetchInterval: (q) =>
      q.state.data?.exports.some((e) => e.status === "queued" || e.status === "running")
        ? 2000
        : false,
  }).data
  const logSettings = useQuery(queries.logSettings()).data

  if (!settings) return <Skeleton className="h-48 rounded-2xl" />
  const deleting = settings.status === "deleting"

  return (
    <div className="flex flex-col gap-6">
      {deleting && <DangerZone settings={settings} />}
      {!deleting && <Retention settings={settings} />}
      {!deleting && logSettings && (
        <QuestionLogSettings
          key={`${logSettings.mode}-${logSettings.days}`}
          settings={logSettings}
          canEdit
        />
      )}
      <ExportPanel settings={settings} />
      {!deleting && <SupportPanel settings={settings} />}
      {!deleting && <DangerZone settings={settings} />}
    </div>
  )
}
