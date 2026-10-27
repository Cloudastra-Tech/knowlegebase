import { useCallback, useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Archive,
  Cloud,
  FileText,
  FolderCog,
  Globe,
  History,
  Loader2,
  Lock,
  Plug,
  RefreshCw,
  TriangleAlert,
  Unplug,
  Users,
} from "lucide-react"
import {
  siAsana,
  siBox,
  siClickup,
  siCoda,
  siConfluence,
  siDropbox,
  siGithub,
  siGmail,
  siGooglecalendar,
  siGoogledrive,
  siHubspot,
  siIntercom,
  siJira,
  siLinear,
  siNotion,
  siOutline,
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { ContainerIcon, ContainerPicker } from "@/components/container-picker"
import { IntegrationConnectDialog } from "@/components/integration-connect-dialog"
import { S3ConnectDialog } from "@/components/s3-connect-dialog"
import { SalesforceConnectDialog } from "@/components/salesforce-connect-dialog"
import { SlackAppCard } from "@/components/slack-app-card"
import { SurfaceCards } from "@/components/surface-cards"
import { SyncHistorySheet } from "@/components/sync-history-sheet"
import { MEMBER_OPTIONS, TICKET_OPTIONS } from "@/lib/ticket-options"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type Integration, type IntegrationStatus, type PermissionPolicy } from "@/lib/api"
import { useIndexStatus } from "@/lib/index-status-context"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// Brand marks by the registry's simple-icons slug (connectors/registry.py).
const BRANDS: Record<string, SimpleIcon> = {
  googledrive: siGoogledrive,
  notion: siNotion,
  outline: siOutline,
  confluence: siConfluence,
  jira: siJira,
  github: siGithub,
  dropbox: siDropbox,
  zendesk: siZendesk,
  linear: siLinear,
  box: siBox,
  intercom: siIntercom,
  hubspot: siHubspot,
  asana: siAsana,
  clickup: siClickup,
  coda: siCoda,
  gmail: siGmail,
  googlecalendar: siGooglecalendar,
}
// What a connection with an optional picker syncs while nothing is picked.
const UNPICKED: Record<string, string> = {
  zendesk: "Every published Help Center article is synced.",
  intercom: "Every published Help Center article is synced.",
  freshdesk: "Every published knowledge base article is synced.",
  hubspot: "Every published knowledge base article is synced.",
  salesforce: "Every published Knowledge article is synced.",
  s3: "Every readable file in the bucket (under its prefix) is synced.",
}
const LETTERS: Record<string, string> = {
  sharepoint: "S",
  slack: "#",
  freshdesk: "F",
  teams_messages: "T",
}

export function ConnectorIcon({ item }: { item: Integration }) {
  const brand = item.icon ? BRANDS[item.icon] : undefined
  if (brand)
    return (
      <svg viewBox="0 0 24 24" className="size-5 fill-current" aria-hidden>
        <path d={brand.path} />
      </svg>
    )
  if (item.id === "uploads") return <FileText className="size-5" />
  if (item.id === "website") return <Globe className="size-5" />
  if (item.id === "s3") return <Archive className="size-5" />
  if (item.id === "salesforce") return <Cloud className="size-5" />
  if (LETTERS[item.id]) return <span className="text-xs font-semibold">{LETTERS[item.id]}</span>
  return <Plug className="size-5" />
}

const STATUS: Record<IntegrationStatus, { label: string; tone: "on" | "busy" | "bad" | "off" }> = {
  connected: { label: "Connected", tone: "on" },
  syncing: { label: "Syncing", tone: "busy" },
  error: { label: "Needs attention", tone: "bad" },
  disconnected: { label: "Not connected", tone: "off" },
  not_configured: { label: "Not set up", tone: "off" },
  unavailable: { label: "Not available", tone: "off" },
}

export function StatusBadge({ status }: { status: IntegrationStatus }) {
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

export function Card({ item, children }: { item: Integration; children?: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted">
          <ConnectorIcon item={item} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium">{item.name}</p>
          <p className="text-sm text-muted-foreground">{item.description}</p>
        </div>
        <StatusBadge status={item.status} />
      </div>
      {children && <div className="mt-4 flex flex-1 flex-col">{children}</div>}
    </div>
  )
}

export function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="truncate text-sm font-medium">{value}</p>
    </div>
  )
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

// Who may ask about a connection's synced files: its PERMISSION POLICY. Shared by
// every connector card. Opening them to everyone asks for confirmation; the others
// don't (they can only narrow it, or follow the source). Changing it re-tags the
// index: nothing is embedded again.
function PolicyControl({ item, onChange }: { item: Integration; onChange: () => void }) {
  const policy: PermissionPolicy =
    item.policy ?? (item.access === "everyone" ? "workspace" : "restricted")
  const offered = item.policies ?? ["workspace", "restricted"]
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const { refresh: refreshIndex } = useIndexStatus()
  const ids = `${item.id}-access`

  async function save(next: PermissionPolicy) {
    setSaving(true)
    try {
      await api.setIntegrationPolicy(item.id, next)
      toast.success(
        next === "workspace"
          ? `Everyone can now ask about ${item.name} files. Updating the index…`
          : next === "source"
            ? `${item.name} files now follow their sharing in ${item.name}. Updating the index…`
            : `Only HR and admins can now ask about ${item.name} files. Updating the index…`,
      )
      onChange()
      refreshIndex()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  async function syncPermissions() {
    setSyncing(true)
    try {
      await api.syncIntegrationPermissions(item.id)
      toast.success(`Reading who can see each ${item.name} item again…`)
      onChange()
      refreshIndex()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="mt-3 rounded-xl border border-border/70 p-3">
      <p className="text-sm font-medium" id={`${ids}-label`}>
        Who can ask about these files
      </p>
      <RadioGroup
        aria-labelledby={`${ids}-label`}
        value={policy}
        onValueChange={(v) => (v === "workspace" ? setConfirming(true) : save(v as PermissionPolicy))}
        disabled={saving || !!item.access_locked}
        className="mt-2 gap-2"
      >
        {offered.includes("source") && (
          <div className="flex items-start gap-2">
            <RadioGroupItem value="source" id={`${ids}-source`} className="mt-0.5" />
            <Label htmlFor={`${ids}-source`} className="flex-col items-start gap-0.5 font-normal">
              <span className="flex items-center gap-1.5">
                <Users className="size-3.5 text-muted-foreground" /> As shared in {item.name}
              </span>
              <span className="text-xs text-muted-foreground">
                Each file only for the people and groups it's shared with there, matched to
                members, identity-provider groups and verified domains here.
              </span>
            </Label>
          </div>
        )}
        <div className="flex items-center gap-2">
          <RadioGroupItem value="restricted" id={`${ids}-restricted`} />
          <Label htmlFor={`${ids}-restricted`} className="font-normal">
            <Lock className="size-3.5 text-muted-foreground" /> HR and admins
          </Label>
        </div>
        <div className="flex items-center gap-2">
          <RadioGroupItem value="workspace" id={`${ids}-everyone`} />
          <Label htmlFor={`${ids}-everyone`} className="font-normal">
            <Globe className="size-3.5 text-muted-foreground" /> Everyone in the workspace
          </Label>
        </div>
      </RadioGroup>
      {!offered.includes("source") && item.source_policy_note && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
          <Users className="mt-px size-3.5 shrink-0" />
          {item.source_policy_note}
        </p>
      )}
      {item.access_locked && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
          <Lock className="mt-px size-3.5 shrink-0" />
          {MEMBER_OPTIONS[item.id] ? (
            MEMBER_OPTIONS[item.id].locked
          ) : (
            <>
              Locked to HR and admins while {item.name}{" "}
              {(TICKET_OPTIONS[item.id]?.label ?? "tickets").toLowerCase()} are synced: they
              hold customers' details.
            </>
          )}
        </p>
      )}
      {policy === "workspace" && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
          <TriangleAlert className="mt-px size-3.5 shrink-0" />
          Everyone in the workspace can ask about these files, whatever their sharing is in{" "}
          {item.name}.
        </p>
      )}
      {policy === "source" && (
        <div className="mt-2 flex flex-col gap-2 text-xs text-muted-foreground">
          <p className="flex items-start gap-1.5">
            <History className="mt-px size-3.5 shrink-0" />
            Sharing is read at every sync, so a change in {item.name} reaches answers within
            about 30 minutes. Anyone or any group that matches no one here is left out, never
            let in. The Knowledge page shows who can see each file.
          </p>
          <Button
            size="sm"
            variant="outline"
            className="self-start text-foreground"
            onClick={syncPermissions}
            disabled={syncing || item.status === "syncing"}
          >
            {syncing ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Sync permissions now
          </Button>
        </div>
      )}
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Let everyone ask about {item.name} files?</AlertDialogTitle>
            <AlertDialogDescription>
              Everyone in the workspace will be able to ask about these files, and answers can
              quote them, whoever they're shared with in {item.name}. Only do this if nothing synced
              here is confidential.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it as it is</AlertDialogCancel>
            <AlertDialogAction onClick={() => save("workspace")}>Let everyone ask</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// One connector, whatever it is: drawn from its registry entry and connection state.
function ConnectorCard({
  item,
  onChange,
  autoOpenPicker,
}: {
  item: Integration
  onChange: () => void
  autoOpenPicker?: boolean
}) {
  const [busy, setBusy] = useState<"connect" | "sync" | "disconnect" | "confirm" | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const picks = item.containers ?? []
  const unit = item.unit ?? "items"
  const hasPicker = !!item.picker && item.picker !== "none"
  const tickets = TICKET_OPTIONS[item.id]
  const members = MEMBER_OPTIONS[item.id]
  const nothingPicked = !!item.picker_required && picks.length === 0

  // Just connected and nothing picked yet: open the picker (once per arrival).
  const shouldAutoOpen = !!autoOpenPicker && nothingPicked
  const [autoOpened, setAutoOpened] = useState(false)
  if (shouldAutoOpen !== autoOpened) {
    setAutoOpened(shouldAutoOpen)
    if (shouldAutoOpen) setPickerOpen(true)
  }

  async function connect() {
    if (item.auth !== "oauth") {
      setFormOpen(true)
      return
    }
    setBusy("connect")
    try {
      const { url } = await api.connectIntegration(item.id)
      window.location.href = url // off to the provider's consent screen
    } catch (err) {
      toast.error(errorText(err))
      setBusy(null)
    }
  }

  async function sync(opts: { full?: boolean; confirm_deletes?: boolean } = {}) {
    setBusy(opts.confirm_deletes ? "confirm" : "sync")
    try {
      await api.syncIntegration(item.id, opts)
      toast.success(opts.confirm_deletes ? "Removing them…" : `Syncing ${item.name}…`)
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
      await api.disconnectIntegration(item.id)
      toast.success(`${item.name} disconnected. Its ${unit} were removed from search.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  const connected = ["connected", "syncing", "error"].includes(item.status)
  const reconnect = item.error?.code === "auth_expired" || item.error?.code === "missing_scope"
  const fixSettings = item.error?.code === "config" || item.error?.code === "not_found"
  const limit = item.last_run?.limit_reached ?? 0

  return (
    <Card item={item}>
      {item.status === "not_configured" && (
        <p className="text-sm text-muted-foreground">
          The server needs an OAuth app before {item.name} can be connected
          {item.setup_env ? ` (${item.setup_env})` : ""}. Ask your admin to set it up.
        </p>
      )}

      {item.status === "disconnected" && (
        <>
          <p className="text-sm text-muted-foreground">
            {item.auth === "url"
              ? "Crawls public pages of a site you name, follows its links on the same site and keeps them up to date. Answers link back to each page."
              : item.picker_required
                ? `Read-only. After connecting, you choose what to sync; nothing else is touched. Answers link back to ${item.name}.`
                : `Read-only. Answers cite each page with a link back to ${item.name}.`}
          </p>
          {item.note && <p className="mt-2 text-sm text-muted-foreground">{item.note}</p>}
          {item.live_search && (
            <p className="mt-2 text-sm text-muted-foreground">
              Until then, this workspace searches the server's own wiki live.
            </p>
          )}
          <Button className="mt-4 w-fit" onClick={connect} disabled={busy !== null}>
            {busy === "connect" && <Loader2 className="animate-spin" />}
            {item.auth === "url" ? "Add a website" : `Connect ${item.name}`}
          </Button>
        </>
      )}

      {connected && (
        <>
          {item.error && item.status === "error" && (
            <Alert variant="destructive" className="mb-3">
              <TriangleAlert />
              <AlertTitle>The last sync didn't finish</AlertTitle>
              <AlertDescription>{item.error.message}</AlertDescription>
            </Alert>
          )}
          {(item.held_deletes ?? 0) > 0 && (
            <Alert className="mb-3">
              <TriangleAlert />
              <AlertTitle>
                {item.held_deletes} {unit} would be removed
              </AlertTitle>
              <AlertDescription>
                <p>
                  That's a large share of what this connection syncs, so nothing was removed. If
                  they were really deleted or unshared, remove them; otherwise check the
                  connection.
                </p>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button size="sm" variant="outline" className="w-fit" disabled={busy !== null}>
                      {busy === "confirm" && <Loader2 className="animate-spin" />}
                      Remove them
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>
                        Remove {item.held_deletes} {unit} from search?
                      </AlertDialogTitle>
                      <AlertDialogDescription>
                        They are no longer in {item.name}, or no longer shared with this
                        connection. Their copies and search entries are deleted here; nothing
                        changes in {item.name}.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Keep them for now</AlertDialogCancel>
                      <AlertDialogAction onClick={() => sync({ confirm_deletes: true })}>
                        Remove them
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </AlertDescription>
            </Alert>
          )}

          {hasPicker &&
            (picks.length === 0 ? (
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed p-3">
                <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                  {item.picker_required
                    ? "Nothing is synced yet. Choose what your team should search."
                    : (UNPICKED[item.id] ?? "Everything shared with the integration is synced.")}
                </p>
                <Button size="sm" onClick={() => setPickerOpen(true)}>
                  <FolderCog /> {item.picker_required ? "Choose what to sync" : "Narrow it down"}
                </Button>
              </div>
            ) : (
              <div className="mb-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted-foreground">Syncing</p>
                  <Button
                    variant="link"
                    size="xs"
                    className="h-auto px-0"
                    onClick={() => setPickerOpen(true)}
                  >
                    Edit selection
                  </Button>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {picks.map((f) => (
                    <span
                      key={f.id}
                      className="flex max-w-full items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-xs sm:max-w-60"
                    >
                      <ContainerIcon item={f} className="size-3" />
                      <span className="truncate">{f.name}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))}

          {tickets && item.options?.[tickets.key] !== undefined && (
            <div className="mb-3 flex items-center justify-between gap-3 text-sm">
              <p className="min-w-0 text-muted-foreground">
                {tickets.label}:{" "}
                <span className="font-medium text-foreground">
                  {item.options[tickets.key] ? "synced (HR and admins only)" : "off"}
                </span>
              </p>
              <Button
                variant="link"
                size="xs"
                className="h-auto shrink-0 px-0"
                onClick={() => setPickerOpen(true)}
              >
                Change
              </Button>
            </div>
          )}

          {members && item.options?.members !== undefined && (
            <div className="mb-3 flex items-center justify-between gap-3 text-sm">
              <p className="min-w-0 text-muted-foreground">
                {members.label}:{" "}
                <span className="font-medium text-foreground">
                  {item.options.members ? "yes" : "no (HR and admins only)"}
                </span>
              </p>
              <Button
                variant="link"
                size="xs"
                className="h-auto shrink-0 px-0"
                onClick={() => setPickerOpen(true)}
              >
                Change
              </Button>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3 rounded-xl bg-muted/50 p-3">
            <Stat label="Account" value={item.account || "—"} />
            <Stat
              label={capitalize(unit)}
              value={
                item.skipped_count
                  ? `${item.doc_count ?? 0} (+${item.skipped_count} skipped)`
                  : (item.doc_count ?? 0)
              }
            />
            <Stat label="Last sync" value={item.status === "syncing" ? "now" : timeAgo(item.last_sync_at)} />
          </div>
          {limit > 0 && (
            <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
              <TriangleAlert className="mt-px size-3.5 shrink-0" />
              The last sync stopped at {limit} {unit}, the limit for one connection. Pick less,
              or ask your admin to raise the limit.
            </p>
          )}
          <PolicyControl item={item} onChange={onChange} />

          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => sync()}
              disabled={busy !== null || item.status === "syncing" || nothingPicked}
            >
              {busy === "sync" || item.status === "syncing" ? (
                <Loader2 className="animate-spin" />
              ) : (
                <RefreshCw />
              )}
              Sync now
            </Button>
            <Button size="sm" variant="outline" onClick={() => setHistoryOpen(true)}>
              <History /> History
            </Button>
            {reconnect && (
              <Button size="sm" onClick={connect} disabled={busy !== null}>
                Reconnect
              </Button>
            )}
            {fixSettings && hasPicker && (
              <Button size="sm" onClick={() => setPickerOpen(true)}>
                Check what it syncs
              </Button>
            )}
            {item.auth === "oauth" && item.id === "notion" && !reconnect && (
              <Button size="sm" variant="outline" onClick={connect} disabled={busy !== null}>
                Share more pages
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
                  <AlertDialogTitle>Disconnect {item.name}?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Every synced {item.name} item is deleted from this workspace and its search
                    index. Nothing changes in {item.name} itself.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={disconnect}>Disconnect</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
          {hasPicker && (
            <ContainerPicker
              item={item}
              open={pickerOpen}
              onOpenChange={setPickerOpen}
              onSaved={onChange}
            />
          )}
          <SyncHistorySheet item={item} open={historyOpen} onOpenChange={setHistoryOpen} />
        </>
      )}

      {item.id === "salesforce" ? (
        <SalesforceConnectDialog item={item} open={formOpen} onOpenChange={setFormOpen} />
      ) : item.id === "s3" ? (
        <S3ConnectDialog
          item={item}
          open={formOpen}
          onOpenChange={setFormOpen}
          onConnected={(choose) => {
            onChange()
            if (choose) setPickerOpen(true)
          }}
        />
      ) : (
        item.auth &&
        item.auth !== "oauth" && (
          <IntegrationConnectDialog
            item={item}
            open={formOpen}
            onOpenChange={setFormOpen}
            onConnected={(choose) => {
              onChange()
              if (choose) setPickerOpen(true)
            }}
          />
        )
      )}
    </Card>
  )
}

export function IntegrationsView({
  onOpenDocuments,
  openPickerFor,
}: {
  onOpenDocuments: () => void
  openPickerFor?: string | null // just connected: open this connector's picker
}) {
  const queryClient = useQueryClient()
  // While a connection syncs, refresh every few seconds (and nudge the index bar).
  const { data, dataUpdatedAt } = useQuery({
    ...queries.integrations(),
    refetchInterval: (query) =>
      query.state.data?.integrations.some((i) => i.status === "syncing") ? 3000 : false,
  })
  const items = data?.integrations ?? null
  const workspace = data?.workspace ?? ""
  const { refresh: refreshIndex } = useIndexStatus()

  const load = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queries.integrations().queryKey })
  }, [queryClient])

  const syncing = items?.some((i) => i.status === "syncing")
  useEffect(() => {
    if (syncing) refreshIndex()
  }, [syncing, dataUpdatedAt, refreshIndex])

  const live = (items ?? []).filter((i) => i.availability === "live" && i.id !== "uploads")
  const uploads = items?.find((i) => i.id === "uploads")
  const soon = (items ?? []).filter((i) => i.availability === "soon")

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
            {live.map((item) => (
              <ConnectorCard
                key={item.id}
                item={item}
                onChange={load}
                autoOpenPicker={openPickerFor === item.id}
              />
            ))}

            {uploads && (
              <Card item={uploads}>
                <div className="grid grid-cols-3 gap-3 rounded-xl bg-muted/50 p-3">
                  <Stat label="Files" value={uploads.doc_count ?? 0} />
                </div>
                <Button size="sm" variant="outline" className="mt-4 w-fit" onClick={onOpenDocuments}>
                  Manage documents
                </Button>
              </Card>
            )}
          </div>

          <div className="mt-10">
            <p className="text-sm font-medium">Ask from your tools</p>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <SlackAppCard />
              <SurfaceCards />
            </div>
          </div>

          {soon.length > 0 && (
            <div className="mt-10">
              <p className="text-sm font-medium">Coming soon</p>
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {soon.map((s) => (
                  <div
                    key={s.id}
                    className="flex min-w-0 items-center gap-2.5 rounded-xl border border-dashed border-border px-3 py-2.5 text-sm text-muted-foreground"
                  >
                    <span className="flex size-6 shrink-0 items-center justify-center">
                      <ConnectorIcon item={s} />
                    </span>
                    <span className="truncate">{s.name}</span>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                Need something else? Most tools with an API can be connected, and any public site
                can be added as a website.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
