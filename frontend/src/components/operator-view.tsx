import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  Building2,
  KeyRound,
  LifeBuoy,
  LogOut,
  MoreHorizontal,
  RefreshCw,
  ScrollText,
  Search,
  ShieldCheck,
  Timer,
  UserCog,
  Wallet,
} from "lucide-react"
import { toast } from "sonner"

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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { AlertsPanel, SpendCapsDialog } from "@/components/operator-alerts"
import { CopyField } from "@/components/sign-in-provider-dialog"
import { operatorApi, operatorSession, type OperatorWorkspace } from "@/lib/api/operator"
import { cn, timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// Why the operator sign-in sent someone back here (kb/routers/operator.py).
const SIGN_IN_ERRORS: Record<string, string> = {
  not_operator: "That account isn't an operator of this server.",
  mfa_required: "Sign in with a second factor (your provider must report it).",
  sso_unavailable: "That sign-in option isn't set up for operators.",
  sso_failed: "Single sign-on didn't finish. Please try again.",
  cancelled: "Sign-in was cancelled.",
  unverified: "Your provider didn't confirm your email address.",
  not_workspace: "Use your work account, not a personal one.",
}

const STATUS_LABEL: Record<OperatorWorkspace["status"], string> = {
  active: "Active",
  suspended: "Suspended",
  deleting: "Being deleted",
}

function StatusBadge({ ws }: { ws: OperatorWorkspace }) {
  return (
    <Badge
      variant={ws.status === "active" ? "secondary" : "destructive"}
      className="font-normal"
      title={ws.suspended_reason ?? undefined}
    >
      {STATUS_LABEL[ws.status]}
    </Badge>
  )
}

// The plan, and where its trial stands: "Trial · 5 days left", "Business · past due".
function planText(plan: OperatorWorkspace["plan"], now = Date.now() / 1000) {
  if (!plan) return "—"
  if (plan.status === "trialing") {
    if (plan.trial_expired || plan.trial_ends_at === null) return `${plan.name} · trial ended`
    const days = Math.ceil((plan.trial_ends_at - now) / 86400)
    return `${plan.name} · ${days} day${days === 1 ? "" : "s"} left`
  }
  return `${plan.name} · ${{ active: "active", past_due: "past due", canceled: "canceled" }[plan.status]}`
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate text-sm">{value}</dd>
    </div>
  )
}

// ---- Signed out: single sign-on only --------------------------------------------------
function OperatorSignIn({ error }: { error: string | null }) {
  const providers = useQuery({ queryKey: ["operator", "providers"], queryFn: operatorApi.providers })
  return (
    <div className="flex min-h-svh items-center justify-center px-4">
      <section className="w-full max-w-sm rounded-xl border border-border/70 bg-background p-6">
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <ShieldCheck className="size-5" /> Operator console
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          For the people who run this server. Sign in with your organisation's single sign-on.
        </p>
        {error && (
          <p role="alert" className="mt-4 text-sm text-destructive">
            {SIGN_IN_ERRORS[error] ?? "Sign-in didn't work. Please try again."}
          </p>
        )}
        <div className="mt-5 flex flex-col gap-2">
          {providers.isPending && <Skeleton className="h-8 w-full" />}
          {providers.data?.providers.map((p) => (
            <Button key={p.id} variant="outline" onClick={() => window.location.assign(p.url)}>
              Continue with {p.name}
            </Button>
          ))}
          {providers.data && !providers.data.providers.length && (
            <p className="text-sm text-muted-foreground">
              No single sign-on provider is set up for operators on this server.
            </p>
          )}
        </div>
      </section>
    </div>
  )
}

// ---- One workspace ---------------------------------------------------------------------
function WorkspaceRow({
  ws,
  onSuspend,
  onAudit,
  onSpendCaps,
  onExtendTrial,
  onBillingOwner,
  onClaimCode,
}: {
  ws: OperatorWorkspace
  onSuspend: (ws: OperatorWorkspace) => void
  onAudit: (ws: OperatorWorkspace) => void
  onSpendCaps: (ws: OperatorWorkspace) => void
  onExtendTrial: (ws: OperatorWorkspace) => void
  onBillingOwner: (ws: OperatorWorkspace) => void
  onClaimCode: (ws: OperatorWorkspace) => void
}) {
  const queryClient = useQueryClient()
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["operator", "workspaces"] })
  const act = useMutation({
    mutationFn: async (what: "unsuspend" | "reindex" | "limits" | "support") => {
      if (what === "unsuspend") return operatorApi.unsuspend(ws.slug).then(() => "Unsuspended.")
      if (what === "reindex") return operatorApi.reindex(ws.slug).then(() => "Re-indexing.")
      if (what === "limits")
        return operatorApi
          .resetRateLimits(ws.slug)
          .then((r) => `Rate limits reset (${r.counters} counters).`)
      const { code } = await operatorApi.supportSession(ws.slug)
      window.location.assign(`/#sso?code=${encodeURIComponent(code)}`)
      return "Opening the workspace as support…"
    },
    onSuccess: (message) => {
      toast.success(message)
      void refresh()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const supportOpen = !!ws.support_until && ws.status !== "suspended"

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium">{ws.name}</span>
            <StatusBadge ws={ws} />
            {supportOpen && (
              <Badge variant="outline" className="font-normal">
                <LifeBuoy /> Support allowed
              </Badge>
            )}
            {ws.open_alerts > 0 && (
              <Badge variant="destructive" className="font-normal">
                <AlertTriangle /> {ws.open_alerts} alert{ws.open_alerts === 1 ? "" : "s"}
              </Badge>
            )}
            {ws.connector_errors.length > 0 && (
              <Badge variant="destructive" className="font-normal">
                <AlertTriangle /> {ws.connector_errors.length} connector error
                {ws.connector_errors.length === 1 ? "" : "s"}
              </Badge>
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {ws.slug} · created {timeAgo(ws.created_at)}
            {ws.deleting_at
              ? ` · to be purged ${new Date(ws.deleting_at * 1000).toLocaleDateString()}`
              : ""}
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon" variant="ghost" className="size-8" aria-label={`Act on ${ws.slug}`}>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-52">
            {ws.status === "suspended" ? (
              <DropdownMenuItem onSelect={() => act.mutate("unsuspend")}>Unsuspend</DropdownMenuItem>
            ) : (
              <DropdownMenuItem onSelect={() => onSuspend(ws)}>Suspend…</DropdownMenuItem>
            )}
            <DropdownMenuItem onSelect={() => act.mutate("reindex")}>
              <RefreshCw /> Re-run index
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => act.mutate("limits")}>Reset rate limits</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onSpendCaps(ws)}>
              <Wallet /> Spend caps…
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onExtendTrial(ws)}>
              <Timer /> Extend trial…
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onBillingOwner(ws)}>
              <UserCog /> Billing owner…
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onClaimCode(ws)}>
              <KeyRound /> Claim code…
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onAudit(ws)}>
              <ScrollText /> View audit log
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled={!supportOpen} onSelect={() => act.mutate("support")}>
              <LifeBuoy /> Open as support
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 lg:grid-cols-7">
        <Stat
          label="Plan"
          value={
            <span className={cn(ws.plan && (ws.plan.trial_expired || ws.plan.status !== "active" && ws.plan.status !== "trialing") && "text-destructive")}>
              {planText(ws.plan)}
            </span>
          }
        />
        <Stat
          label="Seats (30 d)"
          value={`${ws.seats} of ${ws.members}${ws.plan?.seats_paid ? ` · ${ws.plan.seats_paid} paid` : ""}`}
        />
        <Stat
          label="Questions (30 d)"
          value={`${ws.usage.questions} · $${ws.usage.cost_usd.toFixed(2)}`}
        />
        <Stat
          label="Spend today"
          value={
            <span className={cn(ws.spend.hard_usd !== null && ws.spend.today_usd >= ws.spend.hard_usd && "text-destructive")}>
              ${ws.spend.today_usd.toFixed(2)}
              {ws.spend.hard_usd !== null ? ` of $${ws.spend.hard_usd.toFixed(2)}` : ""}
            </span>
          }
        />
        <Stat label="Last activity" value={timeAgo(ws.last_activity)} />
        <Stat
          label="Connectors"
          value={
            ws.connector_errors.length
              ? ws.connector_errors.map((e) => `${e.provider}: ${e.error ?? "error"}`).join(", ")
              : ws.connectors
                ? `${ws.connectors} ok`
                : "none"
          }
        />
        <Stat
          label="Index"
          value={
            <span className={cn(ws.index.status === "error" && "text-destructive")}>
              {ws.index.message || ws.index.status}
            </span>
          }
        />
      </dl>
    </div>
  )
}

// ---- Suspend, with a reason -------------------------------------------------------------
function SuspendDialog({ ws, onClose }: { ws: OperatorWorkspace | null; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState("")
  const suspend = useMutation({
    mutationFn: () => operatorApi.suspend(ws!.slug, reason.trim()),
    onSuccess: () => {
      toast.success(`${ws!.name} is suspended. Everyone in it is signed out.`)
      void queryClient.invalidateQueries({ queryKey: ["operator", "workspaces"] })
      onClose()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  return (
    <AlertDialog
      open={!!ws}
      onOpenChange={(open) => {
        if (!open) onClose()
        setReason("")
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Suspend {ws?.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            Everyone in it is signed out now and can't sign in; its API keys, SCIM and Slack stop
            working. Nothing is deleted, and unsuspending puts it back.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Field>
          <FieldLabel htmlFor="suspend-reason">Reason (in its audit log)</FieldLabel>
          <Input
            id="suspend-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. unpaid invoice, abuse report"
          />
        </Field>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={!reason.trim() || suspend.isPending}
            onClick={(e) => {
              e.preventDefault()
              suspend.mutate()
            }}
          >
            Suspend
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// ---- More trial (audited) ---------------------------------------------------------------
function ExtendTrialDialog({ ws, onClose }: { ws: OperatorWorkspace; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [days, setDays] = useState("14")
  const n = Number(days)
  const valid = Number.isInteger(n) && n >= 1 && n <= 90
  const extend = useMutation({
    mutationFn: () => operatorApi.extendTrial(ws.slug, n),
    onSuccess: (r) => {
      toast.success(
        `${ws.name}'s trial now ends on ${new Date(r.trial_ends_at * 1000).toLocaleDateString(undefined, { dateStyle: "medium" })}.`,
      )
      void queryClient.invalidateQueries({ queryKey: ["operator", "workspaces"] })
      onClose()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const ended = !!ws.plan?.trial_expired
  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Extend {ws.name}'s trial</AlertDialogTitle>
          <AlertDialogDescription>
            {ended
              ? "Its trial has ended: the days count from now, it stops being read-only, and the deletion the expired trial asked for is cancelled."
              : ws.plan?.status === "trialing"
                ? "The days are added to the end of its trial."
                : "It goes back on a trial for these days."}{" "}
            It's in the workspace's audit log.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Field data-invalid={!valid || undefined}>
          <FieldLabel htmlFor="extend-days">Days</FieldLabel>
          <Input
            id="extend-days"
            type="number"
            inputMode="numeric"
            min={1}
            max={90}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            aria-invalid={!valid || undefined}
          />
          <FieldDescription>From 1 to 90.</FieldDescription>
        </Field>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={!valid || extend.isPending}
            onClick={(e) => {
              e.preventDefault()
              extend.mutate()
            }}
          >
            Extend
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// ---- Who owns its billing (audited) -----------------------------------------------------
function BillingOwnerDialog({ ws, onClose }: { ws: OperatorWorkspace; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState("")
  const valid = /^[^@\s]+@[^@\s]+$/.test(email.trim())
  const add = useMutation({
    mutationFn: () => operatorApi.addBillingOwner(ws.slug, email.trim()),
    onSuccess: () => {
      toast.success(`${email.trim()} now owns ${ws.name}'s billing.`)
      void queryClient.invalidateQueries({ queryKey: ["operator", "workspaces"] })
      onClose()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const owners = ws.plan?.owners ?? []
  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{ws.name}'s billing owner</AlertDialogTitle>
          <AlertDialogDescription>
            Only a billing owner can pick a plan or change payment details. Make one of its
            members an owner when it has none (it was made before billing) or its owner left.
            It's in the workspace's audit log.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {owners.length > 0 && (
          <p className="text-sm text-muted-foreground">
            Owners now: <span className="text-foreground">{owners.join(", ")}</span>
          </p>
        )}
        <Field>
          <FieldLabel htmlFor="billing-owner">Member's email</FieldLabel>
          <Input
            id="billing-owner"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="someone@company.com"
          />
        </Field>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={!valid || add.isPending}
            onClick={(e) => {
              e.preventDefault()
              add.mutate()
            }}
          >
            Make owner
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// ---- A code to claim a workspace nobody can administer (audited) -----------------------
function ClaimCodeDialog({ ws, onClose }: { ws: OperatorWorkspace; onClose: () => void }) {
  const [domain, setDomain] = useState("")
  const [issued, setIssued] = useState<{ code: string; domain: string; expires_at: number } | null>(
    null,
  )
  const issue = useMutation({
    mutationFn: () => operatorApi.claimCode(ws.slug, domain.trim()),
    onSuccess: setIssued,
    onError: (err) => toast.error(errorText(err)),
  })
  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Claim code for {ws.name}</AlertDialogTitle>
          <AlertDialogDescription>
            For a workspace with no verified domain and no admin who can still sign in. Whoever
            you give it to opens /claim and signs in with a Google Workspace account on the
            domain: it's verified for the workspace and they become its admin. Check who's asking
            first. It works once, for 72 hours, and is in both audit logs (never the code).
          </AlertDialogDescription>
        </AlertDialogHeader>
        {issued ? (
          <div className="flex flex-col gap-2">
            <CopyField label={`Code for ${issued.domain}`} value={issued.code} />
            <p className="text-xs text-muted-foreground">
              Shown once. Expires {new Date(issued.expires_at * 1000).toLocaleString()}; any
              earlier code for this workspace stopped working.
            </p>
          </div>
        ) : (
          <Field>
            <FieldLabel htmlFor="claim-domain">Their Google Workspace domain</FieldLabel>
            <Input
              id="claim-domain"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="company.com"
              autoComplete="off"
            />
          </Field>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>{issued ? "Done" : "Cancel"}</AlertDialogCancel>
          {!issued && (
            <AlertDialogAction
              disabled={!domain.trim() || issue.isPending}
              onClick={(e) => {
                e.preventDefault()
                issue.mutate()
              }}
            >
              Issue code
            </AlertDialogAction>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// ---- A workspace's audit log --------------------------------------------------------
function AuditSheet({ ws, onClose }: { ws: OperatorWorkspace | null; onClose: () => void }) {
  const log = useQuery({
    queryKey: ["operator", "audit", ws?.slug],
    queryFn: () => operatorApi.audit(ws!.slug),
    enabled: !!ws,
  })
  return (
    <Sheet open={!!ws} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Audit log · {ws?.name}</SheetTitle>
          <SheetDescription>
            {log.data
              ? log.data.verify.ok
                ? `The hash chain checks out (${log.data.verify.count} events).`
                : "The hash chain does NOT check out."
              : "Newest first. Opening it is itself logged."}
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
          {log.isPending && <Skeleton className="h-40 w-full rounded-xl" />}
          <ul className="divide-y rounded-xl border">
            {log.data?.events.map((e) => (
              <li key={e.id} className="px-3 py-2">
                <p className="text-sm">
                  <span className="font-medium">{e.action}</span>{" "}
                  <span className="text-muted-foreground">
                    {e.target_type ? `${e.target_type}:${e.target_id}` : ""}
                  </span>
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {e.actor ?? "—"} ({e.actor_type}) · {timeAgo(e.at)}
                </p>
              </li>
            ))}
            {log.data && !log.data.events.length && (
              <li className="px-3 py-6 text-center text-sm text-muted-foreground">No events.</li>
            )}
          </ul>
        </div>
      </SheetContent>
    </Sheet>
  )
}

// ---- The console -----------------------------------------------------------------------
function Console({ onSignedOut }: { onSignedOut: () => void }) {
  const me = useQuery({ queryKey: ["operator", "me"], queryFn: operatorApi.me, retry: false })
  const list = useQuery({
    queryKey: ["operator", "workspaces"],
    queryFn: operatorApi.workspaces,
    retry: false,
  })
  const [query, setQuery] = useState("")
  const [suspending, setSuspending] = useState<OperatorWorkspace | null>(null)
  const [auditing, setAuditing] = useState<OperatorWorkspace | null>(null)
  const [capping, setCapping] = useState<OperatorWorkspace | null>(null)
  const [extending, setExtending] = useState<OperatorWorkspace | null>(null)
  const [owning, setOwning] = useState<OperatorWorkspace | null>(null)
  const [claiming, setClaiming] = useState<OperatorWorkspace | null>(null)

  useEffect(() => {
    if (me.isError || list.isError) onSignedOut()
  }, [me.isError, list.isError, onSignedOut])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (list.data?.workspaces ?? []).filter(
      (w) => !q || w.slug.includes(q) || w.name.toLowerCase().includes(q),
    )
  }, [list.data, query])
  const all = list.data?.workspaces ?? []

  async function signOut() {
    await operatorApi.logout().catch(() => null)
    operatorSession.set(null)
    onSignedOut()
  }

  return (
    <div className="min-h-svh">
      <header className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex h-12 max-w-6xl items-center gap-3 px-4">
          <span className="flex shrink-0 items-center gap-2 text-sm font-semibold tracking-tight">
            <ShieldCheck className="size-4" /> Operator console
          </span>
          <span className="ml-auto truncate text-xs text-muted-foreground">{me.data?.email}</span>
          <Button size="sm" variant="ghost" onClick={signOut}>
            <LogOut data-icon="inline-start" /> Sign out
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
              <Building2 className="size-6" /> Workspaces
            </h2>
            <p className="text-sm text-muted-foreground">
              {all.length} workspaces ·{" "}
              {all.filter((w) => w.status !== "active").length} suspended or being deleted. Every
              action is in the workspace's audit log.
            </p>
          </div>
          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="Search workspaces"
              placeholder="Search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full pl-8"
            />
          </div>
        </div>
        <div className="mt-6">
          <AlertsPanel />
        </div>
        <section className="mt-6 divide-y rounded-2xl border border-border/70 bg-background">
          {list.isPending &&
            [0, 1, 2].map((i) => (
              <div key={i} className="p-4">
                <Skeleton className="h-16 w-full" />
              </div>
            ))}
          {shown.map((ws) => (
            <WorkspaceRow
              key={ws.slug}
              ws={ws}
              onSuspend={setSuspending}
              onAudit={setAuditing}
              onSpendCaps={setCapping}
              onExtendTrial={setExtending}
              onBillingOwner={setOwning}
              onClaimCode={setClaiming}
            />
          ))}
          {list.data && !shown.length && (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">
              {all.length ? "Nothing matches." : "No workspaces yet."}
            </p>
          )}
        </section>
      </main>
      <SuspendDialog ws={suspending} onClose={() => setSuspending(null)} />
      <AuditSheet ws={auditing} onClose={() => setAuditing(null)} />
      {capping && (
        <SpendCapsDialog key={capping.slug} ws={capping} onClose={() => setCapping(null)} />
      )}
      {extending && (
        <ExtendTrialDialog key={extending.slug} ws={extending} onClose={() => setExtending(null)} />
      )}
      {owning && (
        <BillingOwnerDialog key={owning.slug} ws={owning} onClose={() => setOwning(null)} />
      )}
      {claiming && (
        <ClaimCodeDialog key={claiming.slug} ws={claiming} onClose={() => setClaiming(null)} />
      )}
    </div>
  )
}

// /operator: sign in (single sign-on, back here with #code=... or #error=...), then the
// console. Its session is the operator's own, never a member's.
export function OperatorView() {
  const queryClient = useQueryClient()
  // Back from single sign-on: #code=... to swap for a session, or #error=...
  const [back] = useState(() => new URLSearchParams(window.location.hash.slice(1)))
  const [token, setToken] = useState(operatorSession.token)
  const [error, setError] = useState<string | null>(() => back.get("error"))
  const exchanged = useRef(false)

  useEffect(() => {
    if (exchanged.current) return // once, even when StrictMode runs the effect twice
    exchanged.current = true
    const code = back.get("code")
    if (code || back.get("error")) window.history.replaceState(null, "", "/operator")
    if (code)
      operatorApi
        .exchange(code)
        .then(({ token }) => {
          operatorSession.set(token)
          setToken(token)
        })
        .catch(() => setError("sso_failed"))
  }, [back])

  if (!token) return <OperatorSignIn error={error} />
  return (
    <Console
      onSignedOut={() => {
        operatorSession.set(null)
        queryClient.removeQueries({ queryKey: ["operator"] })
        setToken(null)
      }}
    />
  )
}
