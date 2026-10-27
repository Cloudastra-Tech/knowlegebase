import { useCallback, useEffect, useRef, useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  Globe,
  KeyRound,
  MoreHorizontal,
  Plus,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { ActivityView } from "@/components/activity-view"
import { ApiAccessView } from "@/components/api-access-view"
import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { Panel } from "@/components/glass/panel"
import { GroupsView } from "@/components/groups-view"
import { MembersView } from "@/components/members-view"
import { OnboardingChecklist } from "@/components/onboarding-checklist"
import { GroupRulesDialog, type RulesTarget } from "@/components/sign-in-group-rules"
import { JoiningPolicy } from "@/components/sign-in-joining"
import { ScreenHeader } from "@/components/screen-header"
import { CopyField, Notice, ProviderDialog } from "@/components/sign-in-provider-dialog"
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
import { WebhooksView } from "@/components/webhooks-view"
import { WorkspaceDataView } from "@/components/workspace-data-view"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  signIn,
  type ProviderHealth,
  type ScimSettings,
  type ScimToken,
  type SignInConnection,
  type SignInDomain,
  type SignInEvent,
  type SignInSettings,
  type SsoTestResult,
} from "@/lib/api"
import { isInstant, markInstantNav } from "@/lib/instant-nav"
import { queries } from "@/lib/queries"
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
// Where roles come from: one provider, or SCIM provisioning (SignInSettings.role_source).
type RoleSourceRef = { id: string; label: string }
const SCIM_SOURCE: RoleSourceRef = { id: "scim", label: "SCIM provisioning" }

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
    <Panel title={title} actions={action}>
      <p className="-mt-2 text-sm text-pretty text-muted-foreground">{description}</p>
      <div className="mt-4">{children}</div>
    </Panel>
  )
}

function OnBadge({ on, onLabel, offLabel }: { on: boolean; onLabel: string; offLabel: string }) {
  return on ? (
    <Badge variant="signal" className="gap-1.5">
      <span aria-hidden className="size-1.5 rounded-full bg-signal" /> {onLabel}
    </Badge>
  ) : (
    <Badge variant="mono">{offLabel}</Badge>
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
          Members tab don't expire, nor does admin from groups SCIM pushes, and the last admin
          always keeps it.
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
              <Button type="submit" size="sm" variant="outline" loading={saving}>
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
        <span className="min-w-0 font-mono text-sm font-medium break-all">{domain.domain}</span>
        <OnBadge on={domain.verified} onLabel="Verified" offLabel="Waiting for DNS" />
        <div className="ml-auto flex gap-2">
          {!domain.verified && (
            <Button size="sm" onClick={check} loading={checking}>
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
      <Button type="submit" variant="outline" loading={adding}>
        {!adding && <Plus />}
        Add domain
      </Button>
    </form>
  )
}

// ---- Providers ---------------------------------------------------------------------
// "kb-admins → Admin, …"
const ruleText = (conn: SignInConnection) =>
  conn.group_rules.map((r) => `${r.group} → ${ROLE_LABEL[r.role]}`).join(", ")
// A warning badge: the token on a hairline, unfilled, so small text keeps its contrast.
const WARN = "border-warning/40 bg-transparent text-warning"
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
    <HintBadge className={days < 7 ? "bg-destructive/10 text-destructive" : WARN} hint={hint}>
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
  roleSource: RoleSourceRef | null
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
            <Badge variant="mono">{PROTOCOL[conn.protocol]}</Badge>
            <OnBadge on={conn.enabled} onLabel="On" offLabel="Off" />
            {conn.health.status === "failing" && (
              <Badge variant="destructive" className="gap-1.5">
                <span aria-hidden className="size-1.5 rounded-full bg-destructive" /> Failing
              </Badge>
            )}
            {isSource && <Badge variant="mono">Role source</Badge>}
            {conn.needs_review && (
              <HintBadge
                className={WARN}
                hint={`New metadata from your provider wasn't applied: ${conn.review_reason}. If that's right, load it again (Edit → Metadata URL).`}
              >
                <AlertTriangle /> Needs review
              </HintBadge>
            )}
            <CertificateBadge conn={conn} />
          </div>
          <p className="truncate text-sm text-muted-foreground">
            <span className="font-mono text-xs">{conn.issuer}</span> · last sign-in{" "}
            {timeAgo(conn.last_login_at)}
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
            loading={refreshing}
          >
            {!refreshing && <RefreshCw />}
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
            <AsciiSpinner label="" className="text-signal" />
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
              <div className="flex items-start gap-3 rounded-lg border border-success/30 bg-success/5 p-4 text-sm">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
                <p>
                  <span className="font-medium">{state.result.name}</span> ({state.result.email}) would
                  sign in as <span className="font-medium">{ROLE_LABEL[state.result.role ?? "public"]}</span>
                  {state.result.role_source === "groups" && " (from their groups)"}
                  {state.result.role_source === "override" && " (set by an admin)"}
                  {state.result.new_member ? ", as a new member." : "."}
                  {state.result.new_member && state.result.join_policy === "request" &&
                    " Under your joining policy they'd first ask to join, and wait for an admin."}
                  {state.result.new_member && state.result.join_policy === "invite_only" &&
                    " Under your joining policy they'd need an invite first."}
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
                <dl className="grid grid-cols-[minmax(0,10rem)_1fr] gap-x-3 gap-y-1 rounded-lg border bg-card p-3 text-xs">
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
  signed_in: { label: "Signed in", className: "text-success" },
  refused: { label: "Turned away", className: "text-destructive" },
  failed: { label: "Failed", className: "text-destructive" },
  test: { label: "Test", className: "text-muted-foreground" },
  warning: { label: "Warning", className: "text-warning" },
  updated: { label: "Updated", className: "text-muted-foreground" },
  attention: { label: "Needs a look", className: "text-warning" },
  scim: { label: "SCIM", className: "text-foreground" },
}
// A SCIM request that was turned away (it has a fix: sso_errors.py).
const SCIM_REFUSED = { label: "Refused", className: "text-destructive" }
// A provider's own metadata changed (or couldn't be fetched): nobody signed in.
const ABOUT_PROVIDER: SignInEvent["outcome"][] = ["updated", "attention"]

function Events({
  events,
  empty = "Nobody has signed in through your providers yet.",
  plain = false,
}: {
  events: SignInEvent[]
  empty?: string
  plain?: boolean // on the Activity tab: a log, so no ASCII even when empty
}) {
  if (!events.length)
    return plain ? (
      <p className="rounded-xl border bg-card px-4 py-6 text-center text-sm text-muted-foreground">{empty}</p>
    ) : (
      <AsciiEmptyState art="inbox" size="sm" headingLevel={4} title={empty} />
    )
  // A log: opaque, mono times, nothing staggers in.
  return (
    <div className="divide-y rounded-xl border bg-card">
      {events.map((e, i) => {
        const look = e.outcome === "scim" && e.fix ? SCIM_REFUSED : OUTCOME[e.outcome]
        return (
          <div key={i} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-4 py-2.5 text-sm">
            <span className={cn("w-24 shrink-0 font-medium", look.className)}>{look.label}</span>
            {!ABOUT_PROVIDER.includes(e.outcome) && (
              <span className="min-w-0 truncate">{e.email ?? "unknown"}</span>
            )}
            <span className="text-muted-foreground">
              {ABOUT_PROVIDER.includes(e.outcome) ? "" : "via "}
              {e.connection ?? "a deleted provider"}
            </span>
            <span className="ml-auto font-mono text-xs text-muted-foreground tabular-nums">{timeAgo(e.at)}</span>
            {(e.reason || e.ref) && (
              <div className="basis-full text-xs break-words text-muted-foreground sm:pl-27">
                <p>
                  {explain(e)}
                  {e.ref && (
                    <span className="ml-2 font-mono whitespace-nowrap select-all">Ref {e.ref}</span>
                  )}
                </p>
                {e.fix && (e.outcome === "failed" || e.outcome === "scim") && (
                  <p className="mt-0.5">{e.fix}</p>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---- Provisioning (SCIM) ---------------------------------------------------------------
const OKTA_DOCS = "https://help.okta.com/en-us/content/topics/apps/apps_app_integration_wizard_scim.htm"
const OKTA_GUIDE = "https://developer.okta.com/docs/guides/scim-provisioning-integration-connect/main/"
const ENTRA_DOCS =
  "https://learn.microsoft.com/en-us/entra/identity/app-provisioning/configure-automatic-user-provisioning-portal"
const ENTRA_SCIM =
  "https://learn.microsoft.com/en-us/entra/identity/app-provisioning/use-scim-to-provision-users-and-groups"

function DocLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 font-medium text-foreground underline-offset-4 hover:underline"
    >
      {children} <ExternalLink className="size-3" />
    </a>
  )
}

function SetupNotes({ baseUrl }: { baseUrl: string }) {
  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ChevronDown className="size-4 transition-transform group-data-[state=open]:rotate-180" />
        Setup notes for Okta and Microsoft Entra ID
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-3 grid grid-cols-1 gap-4 text-sm md:grid-cols-2">
        <div className="grid gap-2 rounded-lg bg-muted/40 p-3">
          <p className="font-medium">Okta</p>
          <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
            <li>
              In your app integration's General tab, set Provisioning to SCIM, then open the
              Provisioning tab.
            </li>
            <li>
              SCIM connector base URL: <span className="break-all font-mono text-xs">{baseUrl}</span>.
              Unique identifier field: <span className="font-mono text-xs">userName</span>.
            </li>
            <li>
              Actions: Push New Users, Push Profile Updates and Push Groups. Authentication mode:
              HTTP Header, with a token from here as the Bearer token. Test the connection.
            </li>
            <li>
              Under To App, turn on Create Users, Update User Attributes and Deactivate Users.
              Assign people, then push the groups that should set roles.
            </li>
          </ol>
          <p className="flex flex-wrap gap-x-3">
            <DocLink href={OKTA_DOCS}>Okta: add SCIM provisioning</DocLink>
            <DocLink href={OKTA_GUIDE}>Okta SCIM guide</DocLink>
          </p>
        </div>
        <div className="grid gap-2 rounded-lg bg-muted/40 p-3">
          <p className="font-medium">Microsoft Entra ID</p>
          <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
            <li>
              Enterprise applications: open your app (or add a non-gallery one), then
              Provisioning, and set the mode to Automatic.
            </li>
            <li>
              Tenant URL: <span className="break-all font-mono text-xs">{baseUrl}</span>. Secret
              token: a token from here. Test Connection.
            </li>
            <li>
              Mappings: keep userName and emails[type eq "work"].value; the work email is what
              people sign in with, so it must be on a verified domain.
            </li>
            <li>
              Assign users and groups to the app, then start provisioning. Entra syncs about every
              40 minutes.
            </li>
          </ol>
          <p className="flex flex-wrap gap-x-3">
            <DocLink href={ENTRA_DOCS}>Entra: automatic provisioning</DocLink>
            <DocLink href={ENTRA_SCIM}>Entra SCIM reference</DocLink>
          </p>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function TokenRow({ token, onRevoke }: { token: ScimToken; onRevoke: () => void }) {
  const [confirm, setConfirm] = useState(false)
  const revoked = token.revoked_at !== null
  return (
    <div className={cn("flex flex-wrap items-center gap-3 rounded-xl border bg-card p-3", revoked && "opacity-60")}>
      <KeyRound className="size-4 text-muted-foreground" />
      <div className="min-w-0 flex-1 basis-48">
        <p className="text-sm">
          <span className="font-mono">kbscim_…{token.hint}</span>{" "}
          <OnBadge on={!revoked} onLabel="Active" offLabel="Revoked" />
        </p>
        <p className="truncate text-xs text-muted-foreground">
          Made by {token.created_by} {timeAgo(token.created_at)} ·{" "}
          {token.last_used_at ? `last used ${timeAgo(token.last_used_at)}` : "never used"}
        </p>
      </div>
      {!revoked && (
        <Button size="sm" variant="outline" className="text-destructive" onClick={() => setConfirm(true)}>
          Revoke
        </Button>
      )}
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke this token?</AlertDialogTitle>
            <AlertDialogDescription>
              Your identity provider can't provision with it any more, from its next request.
              Members it added stay as they are.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onRevoke}>Revoke</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// The token is only ever shown here, once: the server keeps a hash of it.
function NewTokenDialog({ token, onClose }: { token: string | null; onClose: () => void }) {
  return (
    <Dialog open={!!token} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Your new SCIM token</DialogTitle>
          <DialogDescription>
            Paste it into your identity provider as the bearer token (Okta) or secret token
            (Entra ID).
          </DialogDescription>
        </DialogHeader>
        <Notice>
          <p className="font-medium">Copy it now</p>
          <p className="text-muted-foreground">
            It won't be shown again. If it's lost, make a new one and revoke this one.
          </p>
        </Notice>
        {token && <CopyField label="Token" value={token} />}
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Provisioning({
  roleSource,
  onRoles,
}: {
  roleSource: RoleSourceRef | null
  onRoles: (rules: ScimSettings["group_rules"]) => void
}) {
  const queryClient = useQueryClient()
  const scim = useQuery(queries.scimSettings()).data ?? null
  const [creating, setCreating] = useState(false)
  const [shown, setShown] = useState<string | null>(null)

  const load = () => {
    void queryClient.invalidateQueries({ queryKey: queries.scimSettings().queryKey })
  }

  async function create() {
    setCreating(true)
    try {
      const made = await signIn.createScimToken()
      setShown(made.token)
      load()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setCreating(false)
    }
  }

  async function revoke(token: ScimToken) {
    try {
      await signIn.revokeScimToken(token.id)
      toast.success(`Revoked the token ending ${token.hint}.`)
      load()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const active = scim?.tokens.filter((t) => t.revoked_at === null) ?? []
  const isSource = roleSource?.id === SCIM_SOURCE.id
  const rules = scim?.group_rules.map((r) => `${r.group} → ${ROLE_LABEL[r.role]}`).join(", ")

  return (
    <Section
      title="Provisioning (SCIM)"
      description="Your identity provider adds people, updates them, and turns them off here as soon as it does there."
      action={
        <Button size="sm" onClick={create} loading={creating} disabled={!scim || active.length >= scim.max_tokens}>
          {!creating && <Plus />}
          New token
        </Button>
      }
    >
      {!scim ? (
        <Skeleton className="h-32 rounded-xl" />
      ) : (
        <div className="grid grid-cols-1 gap-4">
          <CopyField label="SCIM base URL (Okta) / Tenant URL (Entra ID)" value={scim.base_url} />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <RefreshCw className="size-3.5" />
              {scim.last_sync_at ? `Last sync ${timeAgo(scim.last_sync_at)}` : "No sync yet"}
            </span>
            <span>
              <span className="font-mono tabular-nums">{scim.users}</span>{" "}
              {scim.users === 1 ? "person" : "people"} and{" "}
              <span className="font-mono tabular-nums">{scim.groups}</span>{" "}
              {scim.groups === 1 ? "group" : "groups"} provisioned
            </span>
          </div>

          <div className="grid grid-cols-1 gap-2">
            {scim.tokens.map((t) => (
              <TokenRow key={t.id} token={t} onRevoke={() => revoke(t)} />
            ))}
            {!scim.tokens.length && (
              <AsciiEmptyState
                art="key"
                size="sm"
                headingLevel={4}
                title="No token yet."
                description="Make one, then give it and the URL above to your identity provider."
              />
            )}
          </div>

          <div className="flex flex-wrap items-start gap-3 rounded-xl border p-3">
            <div className="min-w-0 flex-1 basis-56">
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                Roles from SCIM groups
                {isSource && <Badge variant="mono">Role source</Badge>}
              </p>
              <p className="text-xs text-muted-foreground">
                {isSource
                  ? `Groups your provider pushes set roles, as they change: ${rules}`
                  : rules
                    ? `Not used: roles come from ${roleSource?.label ?? "nowhere"} (${rules})`
                    : roleSource
                      ? `Roles come from ${roleSource.label}. Give SCIM's groups rules to switch to them.`
                      : "Groups don't set roles yet. Give them rules to make SCIM the role source."}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={() => onRoles(scim.group_rules)}>
              Roles from groups
            </Button>
          </div>

          <p className="text-xs text-muted-foreground">
            Only emails on your verified domains, or ones you've invited, can be provisioned.
            Turning someone off signs them out everywhere at once; deleting them also anonymises
            their name and email after {scim.retention_days} days. The last admin can't be
            removed.
          </p>
          <SetupNotes baseUrl={scim.base_url} />

          <div className="grid grid-cols-1 gap-2">
            <AsciiDivider as="h4" label="Recent SCIM events" />
            <Events events={scim.events} empty="Your identity provider hasn't changed anything yet." />
          </div>
        </div>
      )}
      <NewTokenDialog token={shown} onClose={() => setShown(null)} />
    </Section>
  )
}

// ---- The screen ----------------------------------------------------------------------
export type AccessTab =
  | "sign-in"
  | "members"
  | "groups"
  | "activity"
  | "api"
  | "webhooks"
  | "data"

const ACCESS_TABS: [AccessTab, string][] = [
  ["sign-in", "Sign-in"],
  ["members", "Members"],
  ["groups", "Groups"],
  ["activity", "Activity"],
  ["api", "API & MCP"],
  ["webhooks", "Webhooks"],
  ["data", "Data"],
]

// The Activity tab's "Sign-ins": the same events as the sign-in settings show.
function SignInActivity() {
  const settings = useQuery(queries.signInSettings()).data
  if (!settings) return <Skeleton className="h-48 rounded-2xl" />
  return <Events events={settings.events} plain />
}

export function SignInView({
  workspace,
  me,
  tab,
  onTabChange,
}: {
  workspace: string
  me: string
  tab: AccessTab // each tab has its own URL (routes.tsx)
  onTabChange: (tab: AccessTab) => void
}) {
  // Requests to join waiting for an admin: counted on the Members tab.
  const waiting = useQuery(queries.joinRequests()).data?.requests.length ?? 0

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <ScreenHeader
        title="Access"
        icon={ShieldCheck}
        description={
          <>
            Who can sign in to the <span className="font-medium text-foreground">{workspace}</span>{" "}
            workspace, as what, and what they may see.
          </>
        }
        actions={<AccessTabs tab={tab} waiting={waiting} onTabChange={onTabChange} />}
        className="[&>div:last-child]:w-full sm:[&>div:last-child]:w-auto"
      />
      <div className="mt-6">
        <TabSwap key={tab}>
          {tab === "sign-in" ? (
            <SignInSettingsView />
          ) : tab === "members" ? (
            <MembersView me={me} />
          ) : tab === "groups" ? (
            <GroupsView />
          ) : tab === "api" ? (
            <ApiAccessView />
          ) : tab === "webhooks" ? (
            <WebhooksView />
          ) : tab === "data" ? (
            <WorkspaceDataView />
          ) : (
            <ActivityView signIns={<SignInActivity />} />
          )}
        </TabSwap>
      </div>
    </div>
  )
}

// A tab's content arriving: a 150ms fade, opacity only (no slide), and none at all
// when the tab was picked from the keyboard. Nothing in a tab is blurred glass, so the
// fade can't make a backdrop root of anything.
function TabSwap({ children }: { children: React.ReactNode }) {
  const [fade] = useState(() => !isInstant())
  return (
    <div
      data-slot="tab-swap"
      className={cn(fade && "animate-in duration-(--dur-swap) ease-out-strong fade-in")}
    >
      {children}
    </div>
  )
}

const TAB_KEYS = new Set(["ArrowLeft", "ArrowRight", "Home", "End"])

function AccessTabs({
  tab,
  waiting,
  onTabChange,
}: {
  tab: AccessTab
  waiting: number
  onTabChange: (tab: AccessTab) => void
}) {
  return (
    <>
        {/* Seven tabs don't fit a phone: there it's a picker instead of a sideways scroll. */}
        <Select value={tab} onValueChange={(v) => onTabChange(v as AccessTab)}>
          <SelectTrigger aria-label="Access section" className="w-full sm:hidden">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ACCESS_TABS.map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
                {value === "members" && waiting > 0 && ` (${waiting} waiting to join)`}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Tabs
          panels={false}
          value={tab}
          onValueChange={(v) => onTabChange(v as AccessTab)}
          className="hidden max-w-full sm:flex"
        >
          {/* Arrow keys switch tabs: from the keyboard, the new one lands without a fade. */}
          <TabsList onKeyDownCapture={(e) => TAB_KEYS.has(e.key) && markInstantNav()}>
            {ACCESS_TABS.map(([value, label]) => (
              <TabsTrigger key={value} value={value}>
                {label}
                {value === "members" && waiting > 0 && (
                  <Badge
                    className="h-5 min-w-5 rounded-full px-1.5 font-mono tabular-nums"
                    aria-label={`${waiting} waiting to join`}
                  >
                    {waiting}
                  </Badge>
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
    </>
  )
}

function SignInSettingsView() {
  const queryClient = useQueryClient()
  const settings = useQuery(queries.signInSettings()).data ?? null
  const [editing, setEditing] = useState<SignInConnection | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [testing, setTesting] = useState<SignInConnection | null>(null)
  const [testWindow, setTestWindow] = useState<Window | null>(null)
  const [roles, setRoles] = useState<RulesTarget | null>(null)
  const [opened, setOpened] = useState(0)     // remounts the dialogs, fresh, on each open

  const load = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queries.signInSettings().queryKey })
  }, [queryClient])

  function openDialog(conn: SignInConnection | null) {
    setEditing(conn)
    setOpened((n) => n + 1)
    setDialogOpen(true)
  }

  function openRoles(conn: RulesTarget) {
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
  const roleSource: RoleSourceRef | null =
    settings?.role_source === SCIM_SOURCE.id
      ? SCIM_SOURCE
      : (settings?.connections.find((c) => c.id === settings.role_source) ?? null)

  return (
    <div>
      {!settings ? (
        <div className="grid grid-cols-1 gap-4">
          <Skeleton className="h-36 rounded-2xl" />
          <Skeleton className="h-48 rounded-2xl" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6">
          <OnboardingChecklist onAddProvider={() => openDialog(null)} />
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
            title="Who can join"
            description="When someone signs in for the first time with an email on a verified domain. People you invite, or provision with SCIM, always get in."
          >
            <JoiningPolicy settings={settings} onChange={load} />
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
                <Notice>Verify a domain first: providers only sign in people with an email on one.</Notice>
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
                <AsciiEmptyState
                  art="plug"
                  size="sm"
                  headingLevel={4}
                  title="No provider yet."
                  description="Add your Okta, Entra ID, Google Workspace or any other OpenID Connect or SAML provider."
                />
              )}
            </div>
          </Section>

          <Provisioning
            roleSource={roleSource}
            onRoles={(rules) =>
              openRoles({ ...SCIM_SOURCE, protocol: "scim", group_claim: null, group_rules: rules })
            }
          />

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
          void queryClient.invalidateQueries({ queryKey: queries.scimSettings().queryKey })
        }}
      />
    </div>
  )
}
