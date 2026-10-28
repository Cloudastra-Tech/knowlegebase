import { useMemo, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BellRing, Mail, Newspaper, Plus, Send, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { Panel } from "@/components/glass/panel"
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
  DialogTrigger,
} from "@/components/ui/dialog"
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
import { Skeleton } from "@/components/ui/skeleton"
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

// /digests: a person's own digests of what changed in the documents (platform.md
// PL-30). What one holds is worked out when it's sent, from what they can read then.

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

function NewDigestDialog({ settings }: { settings: DigestSettings }) {
  const refresh = useChange()
  const [open, setOpen] = useState(false)
  const [frequency, setFrequency] = useState<DigestFrequency>("weekly")
  const [channel, setChannel] = useState<DigestChannel>("email")
  const [sources, setSources] = useState<string[]>([])
  const [folders, setFolders] = useState<string[]>([])
  const add = useMutation({
    mutationFn: () => digests.subscribe({ frequency, channel, sources, folders }),
    onSuccess: () => {
      toast.success(`Subscribed. The first one comes ${frequency === "daily" ? "tomorrow" : "in a week"}.`)
      setOpen(false)
      setSources([])
      setFolders([])
      void refresh()
    },
    onError: (err) => toast.error(errorText(err)),
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    add.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus data-icon="inline-start" /> New digest
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>New digest</DialogTitle>
            <DialogDescription>
              Documents added, updated or removed, each with a short summary. Only documents
              you can read are ever in it.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="new-frequency">How often</FieldLabel>
                <FrequencySelect id="new-frequency" value={frequency} onChange={setFrequency} />
              </Field>
              <Field>
                <FieldLabel htmlFor="new-channel">Send it by</FieldLabel>
                <ChannelSelect id="new-channel" settings={settings} value={channel} onChange={setChannel} />
              </Field>
            </div>
            <SourcePicker settings={settings} picked={sources} onChange={setSources} idPrefix="new-source" />
            <FolderPicker sources={sources} picked={folders} onChange={setFolders} idPrefix="new-folder" />
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={add.isPending}>
              Subscribe
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function DigestCard({ sub, settings }: { sub: DigestSubscription; settings: DigestSettings }) {
  const refresh = useChange()
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

  return (
    <Panel className="min-w-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-base font-semibold tracking-tight">
            {sub.channel === "slack" ? (
              <BellRing className="size-4 text-muted-foreground" />
            ) : (
              <Mail className="size-4 text-muted-foreground" />
            )}
            {FREQUENCY[sub.frequency]} by {CHANNEL[sub.channel].toLowerCase()}
            {!sub.enabled && <Badge variant="mono">paused</Badge>}
          </h3>
          <p className="text-sm text-muted-foreground">
            {labels.length ? labels.join(", ") : "Every source you can read"}
            {folderCount > 0 && ` (${folderCount} folder${folderCount === 1 ? "" : "s"})`} · last sent{" "}
            {timeAgo(sub.last_sent_at)}
            {sub.enabled && ` · next ${when(sub.next_due_at)}`}
          </p>
          {sub.last_error && <p className="mt-1 text-sm text-destructive">{sub.last_error}</p>}
        </div>
        <Field orientation="horizontal" className="w-auto">
          <Switch
            id={`on-${sub.id}`}
            checked={sub.enabled}
            onCheckedChange={(on) => change.mutate({ enabled: on })}
            disabled={change.isPending}
          />
          <FieldLabel htmlFor={`on-${sub.id}`} className="font-normal">
            {sub.enabled ? "On" : "Off"}
          </FieldLabel>
        </Field>
      </div>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Field>
          <FieldLabel htmlFor={`freq-${sub.id}`}>How often</FieldLabel>
          <FrequencySelect
            id={`freq-${sub.id}`}
            value={sub.frequency}
            onChange={(frequency) => change.mutate({ frequency })}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor={`channel-${sub.id}`}>Send it by</FieldLabel>
          <ChannelSelect
            id={`channel-${sub.id}`}
            settings={settings}
            value={sub.channel}
            onChange={(channel) => change.mutate({ channel })}
          />
        </Field>
      </div>
      <div className="mt-4">
        <SourcePicker
          settings={settings}
          picked={sub.sources}
          onChange={(sources) => change.mutate({ sources })}
          idPrefix={`source-${sub.id}`}
        />
      </div>
      <div className="mt-4">
        <FolderPicker
          sources={sub.sources}
          picked={sub.folders}
          onChange={(folders) => change.mutate({ folders })}
          idPrefix={`folder-${sub.id}`}
        />
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => send.mutate()} loading={send.isPending}>
          {!send.isPending && <Send data-icon="inline-start" />}
          Send now
        </Button>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="outline" size="sm" className="text-destructive">
              <Trash2 data-icon="inline-start" /> Unsubscribe
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Unsubscribe from this digest?</AlertDialogTitle>
              <AlertDialogDescription>
                It stops now. You can subscribe again any time.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => remove.mutate()}>Unsubscribe</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </Panel>
  )
}

export function DigestsView() {
  const { data: settings } = useQuery(queries.digests())
  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <ScreenHeader
        icon={Newspaper}
        title="Digests"
        description="What changed in the documents, by email or Slack. Each one only lists documents you can read when it's sent."
        actions={settings && <NewDigestDialog settings={settings} />}
      />
      <div className="mt-6 flex flex-col gap-4">
        {!settings ? (
          <Skeleton className="h-48 rounded-2xl" />
        ) : settings.subscriptions.length === 0 ? (
          <Panel>
            <AsciiEmptyState
              art="inbox"
              title="No digests yet."
              description="Subscribe to get a weekly summary of what changed."
            />
          </Panel>
        ) : (
          settings.subscriptions.map((s) => <DigestCard key={s.id} sub={s} settings={settings} />)
        )}
      </div>
    </div>
  )
}
