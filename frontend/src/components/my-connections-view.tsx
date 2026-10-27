import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { FolderCog, Lock, RefreshCw, TriangleAlert, Unplug } from "lucide-react"

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
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ContainerIcon, ContainerPicker } from "@/components/container-picker"
import { Card, Stat } from "@/components/integrations-view"
import { ScreenHeader } from "@/components/screen-header"
import { api, type Integration } from "@/lib/api"
import { type StaggerItem, useStaggerOnce } from "@/lib/motion"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// /connections: a member's OWN Gmail and Google Calendar. What they sync is theirs
// alone: searched and cited only for them, never listed or counted for anyone else,
// admins included (kb/routers/my_integrations.py).

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function MyConnectionCard({
  item,
  enter,
  onChange,
  autoOpenPicker,
}: {
  item: Integration
  enter: StaggerItem // the grid's first-draw stagger (useStaggerOnce)
  onChange: () => void
  autoOpenPicker?: boolean
}) {
  const [busy, setBusy] = useState<"connect" | "sync" | "disconnect" | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
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

  return (
    <Card item={item} enter={enter}>
      {item.status === "not_configured" && (
        <p className="text-sm text-muted-foreground">
          Not turned on for this server yet. Ask your admin.
        </p>
      )}

      {item.status === "disconnected" && (
        <>
          <p className="text-sm text-muted-foreground">{item.note}</p>
          <Button className="mt-4 w-fit" onClick={connect} loading={busy === "connect"} disabled={busy !== null}>
            Connect your {item.name}
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

          {picks.length === 0 ? (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed p-3">
              <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                Nothing is synced yet. Choose what you want to ask about.
              </p>
              <Button size="sm" onClick={() => setPickerOpen(true)}>
                <FolderCog /> Choose what to sync
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
          )}

          <div className="grid grid-cols-3 gap-3 rounded-xl bg-muted/50 p-3">
            <Stat label="Account" value={item.account || "—"} />
            <Stat label={capitalize(unit)} value={item.doc_count ?? 0} />
            <Stat
              label="Last sync"
              value={item.status === "syncing" ? "now" : timeAgo(item.last_sync_at)}
            />
          </div>
          <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
            <Lock className="mt-px size-3.5 shrink-0" />
            Only you can ask about these {unit}. They never show up for anyone else here,
            admins included.
          </p>

          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={sync}
              loading={busy === "sync" || item.status === "syncing"}
              disabled={busy !== null || picks.length === 0}
            >
              {busy !== "sync" && item.status !== "syncing" && <RefreshCw />}
              Sync now
            </Button>
            {reconnect && (
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
                  <AlertDialogTitle>Disconnect your {item.name}?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Access is revoked at Google, and every synced {unit.replace(/s$/, "")} is
                    deleted from here and from search. Nothing changes in your {item.name}.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={disconnect}>Disconnect</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
          <ContainerPicker
            item={item}
            open={pickerOpen}
            onOpenChange={setPickerOpen}
            onSaved={onChange}
          />
        </>
      )}
    </Card>
  )
}

export function MyConnectionsView({ openPickerFor }: { openPickerFor?: string | null }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    ...queries.myIntegrations(),
    refetchInterval: (query) =>
      query.state.data?.integrations.some((i) => i.status === "syncing") ? 3000 : false,
  })
  const items = data?.integrations ?? null
  const enter = useStaggerOnce(items !== null)
  const load = () =>
    void queryClient.invalidateQueries({ queryKey: queries.myIntegrations().queryKey })

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <ScreenHeader
        title="My connections"
        description="Ask about your own email and calendar. What you connect here is yours alone: nobody else in the workspace can search it, see it listed or read it in an answer."
      />
      {!items ? (
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <Skeleton className="h-44 rounded-2xl" />
          <Skeleton className="h-44 rounded-2xl" />
        </div>
      ) : items.length === 0 ? (
        <AsciiEmptyState
          art="plug"
          className="mt-6"
          title="Nothing to connect yet."
          description="Personal connections aren't turned on for this server. Ask your admin."
        />
      ) : (
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          {items.map((item, i) => (
            <MyConnectionCard
              key={item.id}
              item={item}
              enter={enter(i)}
              onChange={load}
              autoOpenPicker={openPickerFor === item.id}
            />
          ))}
        </div>
      )}
    </div>
  )
}
