import { useCallback, useEffect, useRef, useState, type FormEvent } from "react"
import {
  CheckCircle2,
  ChevronDown,
  Globe,
  KeyRound,
  Loader2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { MembersView } from "@/components/members-view"
import { GroupRulesDialog } from "@/components/sign-in-group-rules"
import { CopyField, ProviderDialog } from "@/components/sign-in-provider-dialog"
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
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
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
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  signIn,
  type ProviderHealth,
  type SignInConnection,
  type SignInDomain,
  type SignInEvent,
  type SignInSettings,
  type SsoTestResult,
} from "@/lib/api"
import { ROLE_LABEL } from "@/lib/roles"
import { cn, timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// Why a sign-in failed or was refused, for the admin. The server explains each
// code (sso_errors.py); an event logged before codes just has its old text.
const explain = (x: { reason?: string | null; reason_text?: string | null }) =>
  x.reason_text ?? x.reason ?? ""

// "10:42" today, "12 Mar, 10:42" before then.
function clockTime(epoch: number) {
  const d = new Date(epoch * 1000)
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  if (d.toDateString() === new Date().toDateString()) return time
  return `${d.toLocaleDateString([], { day: "numeric", month: "short" })}, ${time}`
}

const PROTOCOL = { oidc: "OpenID Connect", saml: "SAML" } as const

function Section({
  title,
  description,
  action,
  children,
}: {
  title: string
  description: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-medium">{title}</h3>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        {action}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  )
}

function OnBadge({ on, onLabel, offLabel }: { on: boolean; onLabel: string; offLabel: string }) {
  return on ? (
    <Badge className="gap-1.5 bg-emerald-600/10 text-emerald-700 hover:bg-emerald-600/10 dark:text-emerald-400">
      <span className="size-1.5 rounded-full bg-emerald-500" /> {onLabel}
    </Badge>
  ) : (
    <Badge variant="outline" className="font-normal text-muted-foreground">
      {offLabel}
    </Badge>
  )
}

// ---- Require single sign-on ----------------------------------------------------------
function SignInRules({ settings, onChange }: { settings: SignInSettings; onChange: () => void }) {
  const [saving, setSaving] = useState(false)

  async function toggle(on: boolean) {
    setSaving(true)
    try {
      await signIn.setPolicy(on)
      toast.success(
        on
          ? "Single sign-on is required. Only admins can still use a password."
          : "People can sign in with a password again.",
      )
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  if (!settings.password_login)
    return (
      <p className="text-sm text-muted-foreground">
        Passwords are turned off on this server, so everyone already signs in with single
        sign-on.
      </p>
    )
  return (
    <div className="flex items-start gap-3">
      <Checkbox
        id="require-sso"
        className="mt-0.5"
        checked={settings.require_sso}
        disabled={saving}
        onCheckedChange={(v) => toggle(v === true)}
      />
      <div className="grid gap-1">
        <Label htmlFor="require-sso">Require single sign-on</Label>
        <p className="text-sm text-muted-foreground">
          Nobody but admins can sign in with a password, and anyone signed in with one is
          signed out. Admins keep theirs as a way in if your provider ever breaks.
        </p>
      </div>
    </div>
  )
}

// ---- Domains -----------------------------------------------------------------------
function DomainRow({ domain, onChange }: { domain: SignInDomain; onChange: () => void }) {
  const [checking, setChecking] = useState(false)
  const [confirm, setConfirm] = useState(false)

  async function check() {
    setChecking(true)
    try {
      const d = await signIn.verifyDomain(domain.domain)
      if (d.verified) toast.success(`${domain.domain} is verified.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setChecking(false)
    }
  }

  async function remove() {
    try {
      await signIn.removeDomain(domain.domain)
      toast.success(`Removed ${domain.domain}.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div className="rounded-xl border p-4">
      <div className="flex flex-wrap items-center gap-3">
        <Globe className="size-4 text-muted-foreground" />
        <span className="font-medium">{domain.domain}</span>
        <OnBadge on={domain.verified} onLabel="Verified" offLabel="Waiting for DNS" />
        <div className="ml-auto flex gap-2">
          {!domain.verified && (
            <Button size="sm" onClick={check} disabled={checking}>
              {checking && <Loader2 className="animate-spin" />}
              Check DNS
            </Button>
          )}
          <Button size="sm" variant="outline" className="text-destructive" onClick={() => setConfirm(true)}>
            Remove
          </Button>
        </div>
      </div>
      {!domain.verified && domain.txt_name && domain.txt_value && (
        <div className="mt-3 grid grid-cols-1 gap-2 rounded-lg bg-muted/40 p-3">
          <p className="text-sm">
            Add this <span className="font-medium">TXT record</span> at your DNS provider, then
            check it. DNS changes can take a few minutes to appear.
          </p>
          <CopyField label="Name (host)" value={domain.txt_name} />
          <CopyField label="Value" value={domain.txt_value} />
        </div>
      )}
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {domain.domain}?</AlertDialogTitle>
            <AlertDialogDescription>
              {domain.verified
                ? `People with an @${domain.domain} address can no longer sign in, and anyone signed in with one is signed out on their next click.`
                : "The domain was never verified, so nobody signs in with it yet."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={remove}>Remove</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function AddDomain({ onAdded }: { onAdded: () => void }) {
  const [domain, setDomain] = useState("")
  const [adding, setAdding] = useState(false)

  async function add(e: FormEvent) {
    e.preventDefault()
    setAdding(true)
    try {
      await signIn.addDomain(domain)
      setDomain("")
      onAdded()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setAdding(false)
    }
  }

  return (
    <form onSubmit={add} className="flex gap-2">
      <Input
        id="new-domain"
        aria-label="Domain"
        placeholder="example.com"
        value={domain}
        onChange={(e) => setDomain(e.target.value)}
        className="max-w-xs"
        required
      />
      <Button type="submit" variant="outline" disabled={adding}>
        {adding ? <Loader2 className="animate-spin" /> : <Plus />}
        Add domain
      </Button>
    </form>
  )
}

// ---- Providers ---------------------------------------------------------------------
function ProviderRow({
  conn,
  onEdit,
  onTest,
  onRoles,
  onChange,
}: {
  conn: SignInConnection
  onEdit: () => void
  onTest: () => void
  onRoles: () => void
  onChange: () => void
}) {
  const [confirm, setConfirm] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  // A SAML provider added by URL: fetch its metadata (and new certificate) again.
  async function refreshMetadata() {
    setRefreshing(true)
    try {
      await signIn.editSaml(conn.id, { metadata_url: conn.metadata_url! })
      toast.success(`Refreshed ${conn.label}'s metadata. Run Test sign-in to check it.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setRefreshing(false)
    }
  }

  async function toggle() {
    try {
      await signIn.setEnabled(conn, !conn.enabled)
      toast.success(conn.enabled ? `${conn.label} is off.` : `${conn.label} is on.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function remove() {
    try {
      await signIn.remove(conn.id)
      toast.success(`Deleted ${conn.label}.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div className="rounded-xl border p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
          <KeyRound className="size-4" />
        </div>
        {/* At least 14rem for the name; on a phone the buttons go below it. */}
        <div className="min-w-0 flex-1 basis-56">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{conn.label}</span>
            <Badge variant="secondary" className="font-normal">
              {PROTOCOL[conn.protocol]}
            </Badge>
            <OnBadge on={conn.enabled} onLabel="On" offLabel="Off" />
            {conn.health.status === "failing" && (
              <Badge variant="destructive" className="gap-1.5">
                <span className="size-1.5 rounded-full bg-destructive" /> Failing
              </Badge>
            )}
          </div>
          <p className="truncate text-sm text-muted-foreground">
            {conn.issuer} · last sign-in {timeAgo(conn.last_login_at)}
          </p>
          <p className="text-xs text-muted-foreground">
            {conn.group_rules.length
              ? `Roles from groups: ${conn.group_rules
                  .map((r) => `${r.group} → ${ROLE_LABEL[r.role]}`)
                  .join(", ")}`
              : "Doesn't set roles"}
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={onTest}>
            Test sign-in
          </Button>
          {!conn.enabled && (
            <Button size="sm" onClick={toggle}>
              Turn on
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="icon" variant="ghost" className="size-8" aria-label={`More for ${conn.label}`}>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onEdit}>Edit</DropdownMenuItem>
              <DropdownMenuItem onSelect={onRoles}>Roles from groups</DropdownMenuItem>
              {conn.enabled && <DropdownMenuItem onSelect={toggle}>Turn off</DropdownMenuItem>}
              <DropdownMenuSeparator />
              <DropdownMenuItem className="text-destructive" onSelect={() => setConfirm(true)}>
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      {conn.health.status === "failing" && (
        <HealthAlert
          health={conn.health}
          onRefresh={
            conn.health.code === "saml_signature" && conn.metadata_url ? refreshMetadata : undefined
          }
          refreshing={refreshing}
        />
      )}
      <Collapsible>
        <CollapsibleTrigger className="group mt-3 flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ChevronDown className="size-4 transition-transform group-data-[state=open]:rotate-180" />
          Setup details
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-3 grid grid-cols-1 gap-3">
          {conn.protocol === "oidc" ? (
            <>
              <CopyField label="Redirect URI (at your provider)" value={conn.redirect_uri!} />
              <CopyField label="Client ID" value={conn.client_id!} />
              {conn.logout_at_idp && (
                <CopyField
                  label="Sign-out redirect URI (at your provider)"
                  value={conn.post_logout_redirect_uri!}
                />
              )}
            </>
          ) : (
            <>
              <CopyField label="Entity ID (at your provider)" value={conn.sp_entity_id!} />
              <CopyField label="ACS URL (at your provider)" value={conn.acs_url!} />
              <CopyField label="SP metadata URL" value={conn.sp_metadata_url!} />
              <CopyField label="Your provider's sign-in URL" value={conn.sso_url!} />
              <p className="text-xs text-muted-foreground">
                {conn.signs_requests
                  ? "Your provider asks for signed sign-in requests, so they're signed."
                  : "Sign-in requests aren't signed: your provider doesn't ask for it."}
                {conn.email_attribute ? ` Email comes from "${conn.email_attribute}".` : ""}
              </p>
            </>
          )}
        </CollapsibleContent>
      </Collapsible>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {conn.label}?</AlertDialogTitle>
            <AlertDialogDescription>
              Nobody can sign in with it any more, and everyone signed in through it is signed
              out on their next click. To pause it instead, turn it off.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={remove}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// Its latest sign-ins all failed: since when, why, and what to do. Clears on the
// next sign-in that works.
function HealthAlert({
  health,
  onRefresh,
  refreshing,
}: {
  health: Extract<ProviderHealth, { status: "failing" }>
  onRefresh?: () => void
  refreshing: boolean
}) {
  return (
    <Alert variant="destructive" className="mt-3 border-destructive/30 bg-destructive/5">
      <TriangleAlert />
      <AlertTitle>
        Failing since {clockTime(health.since)} — {health.reason} ({health.count}{" "}
        {health.count === 1 ? "attempt" : "attempts"})
      </AlertTitle>
      <AlertDescription className="text-destructive/90">
        {health.fix && <p>{health.fix}</p>}
        {onRefresh && (
          <Button
            size="sm"
            variant="outline"
            className="mt-2 text-foreground"
            onClick={onRefresh}
            disabled={refreshing}
          >
            {refreshing ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Refresh metadata
          </Button>
        )}
      </AlertDescription>
    </Alert>
  )
}

// ---- Test sign-in: a popup signs in at the provider; the result comes back here ------
type TestState =
  | { phase: "waiting" }
  | { phase: "done"; result: SsoTestResult }
  | { phase: "error"; message: string }

const POLL_MS = 1500

const POLL_ERRORS_TOLERATED = 3           // a network blip doesn't end the test

function TestDialog({
  conn,
  popup,
  onClose,
}: {
  conn: SignInConnection | null
  popup: Window | null // opened by the click, so pop-up blockers let it through
  onClose: () => void
}) {
  // A new `key` per test (see SignInView), so each one starts out waiting.
  const [state, setState] = useState<TestState>({ phase: "waiting" })
  // One ticket per test, even when StrictMode runs the effect twice.
  const ticket = useRef<Promise<{ ticket: string; url: string }> | null>(null)

  useEffect(() => {
    if (!conn) return
    let stopped = false
    let timer: number | undefined
    ticket.current ??= signIn.startTest(conn.id)

    const poll = (id: string, closedChecks: number, errors: number) => {
      timer = window.setTimeout(async () => {
        if (stopped) return
        try {
          const { done, result } = await signIn.testResult(id)
          if (stopped) return
          if (done && result) {
            setState({ phase: "done", result })
            return
          }
          // Closed without finishing? Give the last answer a moment to arrive.
          const closed = popup?.closed ?? false
          if (closed && closedChecks >= 2) {
            setState({
              phase: "error",
              message: "The test window was closed before the sign-in finished.",
            })
            return
          }
          poll(id, closed ? closedChecks + 1 : 0, 0)
        } catch (err) {
          if (stopped) return
          if (errors + 1 < POLL_ERRORS_TOLERATED) poll(id, closedChecks, errors + 1)
          else setState({ phase: "error", message: errorText(err) })
        }
      }, POLL_MS)
    }

    ticket.current
      .then(({ ticket: id, url }) => {
        if (stopped) return
        if (!popup || popup.closed) {
          setState({
            phase: "error",
            message:
              "Your browser blocked the test window. Allow pop-ups for this site and try again.",
          })
          return
        }
        popup.location.href = url
        poll(id, 0, 0)
      })
      .catch((err) => {
        if (stopped) return
        popup?.close()
        setState({ phase: "error", message: errorText(err) })
      })
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [conn, popup])

  function close() {
    if (popup && !popup.closed) popup.close()
    onClose()
  }

  const sent = state.phase === "done" ? Object.entries(state.result.sent) : []

  return (
    <Dialog open={!!conn} onOpenChange={(open) => !open && close()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Test sign-in: {conn?.label}</DialogTitle>
          <DialogDescription>
            Sign in at your provider in the window that opened. Nobody is signed in here and no
            account is added.
          </DialogDescription>
        </DialogHeader>

        {state.phase === "waiting" && (
          <div className="flex items-center gap-3 rounded-lg bg-muted/40 p-4 text-sm">
            <Loader2 className="size-4 animate-spin" />
            Waiting for you to sign in in the test window…
          </div>
        )}
        {state.phase === "error" && (
          <div className="flex items-start gap-3 rounded-lg bg-destructive/10 p-4 text-sm text-destructive">
            <XCircle className="mt-0.5 size-4 shrink-0" />
            {state.message}
          </div>
        )}
        {state.phase === "done" && (
          <>
            {state.result.ok ? (
              <div className="flex items-start gap-3 rounded-lg bg-emerald-600/10 p-4 text-sm text-emerald-800 dark:text-emerald-300">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                <p>
                  <span className="font-medium">{state.result.name}</span> ({state.result.email}) would
                  sign in as <span className="font-medium">{ROLE_LABEL[state.result.role ?? "public"]}</span>
                  {state.result.role_source === "groups" && " (from their groups)"}
                  {state.result.role_source === "override" && " (set by an admin)"}
                  {state.result.new_member ? ", as a new member." : "."}
                  {state.result.groups && (
                    <span className="mt-1 block text-xs">
                      Groups found: {state.result.groups.length ? state.result.groups.join(", ") : "none"}
                    </span>
                  )}
                </p>
              </div>
            ) : (
              <div className="flex items-start gap-3 rounded-lg bg-destructive/10 p-4 text-sm text-destructive">
                <XCircle className="mt-0.5 size-4 shrink-0" />
                <div>
                  <p>
                    {state.result.email ? `${state.result.email} would be turned away. ` : "It didn't work. "}
                    {state.result.reason === "not_allowed" && state.result.detail
                      ? state.result.detail
                      : explain(state.result)}
                  </p>
                  {state.result.fix && state.result.reason !== "not_allowed" && (
                    <p className="mt-1">{state.result.fix}</p>
                  )}
                  {(state.result.reason || state.result.detail) && state.result.reason !== "not_allowed" && (
                    <p className="mt-1 font-mono text-xs break-all">
                      {[state.result.reason, state.result.detail].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
              </div>
            )}
            {sent.length > 0 && (
              <div className="grid grid-cols-1 gap-2">
                <p className="text-sm font-medium">What the provider sent</p>
                <dl className="grid grid-cols-[minmax(0,10rem)_1fr] gap-x-3 gap-y-1 rounded-lg border p-3 text-xs">
                  {sent.map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt className="truncate text-muted-foreground" title={key}>
                        {key}
                      </dt>
                      <dd className="font-mono break-all">{value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={close}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---- Recent sign-ins -----------------------------------------------------------------
const OUTCOME: Record<SignInEvent["outcome"], { label: string; className: string }> = {
  signed_in: { label: "Signed in", className: "text-emerald-700 dark:text-emerald-400" },
  refused: { label: "Turned away", className: "text-destructive" },
  failed: { label: "Failed", className: "text-destructive" },
  test: { label: "Test", className: "text-muted-foreground" },
}

function Events({ events }: { events: SignInEvent[] }) {
  if (!events.length)
    return (
      <p className="text-sm text-muted-foreground">
        Nobody has signed in through your providers yet.
      </p>
    )
  return (
    <div className="divide-y rounded-xl border">
      {events.map((e, i) => (
        <div key={i} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-4 py-2.5 text-sm">
          <span className={cn("w-24 shrink-0 font-medium", OUTCOME[e.outcome].className)}>
            {OUTCOME[e.outcome].label}
          </span>
          <span className="min-w-0 truncate">{e.email ?? "unknown"}</span>
          <span className="text-muted-foreground">via {e.connection ?? "a deleted provider"}</span>
          <span className="ml-auto text-xs text-muted-foreground">{timeAgo(e.at)}</span>
          {(e.reason || e.ref) && (
            <div className="basis-full text-xs break-words text-muted-foreground sm:pl-27">
              <p>
                {explain(e)}
                {e.ref && (
                  <span className="ml-2 font-mono whitespace-nowrap select-all">Ref {e.ref}</span>
                )}
              </p>
              {e.fix && e.outcome === "failed" && <p className="mt-0.5">{e.fix}</p>}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ---- The screen ----------------------------------------------------------------------
export function SignInView({ workspace, me }: { workspace: string; me: string }) {
  const [tab, setTab] = useState<"sign-in" | "members">("sign-in")

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <ShieldCheck className="size-6" /> Access
          </h2>
          <p className="text-sm text-muted-foreground">
            Who can sign in to the <span className="font-medium text-foreground">{workspace}</span>{" "}
            workspace, and as what.
          </p>
        </div>
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList>
            <TabsTrigger value="sign-in">Sign-in</TabsTrigger>
            <TabsTrigger value="members">Members</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div className="mt-6">{tab === "sign-in" ? <SignInSettingsView /> : <MembersView me={me} />}</div>
    </div>
  )
}

function SignInSettingsView() {
  const [settings, setSettings] = useState<SignInSettings | null>(null)
  const [editing, setEditing] = useState<SignInConnection | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [testing, setTesting] = useState<SignInConnection | null>(null)
  const [testWindow, setTestWindow] = useState<Window | null>(null)
  const [roles, setRoles] = useState<SignInConnection | null>(null)
  const [opened, setOpened] = useState(0)     // remounts the dialogs, fresh, on each open

  const load = useCallback(() => {
    signIn
      .get()
      .then(setSettings)
      .catch((err) => toast.error(errorText(err)))
  }, [])

  useEffect(load, [load])

  function openDialog(conn: SignInConnection | null) {
    setEditing(conn)
    setOpened((n) => n + 1)
    setDialogOpen(true)
  }

  function openRoles(conn: SignInConnection) {
    setOpened((n) => n + 1)
    setRoles(conn)
  }

  function openTest(conn: SignInConnection) {
    // Open the window here, in the click: browsers only allow pop-ups from one.
    setTestWindow(window.open("about:blank", "kb-sso-test", "width=520,height=720"))
    setOpened((n) => n + 1)
    setTesting(conn)
  }

  function saved(conn: SignInConnection, added: boolean) {
    setDialogOpen(false)
    toast.success(
      added
        ? `Added ${conn.label}. It's off: test it, then turn it on.`
        : `Saved ${conn.label}.`,
    )
    load()
  }

  function closeTest() {
    setTesting(null)
    load()                  // the test shows up in recent sign-ins
  }

  const verified = settings?.domains.some((d) => d.verified) ?? false

  return (
    <div>
      {!settings ? (
        <div className="grid grid-cols-1 gap-4">
          <Skeleton className="h-36 rounded-2xl" />
          <Skeleton className="h-48 rounded-2xl" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6">
          <Section title="Sign-in rules" description="How people in this workspace sign in.">
            <SignInRules settings={settings} onChange={load} />
          </Section>

          <Section
            title="Email domains"
            description="People sign in only with an email on a domain you've proved you own."
          >
            <div className="grid grid-cols-1 gap-3">
              {settings.domains.map((d) => (
                <DomainRow key={d.domain} domain={d} onChange={load} />
              ))}
              <AddDomain onAdded={load} />
            </div>
          </Section>

          <Section
            title="Identity providers"
            description="People pick one after typing their work email on the sign-in page."
            action={
              <Button size="sm" onClick={() => openDialog(null)}>
                <Plus /> Add provider
              </Button>
            }
          >
            <div className="grid grid-cols-1 gap-3">
              {!verified && (
                <p className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-300">
                  Verify a domain first: providers only sign in people with an email on one.
                </p>
              )}
              {settings.connections.map((c) => (
                <ProviderRow
                  key={c.id}
                  conn={c}
                  onEdit={() => openDialog(c)}
                  onTest={() => openTest(c)}
                  onRoles={() => openRoles(c)}
                  onChange={load}
                />
              ))}
              {!settings.connections.length && (
                <p className="text-sm text-muted-foreground">
                  No provider yet. Add your Okta, Entra ID, Google Workspace or any other OpenID
                  Connect or SAML provider.
                </p>
              )}
            </div>
          </Section>

          <Section title="Recent sign-ins" description="Through your providers, newest first.">
            <Events events={settings.events} />
          </Section>
        </div>
      )}

      <ProviderDialog
        key={`provider-${opened}`}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        editing={editing}
        onSaved={saved}
      />
      <TestDialog key={`test-${opened}`} conn={testing} popup={testWindow} onClose={closeTest} />
      <GroupRulesDialog
        key={`roles-${opened}`}
        conn={roles}
        onOpenChange={(open) => !open && setRoles(null)}
        onSaved={() => {
          setRoles(null)
          load()
        }}
      />
    </div>
  )
}
