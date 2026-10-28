import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { FolderCog, Lock, MoreHorizontal, Plug, RefreshCw, TriangleAlert, Unplug } from "lucide-react"

import { ContainerIcon, ContainerPicker } from "@/components/container-picker"
import { ConnectorIcon, StatusBadge } from "@/components/integrations-view"
import {
  SettingsEmpty,
  SettingsError,
  SettingsList,
  SettingsSection,
  SettingsSkeleton,
} from "@/components/settings/settings-section"
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { api, type Integration } from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// Settings › Connections: a member's OWN Gmail and Google Calendar. What they sync is
// theirs alone: searched and cited only for them, never listed or counted for anyone
// else, admins included (kb/routers/my_integrations.py).

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// One connection, as a row: its mark, name and state, then (connected) what it syncs
// and how much, with Sync now, Choose what to sync and Disconnect in its menu.
function MyConnectionRow({
  item,
  onChange,
  autoOpenPicker,
}: {
  item: Integration
  onChange: () => void
  autoOpenPicker?: boolean
}) {
  const [busy, setBusy] = useState<"connect" | "sync" | "disconnect" | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const picks = item.containers ?? []
  const unit = item.unit ?? "items"

  // Just connected and nothing picked yet: open the picker (once per arrival).
  const shouldAutoOpen = !!autoOpenPicker && picks.length === 0
  const [autoOpened, setAutoOpened] = useState(false)
  if (shouldAutoOpen !== autoOpened) {
    setAutoOpened(shouldAutoOpen)
    if (shouldAutoOpen) setPickerOpen(true)
  }

  async function connect() {
    setBusy("connect")
    try {
      const { url } = await api.connectMyIntegration(item.id)
      window.location.href = url // off to Google's consent screen
    } catch (err) {
      toast.error(errorText(err))
      setBusy(null)
    }
  }

  async function sync() {
    setBusy("sync")
    try {
      await api.syncMyIntegration(item.id)
      toast.success(`Syncing your ${item.name}…`)
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
      await api.disconnectMyIntegration(item.id)
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
  const syncing = busy === "sync" || item.status === "syncing"

  return (
    <li className="flex flex-col gap-3 px-4 py-4">
      <div className="flex items-start gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted [&_svg]:size-4">
          <ConnectorIcon item={item} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{item.name}</p>
          <p className="text-sm text-pretty text-muted-foreground">{item.description}</p>
        </div>
        <StatusBadge status={item.status} />
        {connected && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="-my-1 shrink-0"
                aria-label={`${item.name}: actions`}
                disabled={busy !== null}
              >
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuGroup>
                <DropdownMenuItem onSelect={sync} disabled={syncing || picks.length === 0}>
                  <RefreshCw /> Sync now
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setPickerOpen(true)}>
                  <FolderCog /> Choose what to sync
                </DropdownMenuItem>
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setConfirm(true)}>
                <Unplug /> Disconnect
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <div className="flex flex-col gap-3 sm:pl-11">
        {item.status === "not_configured" && (
          <p className="text-sm text-muted-foreground">Not turned on for this server yet. Ask your admin.</p>
        )}

        {item.status === "disconnected" && (
          <div className="flex flex-wrap items-center justify-between gap-3">
            {item.note && <p className="min-w-0 flex-1 basis-56 text-sm text-pretty text-muted-foreground">{item.note}</p>}
            <Button size="sm" onClick={connect} loading={busy === "connect"} disabled={busy !== null}>
              Connect your {item.name}
            </Button>
          </div>
        )}

        {connected && (
          <>
            {item.error && item.status === "error" && (
              <Alert variant="destructive">
                <TriangleAlert />
                <AlertTitle>The last sync didn't finish</AlertTitle>
                <AlertDescription>
                  {item.error.message}
                  {reconnect && (
                    <Button size="sm" variant="outline" className="mt-2 w-fit" onClick={connect} disabled={busy !== null}>
                      Reconnect
                    </Button>
                  )}
                </AlertDescription>
              </Alert>
            )}

            {picks.length === 0 ? (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                  Nothing is synced yet. Choose what you want to ask about.
                </p>
                <Button size="sm" onClick={() => setPickerOpen(true)}>
                  Choose what to sync
                </Button>
              </div>
            ) : (
              <div>
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted-foreground">Syncing</p>
                  <Button variant="link" size="xs" className="h-auto px-0" onClick={() => setPickerOpen(true)}>
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
            )}

            <p className="text-xs text-muted-foreground tabular-nums">
              {[
                item.account,
                `${(item.doc_count ?? 0).toLocaleString()} ${unit}`,
                item.status === "syncing" ? "syncing now" : `synced ${timeAgo(item.last_sync_at)}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <Lock className="mt-px size-3.5 shrink-0" />
              Only you can ask about these {unit}. They never show up for anyone else here, admins
              included.
            </p>
            <ContainerPicker item={item} open={pickerOpen} onOpenChange={setPickerOpen} onSaved={onChange} />
            <AlertDialog open={confirm} onOpenChange={setConfirm}>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Disconnect your {item.name}?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Access is revoked at Google, and every synced {unit.replace(/s$/, "")} is
                    deleted from here and from search. Nothing changes in your {item.name}.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction variant="destructive" onClick={disconnect}>
                    Disconnect
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </>
        )}
      </div>
    </li>
  )
}

export function MyConnectionsView({ openPickerFor }: { openPickerFor?: string | null }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    ...queries.myIntegrations(),
    refetchInterval: (q) => (q.state.data?.integrations.some((i) => i.status === "syncing") ? 3000 : false),
  })
  const items = query.data?.integrations ?? null
  // None this server offers: nothing to connect, so no list of things that can't be.
  const none = !!items && items.every((i) => i.status === "not_configured" || i.status === "unavailable")
  const load = () => void queryClient.invalidateQueries({ queryKey: queries.myIntegrations().queryKey })

  return (
    <SettingsSection
      title="Connections"
      description="Ask about your own email and calendar. What you connect is yours alone: nobody else here can search it or read it in an answer."
    >
      {query.isPending ? (
        <SettingsSkeleton rows={2} label="Loading your connections" />
      ) : !items ? (
        <SettingsError what="your connections" onRetry={() => void query.refetch()} />
      ) : none ? (
        <SettingsEmpty
          icon={Plug}
          title="Nothing to connect yet"
          description="Connecting your own Gmail or Google Calendar isn't turned on for this workspace. Ask an admin if you'd like it."
        />
      ) : (
        <SettingsList label="Your connections">
          {items.map((item) => (
            <MyConnectionRow
              key={item.id}
              item={item}
              onChange={load}
              autoOpenPicker={openPickerFor === item.id}
            />
          ))}
        </SettingsList>
      )}
    </SettingsSection>
  )
}
