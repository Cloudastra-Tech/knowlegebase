import { useMemo, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  BellRing,
  FilePen,
  FilePlus,
  Mail,
  MoreHorizontal,
  Newspaper,
  Pencil,
  Plus,
  Send,
  Trash2,
} from "lucide-react"
import { toast } from "sonner"

import {
  SettingsEmpty,
  SettingsError,
  SettingsExample,
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
import { Badge } from "@/components/ui/badge"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  digests,
  type DigestChannel,
  type DigestFrequency,
  type DigestSettings,
  type DigestSubscription,
} from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// Settings › Digests: a person's own digests of what changed in the documents
// (platform.md PL-30). What one holds is worked out when it's sent, from what they can read then.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const FREQUENCY: Record<DigestFrequency, string> = { daily: "Daily", weekly: "Weekly" }
const CHANNEL: Record<DigestChannel, string> = { email: "Email", slack: "Slack DM" }

const when = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })

function useChange() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: queries.digests().queryKey })
}

function SourcePicker({
  settings,
  picked,
  onChange,
  idPrefix,
}: {
  settings: DigestSettings
  picked: string[]
  onChange: (next: string[]) => void
  idPrefix: string
}) {
  const toggle = (id: string, on: boolean) =>
    onChange(on ? [...picked, id] : picked.filter((p) => p !== id))
  return (
    <FieldSet>
      <FieldLegend variant="label">Sources</FieldLegend>
      <FieldDescription>None picked: everything you can read.</FieldDescription>
      <FieldGroup data-slot="checkbox-group" className="gap-3">
        {settings.sources.map((s) => (
          <Field key={s.id} orientation="horizontal">
            <Checkbox
              id={`${idPrefix}-${s.id}`}
              checked={picked.includes(s.id)}
              onCheckedChange={(on) => toggle(s.id, on === true)}
            />
            <FieldContent>
              <FieldLabel htmlFor={`${idPrefix}-${s.id}`} className="font-normal">
                {s.label}
              </FieldLabel>
            </FieldContent>
          </Field>
        ))}
      </FieldGroup>
    </FieldSet>
  )
}

// A folder a digest can follow, from the documents this person can read now (the
// Knowledge list): an upload folder, or a synced source's folder.
interface FolderOption {
  folder: string
  label: string
}

function folderLabel(provider: string, source: string, folder: string) {
  if (provider === "uploads") return folder === "restricted" ? "Uploads: limited files" : "Uploads: files for everyone"
  return `${source}: folder ${folder}`
}

// The folders of the sources picked (or of every source when none is).
function useFolderOptions(sources: string[]): FolderOption[] {
  const { data } = useQuery(queries.knowledge())
  return useMemo(() => {
    const out = new Map<string, FolderOption>()
    for (const item of data?.items ?? []) {
      if (!item.folder || item.source === "Sample") continue
      if (sources.length && !sources.includes(item.provider)) continue
      if (!out.has(item.folder))
        out.set(item.folder, { folder: item.folder, label: folderLabel(item.provider, item.source, item.folder) })
    }
    return [...out.values()].sort((a, b) => a.label.localeCompare(b.label))
  }, [data, sources])
}

function FolderPicker({
  sources,
  picked,
  onChange,
  idPrefix,
}: {
  sources: string[]
  picked: string[]
  onChange: (next: string[]) => void
  idPrefix: string
}) {
  const options = useFolderOptions(sources)
  // A folder picked before that isn't listed now (moved, or not readable) stays visible.
  const shown = [
    ...options,
    ...picked
      .filter((f) => !options.some((o) => o.folder === f))
      .map((f) => ({ folder: f, label: `Folder ${f}` })),
  ]
  if (!shown.length) return null
  const toggle = (folder: string, on: boolean) =>
    onChange(on ? [...picked, folder] : picked.filter((p) => p !== folder))
  return (
    <FieldSet>
      <FieldLegend variant="label">Folders</FieldLegend>
      <FieldDescription>None picked: every folder of those sources.</FieldDescription>
      <FieldGroup data-slot="checkbox-group" className="max-h-48 gap-3 overflow-y-auto">
        {shown.map((o, i) => (
          <Field key={o.folder} orientation="horizontal">
            <Checkbox
              id={`${idPrefix}-${i}`}
              checked={picked.includes(o.folder)}
              onCheckedChange={(on) => toggle(o.folder, on === true)}
            />
            <FieldContent className="min-w-0">
              <FieldLabel htmlFor={`${idPrefix}-${i}`} className="font-normal break-all">
                {o.label}
              </FieldLabel>
            </FieldContent>
          </Field>
        ))}
      </FieldGroup>
    </FieldSet>
  )
}

function ChannelSelect({
  settings,
  value,
  onChange,
  id,
}: {
  settings: DigestSettings
  value: DigestChannel
  onChange: (v: DigestChannel) => void
  id: string
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as DigestChannel)}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="email">{CHANNEL.email}</SelectItem>
        <SelectItem value="slack" disabled={!settings.channels.slack}>
          {CHANNEL.slack}
          {!settings.channels.slack && " (the Slack app isn't installed)"}
        </SelectItem>
      </SelectContent>
    </Select>
  )
}

function FrequencySelect({
  value,
  onChange,
  id,
}: {
  value: DigestFrequency
  onChange: (v: DigestFrequency) => void
  id: string
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as DigestFrequency)}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.keys(FREQUENCY) as DigestFrequency[]).map((f) => (
          <SelectItem key={f} value={f}>
            {FREQUENCY[f]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

// New digest, or Edit one: how often, how it's sent, and what it follows. The same
// form either way; a new one subscribes, an edit saves the changes.
function DigestDialog({
  settings,
  sub,
  open,
  onOpenChange,
}: {
  settings: DigestSettings
  sub?: DigestSubscription // editing this one
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const refresh = useChange()
  const [shown, setShown] = useState(false)
  const [frequency, setFrequency] = useState<DigestFrequency>("weekly")
  const [channel, setChannel] = useState<DigestChannel>("email")
  const [sources, setSources] = useState<string[]>([])
  const [folders, setFolders] = useState<string[]>([])
  // Opened: start from the digest (or the defaults), never from the last edit.
  if (open !== shown) {
    setShown(open)
    if (open) {
      setFrequency(sub?.frequency ?? "weekly")
      setChannel(sub?.channel ?? "email")
      setSources(sub?.sources ?? [])
      setFolders(sub?.folders ?? [])
    }
  }
  const save = useMutation({
    mutationFn: () =>
      sub
        ? digests.change(sub.id, { frequency, channel, sources, folders })
        : digests.subscribe({ frequency, channel, sources, folders }),
    onSuccess: () => {
      toast.success(
        sub
          ? "Digest saved."
          : `Subscribed. The first one comes ${frequency === "daily" ? "tomorrow" : "in a week"}.`,
      )
      onOpenChange(false)
      void refresh()
    },
    onError: (err) => toast.error(errorText(err)),
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    save.mutate()
  }

  const id = sub ? `edit-${sub.id}` : "new"
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>{sub ? "Edit digest" : "New digest"}</DialogTitle>
            <DialogDescription>
              Documents added, updated or removed, each with a short summary. Only documents
              you can read are ever in it.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor={`${id}-frequency`}>How often</FieldLabel>
                <FrequencySelect id={`${id}-frequency`} value={frequency} onChange={setFrequency} />
              </Field>
              <Field>
                <FieldLabel htmlFor={`${id}-channel`}>Send it by</FieldLabel>
                <ChannelSelect id={`${id}-channel`} settings={settings} value={channel} onChange={setChannel} />
              </Field>
            </div>
            <SourcePicker settings={settings} picked={sources} onChange={setSources} idPrefix={`${id}-source`} />
            <FolderPicker sources={sources} picked={folders} onChange={setFolders} idPrefix={`${id}-folder`} />
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={save.isPending}>
              {sub ? "Save" : "Subscribe"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// One digest: how often and how, what it follows and when it's next; on or off with
// its switch, and Edit, Send now and Unsubscribe in its menu.
function DigestRow({ sub, settings }: { sub: DigestSubscription; settings: DigestSettings }) {
  const refresh = useChange()
  const [editing, setEditing] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const change = useMutation({
    mutationFn: (body: Parameters<typeof digests.change>[1]) => digests.change(sub.id, body),
    onSuccess: () => void refresh(),
    onError: (err) => toast.error(errorText(err)),
  })
  const remove = useMutation({
    mutationFn: () => digests.remove(sub.id),
    onSuccess: () => {
      toast.success("Unsubscribed.")
      void refresh()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const send = useMutation({
    mutationFn: () => digests.sendNow(sub.id),
    onSuccess: (r) => {
      if (r.sent) toast.success(`Sent, with ${r.entries} document${r.entries === 1 ? "" : "s"}.`)
      else if (r.error) toast.error(r.error)
      else toast.info("Nothing you can read changed since the last one, so nothing was sent.")
      void refresh()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const labels = sub.sources.map((id) => settings.sources.find((s) => s.id === id)?.label ?? id)
  const folderCount = sub.folders.length
  const name = `${FREQUENCY[sub.frequency]} by ${CHANNEL[sub.channel].toLowerCase()}`
  const Icon = sub.channel === "slack" ? BellRing : Mail

  return (
    <li className="flex items-start gap-3 px-4 py-3">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
          {name}
          {!sub.enabled && <Badge variant="mono">paused</Badge>}
        </p>
        <p className="text-sm text-pretty text-muted-foreground">
          {labels.length ? labels.join(", ") : "Every source you can read"}
          {folderCount > 0 && ` (${folderCount} folder${folderCount === 1 ? "" : "s"})`}
        </p>
        <p className="text-xs text-muted-foreground tabular-nums">
          Last sent {timeAgo(sub.last_sent_at)}
          {sub.enabled && ` · next ${when(sub.next_due_at)}`}
        </p>
        {sub.last_error && (
          <p className="mt-1 text-sm text-pretty text-destructive">
            The last one wasn't sent: {sub.last_error}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Switch
          checked={sub.enabled}
          onCheckedChange={(on) => change.mutate({ enabled: on })}
          disabled={change.isPending}
          aria-label={`${name}: ${sub.enabled ? "on" : "off"}`}
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label={`${name}: actions`}>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuGroup>
              <DropdownMenuItem onSelect={() => setEditing(true)}>
                <Pencil /> Edit
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => send.mutate()} disabled={send.isPending}>
                <Send /> Send now
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={() => setLeaving(true)}>
              <Trash2 /> Unsubscribe
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <DigestDialog settings={settings} sub={sub} open={editing} onOpenChange={setEditing} />
      <AlertDialog open={leaving} onOpenChange={setLeaving}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Unsubscribe from this digest?</AlertDialogTitle>
            <AlertDialogDescription>It stops now. You can subscribe again any time.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => remove.mutate()}>
              Unsubscribe
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  )
}

// Settings › Digests: a person's own digests of what changed in the documents.
export function DigestsView() {
  const query = useQuery(queries.digests())
  const settings = query.data
  const [adding, setAdding] = useState(false)
  const none = !!settings && settings.subscriptions.length === 0
  const newDigest = (
    <Button size="sm" onClick={() => setAdding(true)}>
      <Plus data-icon="inline-start" /> New digest
    </Button>
  )

  return (
    <SettingsSection
      title="Digests"
      description="What changed in the documents, by email or Slack. Each lists only documents you can read when it's sent."
      action={settings && !none ? newDigest : undefined}
    >
      {query.isPending ? (
        <SettingsSkeleton rows={2} label="Loading your digests" />
      ) : !settings ? (
        <SettingsError what="your digests" onRetry={() => void query.refetch()} />
      ) : none ? (
        <SettingsEmpty
          icon={Newspaper}
          title="No digests yet"
          description="Get a short summary, daily or weekly, of the documents that were added, changed or removed."
          action={newDigest}
          example={
            <SettingsExample>
              <p className="font-medium">Your weekly digest · 2 changes</p>
              <ul className="mt-1.5 flex flex-col gap-1 text-muted-foreground">
                <li className="flex items-start gap-2">
                  <FilePlus className="mt-0.5 size-3.5 shrink-0" />
                  <span>
                    <span className="text-foreground">Travel policy 2027</span> added: new per-diem rates.
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <FilePen className="mt-0.5 size-3.5 shrink-0" />
                  <span>
                    <span className="text-foreground">Leave policy</span> updated: carry-over is now 10 days.
                  </span>
                </li>
              </ul>
            </SettingsExample>
          }
        />
      ) : (
        <SettingsList label="Your digests">
          {settings.subscriptions.map((s) => (
            <DigestRow key={s.id} sub={s} settings={settings} />
          ))}
        </SettingsList>
      )}
      {settings && <DigestDialog settings={settings} open={adding} onOpenChange={setAdding} />}
    </SettingsSection>
  )
}
