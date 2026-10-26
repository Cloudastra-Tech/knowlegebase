import { useCallback, useEffect, useRef, useState, type FormEvent } from "react"
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  Circle,
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
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
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

const DEFAULT_ADMIN_DAYS = 30 // what turning expiry on starts with (members_store.py)

function GroupAdminExpiry({
  settings,
  onChange,
}: {
  settings: SignInSettings
  onChange: () => void
}) {
  const on = settings.group_admin_days > 0
  const [days, setDays] = useState(String(on ? settings.group_admin_days : DEFAULT_ADMIN_DAYS))
  const [saving, setSaving] = useState(false)
  const n = Number(days)
  const valid = days.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= 3650

  async function apply(value: number) {
    setSaving(true)
    try {
      await signIn.setGroupAdminDays(value)
      toast.success(
        value
          ? `Admin from groups now lasts ${value} days without a sign-in through the role source.`
          : "Admin from groups no longer expires.",
      )
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  function save(e: FormEvent) {
    e.preventDefault()
    if (valid) apply(n)
  }

  return (
    <form onSubmit={save} className="flex items-start gap-3">
      <Switch
        id="group-admin-expiry"
        className="mt-0.5"
        checked={on}
        disabled={saving}
        onCheckedChange={(v) => apply(v ? (valid ? n : DEFAULT_ADMIN_DAYS) : 0)}
      />
      <div className="grid gap-1">
        <Label htmlFor="group-admin-expiry">Expire admin from groups</Label>
        <p className="text-sm text-muted-foreground">
          Their groups are only checked when they sign in, so admin from groups that haven't been
          sent in a while drops to what the rest of their groups give. Roles you set on the
          Members tab don't expire, and the last admin always keeps it.
        </p>
        {on && (
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
            <span>After</span>
            <Input
              id="group-admin-days"
              aria-label="Days"
              type="number"
              inputMode="numeric"
              min={1}
              max={3650}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              className="w-20"
            />
            <span>days without a sign-in through the role source</span>
            {valid && n !== settings.group_admin_days && (
              <Button type="submit" size="sm" variant="outline" disabled={saving}>
                {saving && <Loader2 className="animate-spin" />}
                Save
              </Button>
            )}
          </div>
        )}
      </div>
    </form>
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
// "kb-admins → Admin, …"
const ruleText = (conn: SignInConnection) =>
  conn.group_rules.map((r) => `${r.group} → ${ROLE_LABEL[r.role]}`).join(", ")
const AMBER = "bg-amber-500/10 text-amber-800 dark:text-amber-300"
const CERT_WARN_DAYS = 30 // red from 7
const daysUntil = (epoch: number) => Math.floor((epoch - Date.now() / 1000) / 86400)

// A badge with a tooltip; focusable, so the tooltip opens from the keyboard too.
function HintBadge({
  className,
  hint,
  children,
}: {
  className: string
  hint: string
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge tabIndex={0} className={cn("gap-1 font-normal", className)}>
          {children}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  )
}

// SAML: the IdP's certificate runs out within CERT_WARN_DAYS.
function CertificateBadge({ conn }: { conn: SignInConnection }) {
  if (!conn.cert_expires_at) return null
  const days = daysUntil(conn.cert_expires_at)
  if (days >= CERT_WARN_DAYS) return null
  const label =
    days < 0
      ? "Certificate expired"
      : days === 0
        ? "Certificate expires today"
        : `Certificate expires in ${days} d`
  const hint = conn.metadata_url
    ? "Its metadata URL is checked daily, so the new certificate is picked up as soon as your provider publishes it."
    : "Its metadata was pasted or typed in, so it can't update by itself: once your provider has the new certificate, give it here (Edit)."
  return (
    <HintBadge className={days < 7 ? "bg-destructive/10 text-destructive" : AMBER} hint={hint}>
      <AlertTriangle /> {label}
    </HintBadge>
  )
}

function ProviderRow({
  conn,
  roleSource,
  onEdit,
  onTest,
  onRoles,
  onChange,
}: {
  conn: SignInConnection
  roleSource: SignInConnection | null
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
  const isSource = roleSource?.id === conn.id

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
            {isSource && (
              <Badge variant="secondary" className="font-normal">
                Role source
              </Badge>
            )}
            {conn.needs_review && (
              <HintBadge
                className={AMBER}
                hint={`New metadata from your provider wasn't applied: ${conn.review_reason}. If that's right, load it again (Edit → Metadata URL).`}
              >
                <AlertTriangle /> Needs review
              </HintBadge>
            )}
            <CertificateBadge conn={conn} />
          </div>
          <p className="truncate text-sm text-muted-foreground">
            {conn.issuer} · last sign-in {timeAgo(conn.last_login_at)}
          </p>
          <p className="text-xs text-muted-foreground">
            {isSource
              ? `Sets roles (role source). Roles from groups: ${ruleText(conn)}`
              : !conn.group_rules.length
                ? "Doesn't set roles"
                : roleSource
                  ? `Doesn't set roles: they come from ${roleSource.label}, so its rules (${ruleText(conn)}) aren't used`
                  : `Doesn't set roles until you save its rules again (${ruleText(conn)})`}
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
              {conn.metadata_url && (
                <p className="text-xs break-all text-muted-foreground">
                  {conn.metadata_refreshed_at
                    ? `Metadata refreshed ${timeAgo(conn.metadata_refreshed_at)}`
                    : "Metadata not refreshed yet"}{" "}
                  from {conn.metadata_url}. It's checked daily, and again if a signature doesn't
                  match, so a new certificate is picked up by itself.
                </p>
              )}
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
              out on their next click.
              {isSource &&
                " It's your role source, so the roles its groups gave are gone too, and no provider sets roles until you give one rules."}{" "}
              To pause it instead, turn it off.
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
                  {state.result.sets_roles === false &&
                    " This provider doesn't set roles: signing in through it leaves theirs as it is."}
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
  warning: { label: "Warning", className: "text-amber-700 dark:text-amber-400" },
  updated: { label: "Updated", className: "text-muted-foreground" },
  attention: { label: "Needs a look", className: "text-amber-700 dark:text-amber-400" },
}
// A provider's own metadata changed (or couldn't be fetched): nobody signed in.
const ABOUT_PROVIDER: SignInEvent["outcome"][] = ["updated", "attention"]

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
          {!ABOUT_PROVIDER.includes(e.outcome) && (
            <span className="min-w-0 truncate">{e.email ?? "unknown"}</span>
          )}
          <span className="text-muted-foreground">
            {ABOUT_PROVIDER.includes(e.outcome) ? "" : "via "}
            {e.connection ?? "a deleted provider"}
          </span>
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
export function SignInView({
  workspace,
  me,
  onOpenIntegrations,
}: {
  workspace: string
  me: string
  onOpenIntegrations?: () => void
}) {
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
      <div className="mt-6">
        {tab === "sign-in" ? (
          <SignInSettingsView workspace={workspace} onOpenIntegrations={onOpenIntegrations} />
        ) : (
          <MembersView me={me} />
        )}
      </div>
    </div>
  )
}

// ---- Getting started ---------------------------------------------------------------
// A new workspace (made at sign-up) has a verified domain and one admin. Until it
// has a provider of its own, show what's left; it can also be put away.
function GettingStarted({
  settings,
  workspace,
  onAddProvider,
  onOpenIntegrations,
}: {
  settings: SignInSettings
  workspace: string
  onAddProvider: () => void
  onOpenIntegrations?: () => void
}) {
  const key = `kb-setup-done:${workspace}`
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(key) === "1"
    } catch {
      return false
    }
  })
  const domains = settings.domains.filter((d) => d.verified).map((d) => d.domain)
  if (hidden || settings.connections.length) return null

  function hide() {
    try {
      localStorage.setItem(key, "1")
    } catch {
      // private window: it just comes back next time
    }
    setHidden(true)
  }

  const steps: { done: boolean; title: string; body: string; action?: React.ReactNode }[] = [
    {
      done: domains.length > 0,
      title: domains.length ? `${domains.join(", ")} is verified` : "Verify your email domain",
      body: domains.length
        ? "Colleagues with an email on it can sign in with Continue with Google Workspace now."
        : "Add it under Email domains below, then add the DNS record it shows.",
    },
    {
      done: false,
      title: "Add your company's identity provider",
      body: "Optional. If you use Okta, Microsoft Entra ID or any SAML provider, add it so access follows your directory.",
      action: (
        <Button size="sm" variant="outline" onClick={onAddProvider}>
          <Plus data-icon="inline-start" /> Add provider
        </Button>
      ),
    },
    {
      done: false,
      title: "Connect your documents",
      body: "Google Drive, Notion, or files you upload: what your colleagues' questions are answered from.",
      action: onOpenIntegrations && (
        <Button size="sm" variant="outline" onClick={onOpenIntegrations}>
          Open Integrations <ArrowRight data-icon="inline-end" />
        </Button>
      ),
    },
  ]

  return (
    <Section
      title="Set up your workspace"
      description="Your workspace is ready. Three things make it useful for everyone."
      action={
        <Button size="sm" variant="ghost" onClick={hide}>
          Done
        </Button>
      }
    >
      <ol className="flex flex-col gap-4">
        {steps.map((s) => (
          <li key={s.title} className="flex flex-wrap items-start gap-3">
            {s.done ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-label="Done" />
            ) : (
              <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="To do" />
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{s.title}</p>
              <p className="text-sm text-muted-foreground">{s.body}</p>
            </div>
            {s.action}
          </li>
        ))}
      </ol>
    </Section>
  )
}

function SignInSettingsView({
  workspace,
  onOpenIntegrations,
}: {
  workspace: string
  onOpenIntegrations?: () => void
}) {
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
  const roleSource = settings?.connections.find((c) => c.id === settings.role_source) ?? null

  return (
    <div>
      {!settings ? (
        <div className="grid grid-cols-1 gap-4">
          <Skeleton className="h-36 rounded-2xl" />
          <Skeleton className="h-48 rounded-2xl" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6">
          <GettingStarted
            settings={settings}
            workspace={workspace}
            onAddProvider={() => openDialog(null)}
            onOpenIntegrations={onOpenIntegrations}
          />
          <Section title="Sign-in rules" description="How people in this workspace sign in.">
            <div className="grid grid-cols-1 gap-5">
              <SignInRules settings={settings} onChange={load} />
              <GroupAdminExpiry
                key={settings.group_admin_days}
                settings={settings}
                onChange={load}
              />
            </div>
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
                  roleSource={roleSource}
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

          <Section
            title="Recent sign-ins"
            description="Through your providers, and changes they made by themselves, newest first."
          >
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
        roleSource={roleSource}
        onOpenChange={(open) => !open && setRoles(null)}
        onSaved={() => {
          setRoles(null)
          load()
        }}
      />
    </div>
  )
}
