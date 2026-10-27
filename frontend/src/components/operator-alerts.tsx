import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BellRing, Check, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { operatorApi, type OperatorWorkspace } from "@/lib/api/operator"
import { timeAgo } from "@/lib/utils"

// The operator console's abuse and cost monitoring (platform.md PL-36): what the
// monitor found, to acknowledge, and each workspace's daily spend caps (PL-02).

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const PLATFORM = "_platform"

export function AlertsPanel() {
  const queryClient = useQueryClient()
  const [show, setShow] = useState<"open" | "all">("open")
  const list = useQuery({
    queryKey: ["operator", "alerts", show],
    queryFn: () => operatorApi.alerts(show === "open"),
    retry: false,
  })
  const ack = useMutation({
    mutationFn: (id: number) => operatorApi.acknowledge(id),
    onSuccess: () => {
      toast.success("Acknowledged.")
      void queryClient.invalidateQueries({ queryKey: ["operator"] })
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const alerts = list.data?.alerts ?? []
  const t = list.data?.thresholds

  return (
    <section className="rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 font-medium">
            <BellRing className="size-4" /> Alerts
          </h3>
          <p className="text-sm text-muted-foreground">
            Unusual usage, failed sign-ins and spend, checked every hour.
            {t &&
              ` A spike is ${t.spike_factor}× the week's median (at least ${t.spike_min} questions); one person, over ${t.user_per_hour} an hour.`}
          </p>
        </div>
        <Tabs value={show} onValueChange={(v) => setShow(v as "open" | "all")}>
          <TabsList>
            <TabsTrigger value="open">Open</TabsTrigger>
            <TabsTrigger value="all">All</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <ul className="mt-4 divide-y rounded-xl border border-border/70">
        {list.isPending && (
          <li className="p-3">
            <Skeleton className="h-10 w-full" />
          </li>
        )}
        {alerts.map((a) => (
          <li key={a.id} className="flex items-start gap-3 px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">{a.title}</span>
                <Badge
                  variant={a.kind === "spend_hard" ? "destructive" : "outline"}
                  className="font-normal"
                >
                  {a.workspace === PLATFORM ? "Platform" : a.workspace}
                </Badge>
              </p>
              <p className="text-sm text-muted-foreground">{a.summary}</p>
              <p className="text-xs text-muted-foreground">
                {timeAgo(a.created_at)}
                {a.notified.length ? ` · emailed ${a.notified.length}` : ""}
                {a.acknowledged_by ? ` · acknowledged by ${a.acknowledged_by}` : ""}
              </p>
            </div>
            {!a.acknowledged_at && (
              <Button
                size="sm"
                variant="outline"
                disabled={ack.isPending}
                onClick={() => ack.mutate(a.id)}
              >
                <Check data-icon="inline-start" /> Acknowledge
              </Button>
            )}
          </li>
        ))}
        {list.data && !alerts.length && (
          <li className="px-3 py-6 text-center text-sm text-muted-foreground">
            {show === "open" ? "Nothing needs a look." : "No alerts yet."}
          </li>
        )}
      </ul>
    </section>
  )
}

const usd = (v: string) => {
  const n = Number(v)
  return v.trim() === "" ? null : Number.isFinite(n) && n > 0 ? n : NaN
}

export function SpendCapsDialog({
  ws,
  onClose,
}: {
  ws: OperatorWorkspace | null
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  // Rendered once per workspace opened (keyed by its slug), so these start from it.
  const [soft, setSoft] = useState(ws?.spend.soft_usd != null ? String(ws.spend.soft_usd) : "")
  const [hard, setHard] = useState(ws?.spend.hard_usd != null ? String(ws.spend.hard_usd) : "")
  const save = useMutation({
    mutationFn: () => operatorApi.setSpendCaps(ws!.slug, { soft_usd: usd(soft), hard_usd: usd(hard) }),
    onSuccess: () => {
      toast.success(`Spend caps saved for ${ws!.name}.`)
      void queryClient.invalidateQueries({ queryKey: ["operator", "workspaces"] })
      onClose()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const bad = Number.isNaN(usd(soft)) || Number.isNaN(usd(hard))
  return (
    <Dialog open={!!ws} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Daily spend caps · {ws?.name}</DialogTitle>
          <DialogDescription>
            In US dollars per UTC day, at list prices. Today so far: $
            {(ws?.spend.today_usd ?? 0).toFixed(2)}. Empty uses the server's default.
          </DialogDescription>
        </DialogHeader>
        <form
          id="spend-caps"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
        >
          <FieldGroup className="gap-4 sm:grid sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="spend-soft">Soft limit</FieldLabel>
              <Input
                id="spend-soft"
                type="number"
                inputMode="decimal"
                min={0}
                step="0.01"
                value={soft}
                onChange={(e) => setSoft(e.target.value)}
                placeholder="e.g. 20"
              />
              <FieldDescription>Its admins and the operators are emailed.</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="spend-hard">Hard limit</FieldLabel>
              <Input
                id="spend-hard"
                type="number"
                inputMode="decimal"
                min={0}
                step="0.01"
                value={hard}
                onChange={(e) => setHard(e.target.value)}
                placeholder="e.g. 50"
              />
              <FieldDescription>Questions pause until midnight UTC.</FieldDescription>
            </Field>
          </FieldGroup>
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="spend-caps" disabled={bad || save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" data-icon="inline-start" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
