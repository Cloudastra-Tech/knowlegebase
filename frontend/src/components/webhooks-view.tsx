import { useId, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { History, Plus, RotateCcw, RotateCw, Send, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { CodeBlock } from "@/components/api-keys-view"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { Panel } from "@/components/glass/panel"
import { StatusDot, type StatusDotProps } from "@/components/glass/status-dot"
import { Notice } from "@/components/sign-in-provider-dialog"
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
import { ApiError, webhooks, type Webhook, type WebhookDelivery, type WebhookSettings } from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn, timeAgo } from "@/lib/utils"

// Access > Webhooks (admins; platform.md PL-20): the workspace's own systems are told
// when something happens. Each request is signed with the webhook's secret (shown
// once, and again when it's rotated), and retried for a day if the receiver doesn't
// answer 2xx. A webhook whose deliveries keep failing is turned off by the server.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// webhooks_store.MAX_PER_WORKSPACE: the server refuses one more (400) past it.
const MAX_WEBHOOKS = 20
// kb/services/webhooks.py, ROTATE_GRACE_HOURS: how long the old secret still signs.
const ROTATE_GRACE_HOURS = 24

const VERIFY = `# Each request carries X-KB-Timestamp and X-KB-Signature.
# Recompute the signature with your secret and compare, and refuse
# a timestamp more than 5 minutes old. Just after the secret is
# rotated the header holds two signatures, comma-separated.
import hashlib, hmac, time

def valid(secret, timestamp, body: bytes, signature):
    if abs(time.time() - int(timestamp)) > 300:
        return False
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    mine = "v1=" + mac.hexdigest()
    return any(hmac.compare_digest(mine, s.strip()) for s in signature.split(","))`

const clock = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })

function useRefresh() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ["webhooks"] })
}

// Why webhooks can't be added or listed right now, in words, or null if they can.
function unavailable(error: unknown) {
  if (!error) return null
  if (error instanceof ApiError && (error.status === 404 || error.status === 501))
    return "Webhooks aren't available on this server. Its operator can update it to turn them on."
  if (error instanceof ApiError && error.status === 503)
    return "Webhooks are turned off on this server for now. Try again later, or ask its operator."
  return `Webhooks couldn't be loaded: ${errorText(error)}`
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
                className="font-mono"
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
            <Button type="submit" loading={add.isPending} disabled={!url.trim() || !events.length}>
              {!add.isPending && <Plus data-icon="inline-start" />}
              Add webhook
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// A secret, shown once: when a webhook is added, and when its secret is rotated.
export type ShownSecret = { secret: string; rotated: boolean; graceUntil: number | null }

function SecretDialog({ shown, onClose }: { shown: ShownSecret | null; onClose: () => void }) {
  return (
    <Dialog open={!!shown} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{shown?.rotated ? "Its new signing secret" : "Its signing secret"}</DialogTitle>
          <DialogDescription>
            Copy it now: it isn't shown again. Your receiver uses it to check each request
            came from here.
          </DialogDescription>
        </DialogHeader>
        {shown?.rotated && (
          <Notice>
            {shown.graceUntil
              ? `The old secret also signs each request until ${clock(shown.graceUntil)}, so put this one in your receiver before then.`
              : "The old secret has stopped signing: requests are signed with this one only, from now."}
          </Notice>
        )}
        {shown && <CodeBlock label="the secret" code={shown.secret} wrap />}
        <CodeBlock label="how to check a request" code={VERIFY} />
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// A delivery's state as a mono glyph, coloured, with its word for assistive technology.
const MARK: Record<Exclude<WebhookDelivery["status"], "pending">, { glyph: string; label: string; tone: string }> = {
  delivered: { glyph: "✓", label: "Delivered", tone: "text-success" },
  failed: { glyph: "×", label: "Failed", tone: "text-destructive" },
}

// One delivery on the timeline: "├─ ✓ 5 min ago  member.added  HTTP 200 · 1 attempt".
function DeliveryRow({ d, last, hook }: { d: WebhookDelivery; last: boolean; hook: Webhook }) {
  const queryClient = useQueryClient()
  const reasonId = useId()
  const again = useMutation({
    mutationFn: () => webhooks.redeliver(d.id),
    onSuccess: () => {
      toast.success(`Sending ${d.event} again.`)
      void queryClient.invalidateQueries({ queryKey: ["webhooks", hook.id, "deliveries"] })
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const mark = d.status === "pending" ? null : MARK[d.status]
  return (
    <li className="flex gap-2 py-2.5 text-sm">
      <span aria-hidden className="font-mono leading-5 text-muted-foreground select-none">
        {last ? "└─" : "├─"}
      </span>
      <span className="w-4 shrink-0 text-center font-mono leading-5">
        {mark ? (
          <>
            <span aria-hidden className={mark.tone}>
              {mark.glyph}
            </span>
            <span className="sr-only">{mark.label}</span>
          </>
        ) : (
          <AsciiSpinner label="Retrying" className="text-signal" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-mono leading-5 tabular-nums">{timeAgo(d.created_at)}</span>
          <Badge variant="mono" className="max-w-full">
            <span className="truncate">{d.event}</span>
          </Badge>
        </div>
        <p className="mt-0.5 font-mono text-xs text-muted-foreground tabular-nums">
          {d.attempts} attempt{d.attempts === 1 ? "" : "s"}
          {d.response_code ? ` · HTTP ${d.response_code}` : ""}
          {d.status === "pending" && d.next_at
            ? ` · next try ${new Date(d.next_at * 1000).toLocaleTimeString(undefined, { timeStyle: "short" })}`
            : ""}
        </p>
        {d.error && d.status !== "delivered" && (
          <p className="mt-0.5 text-xs break-words text-destructive">{d.error}</p>
        )}
        {d.status === "failed" && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            <Button
              size="xs"
              variant="outline"
              loading={again.isPending}
              disabled={!hook.enabled}
              aria-describedby={hook.enabled ? undefined : reasonId}
              onClick={() => again.mutate()}
            >
              {!again.isPending && <RotateCcw data-icon="inline-start" />}
              Send again
            </Button>
            {!hook.enabled && (
              <span id={reasonId} className="text-xs text-muted-foreground">
                Turn the webhook on to send it again.
              </span>
            )}
          </div>
        )}
      </div>
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
          <SheetDescription className="truncate font-mono text-xs">{hook?.url}</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4">
          {!data || !hook ? (
            <div className="space-y-3 py-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : data.deliveries.length === 0 ? (
            <AsciiEmptyState art="inbox" size="sm" title="Nothing sent yet." />
          ) : (
            <ul aria-label="Deliveries" className="py-2">
              {data.deliveries.map((d, i) => (
                <DeliveryRow key={d.id} d={d} hook={hook} last={i === data.deliveries.length - 1} />
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

// What the webhook is doing, as a dot with words (StatusDot).
function hookStatus(hook: Webhook): { status: StatusDotProps["status"]; label: string } {
  if (hook.disabled_reason) return { status: "error", label: "Turned off by the server" }
  if (!hook.enabled) return { status: "off", label: "Off" }
  if (hook.failures) return { status: "warning", label: `Failing (${hook.failures} in a row)` }
  return { status: "live", label: "On" }
}

// Until when the secret a rotation replaced still signs, if it still does.
function oldSecretUntil(hook: Webhook) {
  const until = hook.previous_secret_until
  return until && until > Date.now() / 1000 ? until : null
}

function RotateSecret({ hook, onRotated }: { hook: Webhook; onRotated: (shown: ShownSecret) => void }) {
  const refresh = useRefresh()
  const [open, setOpen] = useState(false)
  const [stopOld, setStopOld] = useState(false)
  const rotate = useMutation({
    mutationFn: () => webhooks.rotateSecret(hook.id, stopOld ? 0 : undefined),
    onSuccess: ({ webhook, secret }) => {
      setOpen(false)
      void refresh()
      onRotated({ secret, rotated: true, graceUntil: webhook.previous_secret_until ?? null })
    },
    onError: (err) => toast.error(errorText(err)),
  })
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) setStopOld(false)
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="outline" size="sm">
          <RotateCw data-icon="inline-start" /> Rotate secret
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Rotate this webhook's secret?</AlertDialogTitle>
          <AlertDialogDescription>
            A new secret signs every request from now on, and is shown once. The old one keeps
            signing alongside it for {ROTATE_GRACE_HOURS} hours, so your receiver can switch
            without missing a request.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Field orientation="horizontal">
          <Checkbox
            id={`stop-old-${hook.id}`}
            checked={stopOld}
            onCheckedChange={(on) => setStopOld(on === true)}
          />
          <FieldContent>
            <FieldLabel htmlFor={`stop-old-${hook.id}`} className="font-normal">
              Stop the old secret now
            </FieldLabel>
            <FieldDescription>
              If it leaked. Requests fail at your receiver until it has the new one.
            </FieldDescription>
          </FieldContent>
        </Field>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault()
              rotate.mutate()
            }}
            disabled={rotate.isPending}
          >
            {rotate.isPending && <AsciiSpinner label="" size="xs" />}
            Rotate
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function WebhookCard({
  hook,
  onHistory,
  onRotated,
}: {
  hook: Webhook
  onHistory: () => void
  onRotated: (shown: ShownSecret) => void
}) {
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
  const state = hookStatus(hook)
  const graceUntil = oldSecretUntil(hook)

  return (
    <li className="rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-56">
          <h4 className="truncate font-mono text-sm font-medium" title={hook.url}>
            {hook.url}
          </h4>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {hook.description ? `${hook.description} · ` : ""}secret{" "}
            <span className="font-mono">…{hook.secret_hint}</span> · added {timeAgo(hook.created_at)}
          </p>
          <StatusDot status={state.status} label={state.label} className="mt-1.5 text-muted-foreground" />
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
      {hook.disabled_reason && (
        <Notice className="mt-3">
          The server turned it off{hook.disabled_at ? ` ${timeAgo(hook.disabled_at)}` : ""}
          {hook.disabled_reason === "failing" ? ": its deliveries kept failing" : ""}, and
          what was waiting for it won't be sent. Fix the endpoint, then turn it on again; failed
          deliveries can be sent again from Deliveries.
        </Notice>
      )}
      {graceUntil && (
        <p className="mt-2 text-xs text-muted-foreground">
          The previous secret also signs until <span className="font-mono">{clock(graceUntil)}</span>.
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {hook.events.map((e) => (
          <Badge key={e} variant="mono">
            {e}
          </Badge>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => test.mutate()} loading={test.isPending}>
          {!test.isPending && <Send data-icon="inline-start" />}
          Send a test event
        </Button>
        <Button variant="outline" size="sm" onClick={onHistory}>
          <History data-icon="inline-start" /> Deliveries
        </Button>
        <RotateSecret hook={hook} onRotated={onRotated} />
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
    </li>
  )
}

export function WebhooksView() {
  const { data: settings, error, isPending, refetch, isRefetching } = useQuery(queries.webhooks())
  const [adding, setAdding] = useState(false)
  const [secret, setSecret] = useState<ShownSecret | null>(null)
  const [history, setHistory] = useState<Webhook | null>(null)
  const reasonId = useId()

  if (isPending) return <Skeleton className="h-48 rounded-2xl" />
  const off = settings ? null : unavailable(error)
  const full = !!settings && settings.webhooks.length >= MAX_WEBHOOKS
  // Why "Add webhook" can't be used, said beside it (a disabled button has no tooltip).
  const reason = off
    ? "Webhooks can't be added on this server."
    : full
      ? `A workspace can have up to ${MAX_WEBHOOKS} webhooks. Delete one to add another.`
      : null

  return (
    <Panel
      title="Webhooks"
      actions={
        <Button
          size="sm"
          onClick={() => setAdding(true)}
          disabled={!!reason}
          aria-describedby={reason ? reasonId : undefined}
        >
          <Plus data-icon="inline-start" /> Add webhook
        </Button>
      }
    >
      <p className="-mt-2 max-w-2xl text-sm text-pretty text-muted-foreground">
        Tell your own systems (a SIEM, an ITSM, chat ops) when something happens here.
        Requests are signed with HMAC-SHA256
        {settings ? `, and retried for ${settings.retry_hours} hours until they're answered with a 2xx` : ""}
        .
      </p>
      {reason && (
        <p id={reasonId} className={cn("mt-2 text-xs text-muted-foreground", off && "sr-only")}>
          {reason}
        </p>
      )}
      <div className="mt-4">
        {off ? (
          <AsciiEmptyState
            art="broken"
            size="sm"
            headingLevel={4}
            title="Webhooks are unavailable."
            description={off}
            action={
              <Button size="sm" variant="outline" loading={isRefetching} onClick={() => void refetch()}>
                Try again
              </Button>
            }
          />
        ) : settings && settings.webhooks.length === 0 ? (
          <AsciiEmptyState
            art="plug"
            size="sm"
            headingLevel={4}
            title="No webhooks yet."
            description="Add one to send events to an https:// address you own."
          />
        ) : (
          settings && (
            <ul className="flex flex-col gap-3">
              {settings.webhooks.map((h) => (
                <WebhookCard key={h.id} hook={h} onHistory={() => setHistory(h)} onRotated={setSecret} />
              ))}
            </ul>
          )
        )}
      </div>
      {settings && (
        <AddDialog
          open={adding}
          onOpenChange={setAdding}
          settings={settings}
          onAdded={(s) => setSecret({ secret: s, rotated: false, graceUntil: null })}
        />
      )}
      <SecretDialog shown={secret} onClose={() => setSecret(null)} />
      <DeliveriesSheet hook={history} onClose={() => setHistory(null)} />
    </Panel>
  )
}
