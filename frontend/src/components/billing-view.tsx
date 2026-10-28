import { useEffect, useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, CreditCard, ExternalLink, Minus, Trash2, TriangleAlert } from "lucide-react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { AsciiProgress } from "@/components/ascii/ascii-progress"
import { Panel } from "@/components/glass/panel"
import { ScreenHeader } from "@/components/screen-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { billing, type Billing, type BillingStatus, type Plan } from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn } from "@/lib/utils"

type On = Extract<Billing, { on: true }>

const STATUS: Record<BillingStatus, string> = {
  trialing: "Trial",
  active: "Active",
  past_due: "Payment due",
  canceled: "Canceled",
}

const day = (epoch: number) => new Date(epoch * 1000).toLocaleDateString(undefined, { dateStyle: "medium" })

function money(cents: number, currency: string) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency.toUpperCase(),
    maximumFractionDigits: cents % 100 ? 2 : 0,
  }).format(cents / 100)
}

const LIMIT_WORDS: Record<string, (n: number) => string> = {
  questions_month: (n) => `${n.toLocaleString()} questions a month`,
  docs: (n) => `${n.toLocaleString()} documents`,
  storage_mb: (n) => (n >= 1024 ? `${Math.round(n / 1024)} GB of files` : `${n} MB of files`),
  connectors: (n) => `${n} connector${n === 1 ? "" : "s"}`,
}
const UNLIMITED: Record<string, string> = {
  questions_month: "Unlimited questions",
  docs: "Unlimited documents",
  storage_mb: "Unlimited storage",
  connectors: "Unlimited connectors",
}

function statusLine(b: On) {
  if (b.expired) return "Your trial has ended, so the workspace is read-only."
  if (b.status === "trialing" && b.trial_days_left !== null)
    return `${b.trial_days_left} day${b.trial_days_left === 1 ? "" : "s"} left in your trial${
      b.trial_ends_at ? `, until ${day(b.trial_ends_at)}` : ""
    }.`
  if (b.status === "canceled") return "The subscription was canceled, so the workspace is read-only."
  if (b.status === "past_due") return "Stripe couldn't take the last payment. Everything still works for now."
  if (b.current_period_end) return `Renews on ${day(b.current_period_end)}.`
  return null
}

// What the workspace uses of each of its plan's limits.
function Usage({ b }: { b: On }) {
  return (
    <Panel
      title="Usage"
      description={
        <>
          Questions start again on {new Date(`${b.resets_on}T00:00:00Z`).toLocaleDateString(undefined, { dateStyle: "medium" })}.
          Sample documents don't count.
        </>
      }
      className="min-w-0"
    >
      <ul className="grid gap-4 sm:grid-cols-2">
        {b.usage.map((u) => {
          const share = u.limit ? Math.min(100, (u.used / u.limit) * 100) : 0
          const full = u.limit !== null && u.used >= u.limit
          return (
            <li key={u.id} className="min-w-0">
              <div className="flex items-baseline justify-between gap-2 text-sm">
                <span>{u.label}</span>
                <span className={cn("tabular-nums text-muted-foreground", full && "font-medium text-destructive")}>
                  {u.used.toLocaleString()}
                  {u.limit === null ? "" : ` of ${u.limit.toLocaleString()}`}
                </span>
              </div>
              {u.limit === null ? (
                <p className="mt-1 text-xs text-muted-foreground">No limit on your plan</p>
              ) : (
                <AsciiProgress
                  value={share}
                  className="mt-2"
                  label={`${u.label}: ${u.used} of ${u.limit}`}
                />
              )}
            </li>
          )
        })}
        <li className="min-w-0">
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span>Seats</span>
            <span className="tabular-nums text-muted-foreground">
              {b.seats.used.toLocaleString()}
              {b.seats.paid ? ` of ${b.seats.paid.toLocaleString()} paid` : ""}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            People who signed in or asked something in the last {b.seats.days} days. Members who
            haven't don't count.
          </p>
        </li>
      </ul>
    </Panel>
  )
}

function PlanCard({
  plan,
  canBuy,
  onChoose,
  choosing,
}: {
  plan: Plan
  canBuy: boolean
  onChoose: () => void
  choosing: boolean
}) {
  return (
    <li
      className={cn(
        "glass-subtle flex min-w-0 flex-col rounded-2xl p-5",
        plan.current && "border-signal/40",
      )}
      aria-label={`${plan.name} plan`}
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-semibold tracking-tight">{plan.name}</h4>
        {plan.current && <Badge variant="signal">Current plan</Badge>}
      </div>
      <p className="mt-1 text-2xl font-semibold tracking-tight">
        <span className="font-mono font-medium tabular-nums">{money(plan.seat_price, plan.currency)}</span>
        <span className="text-sm font-normal text-muted-foreground"> a seat / month</span>
      </p>
      <ul className="mt-4 flex flex-1 flex-col gap-1.5 text-sm">
        {(Object.keys(plan.limits) as (keyof Plan["limits"])[]).map((id) => {
          const n = plan.limits[id]
          return (
            <li key={id} className="flex gap-2">
              <Check className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              {n === null ? UNLIMITED[id] : LIMIT_WORDS[id](n)}
            </li>
          )
        })}
        {plan.features.map((f) => (
          <li key={f.id} className={cn("flex gap-2", !f.included && "text-muted-foreground")}>
            {f.included ? (
              <Check className="mt-0.5 size-3.5 shrink-0" />
            ) : (
              <Minus className="mt-0.5 size-3.5 shrink-0" aria-label="Not included" />
            )}
            {f.label}
          </li>
        ))}
      </ul>
      {!plan.current && (
        <Button
          className="mt-5"
          variant={plan.id === "business" ? "default" : "outline"}
          disabled={!canBuy || !plan.available}
          loading={choosing}
          onClick={onChoose}
        >
          {plan.available ? `Choose ${plan.name}` : "Not on sale here yet"}
        </Button>
      )}
    </li>
  )
}

// Who may change the plan: the founder to start with, and whoever an owner adds.
function Owners({ b }: { b: On }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState("")
  const [busy, setBusy] = useState<string | null>(null)

  async function run(key: string, call: () => Promise<unknown>, done: string) {
    setBusy(key)
    try {
      await call()
      toast.success(done)
      setEmail("")
      void queryClient.invalidateQueries({ queryKey: queries.billing().queryKey })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't change the owners.")
    } finally {
      setBusy(null)
    }
  }

  function add(e: FormEvent) {
    e.preventDefault()
    const who = email.trim().toLowerCase()
    if (who) void run("add", () => billing.addOwner(who), `${who} can change billing now.`)
  }

  return (
    <Panel
      title="Billing owners"
      description="Only they can change the plan or payment details. Admins see this page; Cloudastra support never changes it."
      className="min-w-0"
    >
      <ul className="divide-y divide-border/60">
        {b.owners.map((o) => (
          <li key={o.username} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span className="min-w-0 truncate">{o.username}</span>
            {b.is_owner && b.owners.length > 1 && (
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={`Remove ${o.username}`}
                disabled={busy !== null}
                onClick={() =>
                  run(o.username, () => billing.removeOwner(o.username), `${o.username} removed.`)
                }
              >
                <Trash2 />
              </Button>
            )}
          </li>
        ))}
      </ul>
      {b.is_owner && (
        <form onSubmit={add} className="mt-3 flex flex-col gap-2 sm:flex-row">
          <Input
            type="email"
            placeholder="colleague@company.com"
            aria-label="Member's email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Button
            type="submit"
            variant="outline"
            loading={busy === "add"}
            disabled={!email.trim() || busy !== null}
          >
            Add owner
          </Button>
        </form>
      )}
    </Panel>
  )
}

// /billing (BILLING=on): the plan, usage against its limits, seats, the trial, and
// Stripe Checkout / the Customer Portal for the workspace's billing owners.
export function BillingView() {
  const query = useQuery(queries.billing())
  const [params, setParams] = useSearchParams()
  const [going, setGoing] = useState<string | null>(null)

  // Back from Stripe Checkout: the plan changes when Stripe's webhook arrives.
  useEffect(() => {
    const checkout = params.get("checkout")
    if (!checkout) return
    if (checkout === "done")
      toast.success("Thanks. Your plan changes as soon as Stripe confirms the payment.")
    setParams({}, { replace: true })
  }, [params, setParams])

  async function go(key: string, call: () => Promise<{ url: string }>) {
    setGoing(key)
    try {
      const { url } = await call()
      window.location.assign(url)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't open Stripe.")
      setGoing(null)
    }
  }

  const b = query.data
  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <ScreenHeader
        icon={CreditCard}
        title="Plan & billing"
        description="Your workspace's plan, what it uses of it, and who pays."
      />

      {!b && (
        <div className="mt-6 grid gap-4">
          <Skeleton className="h-28 rounded-2xl" />
          <Skeleton className="h-48 rounded-2xl" />
        </div>
      )}
      {b && !b.on && (
        <Panel className="mt-6">
          <AsciiEmptyState art="plug" title="Billing isn't turned on on this server." />
        </Panel>
      )}
      {b?.on && !b.managed && (
        <Panel className="mt-6">
          <AsciiEmptyState
            art="inbox"
            title="No plan to manage"
            description="This workspace isn't on a paid plan or a trial here, so nothing is limited. Cloudastra sets its plan if that changes."
          />
        </Panel>
      )}
      {b?.on && b.managed && b.plan && b.status && (
        <div className="mt-6 flex flex-col gap-6">
          {b.read_only && (
            <Alert variant="destructive">
              <TriangleAlert />
              <AlertTitle>The workspace is read-only</AlertTitle>
              <AlertDescription>
                Everyone can still sign in, read answers already given and export the workspace.
                Nobody can ask, upload or change anything until {b.is_owner ? "you pick" : "an owner picks"} a plan.
              </AlertDescription>
            </Alert>
          )}
          <Panel className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-lg font-semibold tracking-tight">{b.plan.name}</h3>
                <Badge variant={b.read_only || b.status === "past_due" ? "destructive" : "secondary"}>
                  {b.expired ? "Trial ended" : STATUS[b.status]}
                </Badge>
              </div>
              {statusLine(b) && <p className="mt-1 text-sm text-muted-foreground">{statusLine(b)}</p>}
            </div>
            {b.is_owner && b.portal && (
              <Button
                variant="outline"
                disabled={going !== null}
                loading={going === "portal"}
                onClick={() => go("portal", billing.portal)}
              >
                {going !== "portal" && <ExternalLink data-icon="inline-start" />}
                Payment details and invoices
              </Button>
            )}
          </Panel>

          <Usage b={b} />

          <section aria-labelledby="billing-plans">
            <AsciiDivider as="h3" id="billing-plans" label="Plans" />
            <p className="mt-3 text-sm text-pretty text-muted-foreground">
              {b.is_owner
                ? `Billed monthly for each seat in use (${Math.max(1, b.seats.used)} now), through Stripe.`
                : `Only a billing owner (${b.owners.map((o) => o.username).join(", ") || "none yet"}) can change the plan.`}
              {b.stripe.live_key_refused &&
                " Payments are off: this server has a live Stripe key without STRIPE_ALLOW_LIVE."}
              {!b.stripe.configured && !b.stripe.live_key_refused && " Payments aren't set up on this server yet."}
            </p>
            <ul className="mt-4 grid gap-4 md:grid-cols-3">
              {b.plans.map((p) => (
                <PlanCard
                  key={p.id}
                  plan={p}
                  canBuy={b.is_owner && going === null}
                  choosing={going === p.id}
                  onChoose={() => go(p.id, () => billing.checkout(p.id))}
                />
              ))}
            </ul>
          </section>

          <Owners b={b} />
        </div>
      )}
    </div>
  )
}
