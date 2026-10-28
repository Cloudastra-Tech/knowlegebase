import { useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { History, Loader2, Plus, Send, Trash2, Webhook as WebhookIcon } from "lucide-react"
import { toast } from "sonner"

import { CodeBlock } from "@/components/api-keys-view"
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
} from "@/components/ui/dialog"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { webhooks, type Webhook, type WebhookDelivery, type WebhookSettings } from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// Access > Webhooks (admins; platform.md PL-20): the workspace's own systems are told
// when something happens. Each request is signed with the webhook's secret (shown
// once), and retried for a day if the receiver doesn't answer 2xx.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const VERIFY = `# Each request carries X-KB-Timestamp and X-KB-Signature.
# Recompute the signature with your secret and compare, and refuse
# a timestamp more than 5 minutes old:
import hashlib, hmac, time

def valid(secret, timestamp, body: bytes, signature):
    if abs(time.time() - int(timestamp)) > 300:
        return False
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest("v1=" + mac.hexdigest(), signature)`

function useRefresh() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ["webhooks"] })
}

function AddDialog({
  open,
  onOpenChange,
  settings,
  onAdded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  settings: WebhookSettings
  onAdded: (secret: string) => void
}) {
  const refresh = useRefresh()
  const [url, setUrl] = useState("")
  const [description, setDescription] = useState("")
  const [events, setEvents] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const add = useMutation({
    mutationFn: () => webhooks.add({ url: url.trim(), events, description }),
    onSuccess: ({ secret }) => {
      onOpenChange(false)
      setUrl("")
      setDescription("")
      setEvents([])
      void refresh()
      onAdded(secret)
    },
    onError: (err) => setError(errorText(err)),
  })
  const toggle = (id: string, on: boolean) =>
    setEvents((now) => (on ? [...now, id] : now.filter((e) => e !== id)))

  function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    add.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={submit} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>Add a webhook</DialogTitle>
            <DialogDescription>
              A public https:// address. It gets a signed POST for each event you pick, with
              IDs and a few fields, never document text.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <Field data-invalid={!!error || undefined}>
              <FieldLabel htmlFor="hook-url">Address</FieldLabel>
              <Input
                id="hook-url"
                type="url"
                inputMode="url"
                autoComplete="off"
                placeholder="https://siem.example.com/hooks/knowledge"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                aria-invalid={!!error || undefined}
                required
                autoFocus
              />
              {error && <FieldError>{error}</FieldError>}
            </Field>
            <Field>
              <FieldLabel htmlFor="hook-description">Description</FieldLabel>
              <Input
                id="hook-description"
                placeholder="SIEM, ITSM, #ops channel…"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                maxLength={200}
              />
            </Field>
            <FieldSet>
              <FieldLegend variant="label">Events</FieldLegend>
              <FieldGroup data-slot="checkbox-group" className="gap-3">
                {settings.events.map((ev) => (
                  <Field key={ev.id} orientation="horizontal">
                    <Checkbox
                      id={`event-${ev.id}`}
                      checked={events.includes(ev.id)}
                      onCheckedChange={(on) => toggle(ev.id, on === true)}
                    />
                    <FieldContent>
                      <FieldLabel htmlFor={`event-${ev.id}`} className="font-normal">
                        <span className="font-mono text-xs">{ev.id}</span>
                      </FieldLabel>
                      <FieldDescription>{ev.label}</FieldDescription>
                    </FieldContent>
                  </Field>
                ))}
              </FieldGroup>
            </FieldSet>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={add.isPending || !url.trim() || !events.length}>
              {add.isPending ? (
                <Loader2 className="animate-spin" data-icon="inline-start" />
              ) : (
                <Plus data-icon="inline-start" />
              )}
              Add webhook
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SecretDialog({ secret, onClose }: { secret: string | null; onClose: () => void }) {
  return (
    <Dialog open={!!secret} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Its signing secret</DialogTitle>
          <DialogDescription>
            Copy it now: it isn't shown again. Your receiver uses it to check each request
            came from here.
          </DialogDescription>
        </DialogHeader>
        {secret && <CodeBlock label="the secret" code={secret} />}
        <CodeBlock label="how to check a request" code={VERIFY} />
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DeliveryRow({ d }: { d: WebhookDelivery }) {
  return (
    <li className="flex flex-col gap-0.5 py-2.5 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-xs">{d.event}</span>
        <Badge
          variant={d.status === "failed" ? "destructive" : d.status === "delivered" ? "secondary" : "outline"}
        >
          {d.status === "pending" ? "Retrying" : d.status === "delivered" ? "Delivered" : "Failed"}
        </Badge>
      </div>
      <span className="text-xs text-muted-foreground">
        {timeAgo(d.created_at)} · {d.attempts} attempt{d.attempts === 1 ? "" : "s"}
        {d.response_code ? ` · HTTP ${d.response_code}` : ""}
        {d.status === "pending" && d.next_at
          ? ` · next try ${new Date(d.next_at * 1000).toLocaleTimeString(undefined, { timeStyle: "short" })}`
          : ""}
      </span>
      {d.error && d.status !== "delivered" && (
        <span className="text-xs text-destructive">{d.error}</span>
      )}
    </li>
  )
}

function DeliveriesSheet({ hook, onClose }: { hook: Webhook | null; onClose: () => void }) {
  const { data } = useQuery({
    ...queries.webhookDeliveries(hook?.id ?? ""),
    enabled: !!hook,
    refetchInterval: hook ? 5000 : false,
  })
  return (
    <Sheet open={!!hook} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle>Deliveries</SheetTitle>
          <SheetDescription className="truncate">{hook?.url}</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4">
          {!data ? (
            <div className="space-y-3 py-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : data.deliveries.length === 0 ? (
            <p className="py-6 text-sm text-muted-foreground">Nothing sent yet.</p>
          ) : (
            <ul className="divide-y">
              {data.deliveries.map((d) => (
                <DeliveryRow key={d.id} d={d} />
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function WebhookCard({ hook, onHistory }: { hook: Webhook; onHistory: () => void }) {
  const refresh = useRefresh()
  const change = useMutation({
    mutationFn: (enabled: boolean) => webhooks.change(hook.id, { enabled }),
    onSuccess: () => void refresh(),
    onError: (err) => toast.error(errorText(err)),
  })
  const test = useMutation({
    mutationFn: () => webhooks.test(hook.id),
    onSuccess: (d) => {
      if (d.status === "delivered") toast.success(`Delivered (HTTP ${d.response_code}).`)
      else toast.error(`Not delivered: ${d.error ?? "no answer"}.`)
      void refresh()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const remove = useMutation({
    mutationFn: () => webhooks.remove(hook.id),
    onSuccess: () => {
      toast.success("Webhook deleted.")
      void refresh()
    },
    onError: (err) => toast.error(errorText(err)),
  })

  return (
    <section className="rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-medium" title={hook.url}>
            {hook.url}
          </h3>
          <p className="text-sm text-muted-foreground">
            {hook.description ? `${hook.description} · ` : ""}secret …{hook.secret_hint} · added{" "}
            {timeAgo(hook.created_at)}
          </p>
        </div>
        <Field orientation="horizontal" className="w-auto">
          <Switch
            id={`hook-on-${hook.id}`}
            checked={hook.enabled}
            onCheckedChange={(on) => change.mutate(on)}
            disabled={change.isPending}
          />
          <FieldLabel htmlFor={`hook-on-${hook.id}`} className="font-normal">
            {hook.enabled ? "On" : "Off"}
          </FieldLabel>
        </Field>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {hook.events.map((e) => (
          <Badge key={e} variant="outline" className="font-mono font-normal">
            {e}
          </Badge>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Send data-icon="inline-start" />}
          Send a test event
        </Button>
        <Button variant="outline" size="sm" onClick={onHistory}>
          <History data-icon="inline-start" /> Deliveries
        </Button>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="outline" size="sm" className="text-destructive">
              <Trash2 data-icon="inline-start" /> Delete
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete this webhook?</AlertDialogTitle>
              <AlertDialogDescription>
                Nothing more is sent to it, and its delivery history goes too.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => remove.mutate()}>Delete</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </section>
  )
}

export function WebhooksView() {
  const { data: settings } = useQuery(queries.webhooks())
  const [adding, setAdding] = useState(false)
  const [secret, setSecret] = useState<string | null>(null)
  const [history, setHistory] = useState<Webhook | null>(null)

  if (!settings) return <Skeleton className="h-48 rounded-2xl" />
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-medium">
            <WebhookIcon className="size-4" /> Webhooks
          </h3>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Tell your own systems (a SIEM, an ITSM, chat ops) when something happens here.
            Requests are signed with HMAC-SHA256, and retried for {settings.retry_hours} hours
            until they're answered with a 2xx.
          </p>
        </div>
        <Button size="sm" onClick={() => setAdding(true)}>
          <Plus data-icon="inline-start" /> Add webhook
        </Button>
      </div>
      {settings.webhooks.length === 0 ? (
        <p className="rounded-2xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          No webhooks yet.
        </p>
      ) : (
        settings.webhooks.map((h) => (
          <WebhookCard key={h.id} hook={h} onHistory={() => setHistory(h)} />
        ))
      )}
      <AddDialog open={adding} onOpenChange={setAdding} settings={settings} onAdded={setSecret} />
      <SecretDialog secret={secret} onClose={() => setSecret(null)} />
      <DeliveriesSheet hook={history} onClose={() => setHistory(null)} />
    </div>
  )
}
