import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  Building2,
  LifeBuoy,
  LogOut,
  MoreHorizontal,
  RefreshCw,
  ScrollText,
  Search,
  ShieldCheck,
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
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
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
}: {
  ws: OperatorWorkspace
  onSuspend: (ws: OperatorWorkspace) => void
  onAudit: (ws: OperatorWorkspace) => void
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
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Plan" value={ws.plan ?? "—"} />
        <Stat label="Seats (30 d)" value={`${ws.seats} of ${ws.members}`} />
        <Stat
          label="Questions (30 d)"
          value={`${ws.usage.questions} · $${ws.usage.cost_usd.toFixed(2)}`}
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
        <section className="mt-6 divide-y rounded-2xl border border-border/70 bg-background">
          {list.isPending &&
            [0, 1, 2].map((i) => (
              <div key={i} className="p-4">
                <Skeleton className="h-16 w-full" />
              </div>
            ))}
          {shown.map((ws) => (
            <WorkspaceRow key={ws.slug} ws={ws} onSuspend={setSuspending} onAudit={setAuditing} />
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
