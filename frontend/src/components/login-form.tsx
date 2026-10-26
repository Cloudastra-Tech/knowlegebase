import { useEffect, useRef, useState, type FormEvent } from "react"
import { ArrowRight, ChevronDown, KeyRound, Loader2 } from "lucide-react"
import { siAuth0, siAuthentik, siGoogle, siKeycloak, siOkta } from "simple-icons"

import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldSeparator,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  api,
  session,
  signupStartUrl,
  ssoStartUrl,
  type AuthOptions,
  type SsoConnection,
  type SsoRoute,
  type User,
} from "@/lib/api"
import { cn, hashParams } from "@/lib/utils"

// Why sign-in or sign-up sent someone back here: api.py redirects to
// /#login?error=<code> (or /#signup?error=<code>).
const ERRORS: Record<string, string> = {
  not_allowed: "Your account isn't set up for this app. Ask your admin to add it.",
  not_workspace: "Use your company's Google Workspace account, not a personal or guest one.",
  disabled: "Your access has been turned off. Ask your admin.",
  unverified: "Your sign-in provider didn't confirm your email address.",
  cancelled: "Sign-in was cancelled.",
  sso_failed: "Single sign-on didn't finish. Please try again.",
  sso_unavailable: "That sign-in option isn't set up on this server.",
  expired: "That sign-in expired. Please try again.",
  workspace_exists: "Your company already has a workspace here. Sign in instead.",
  account_exists: "That account already belongs to a workspace. Sign in instead.",
  signup_domain:
    "Use an account on your company's primary domain: that's the domain the workspace is for.",
  signup_off: "New workspaces can't be created on this server. Ask whoever runs it.",
  name: "Give your workspace a name.",
}

// A failed sign-in also brings a reference (K7Q2-9XMA); the admin sees the same one
// in Recent sign-ins, with exactly what went wrong.
const REF = /^[A-Z2-9]{4}-[A-Z2-9]{4}$/
const EMAIL = /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i

type Tab = "sign-in" | "create"

function errorFromUrl() {
  const params = hashParams()
  const code = params.get("error")
  if (!code) return null
  const ref = params.get("ref") ?? ""
  return {
    code,
    message: ERRORS[code] ?? "Sign-in failed. Please try again.",
    ref: REF.test(ref) ? ref : null,
  }
}

// Logos by provider id. simple-icons has no Microsoft logo, so draw its four
// squares; any other OpenID Connect provider gets a key.
const PROVIDER_PATHS: Record<string, string> = {
  google: siGoogle.path,
  microsoft: "M1 1h10v10H1zM13 1h10v10H13zM1 13h10v10H1zM13 13h10v10H13z",
  okta: siOkta.path,
  keycloak: siKeycloak.path,
  auth0: siAuth0.path,
  authentik: siAuthentik.path,
}

function ProviderIcon({ id }: { id: string }) {
  const path = PROVIDER_PATHS[id]
  if (!path) return <KeyRound data-icon="inline-start" />
  return (
    <svg viewBox="0 0 24 24" className="fill-current" aria-hidden data-icon="inline-start">
      <path d={path} />
    </svg>
  )
}

// The server-wide Google sign-in only takes Google Workspace accounts.
const providerLabel = (p: { id: string; name: string }) =>
  p.id === "google" ? "Google Workspace" : p.name

const domainOf = (email: string) => email.trim().toLowerCase().split("@").pop() ?? ""

// "acme-corp.co.uk" -> "Acme Corp"
const nameFromDomain = (domain: string) =>
  (domain.split(".")[0] ?? "")
    .split(/[-_]/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ")

// ---- The route pass ---------------------------------------------------------------
// As someone types their work email, the domain is looked up once and the pass
// fills in: which workspace, how it signs them in, and where their role comes from.
type Lookup =
  | { state: "idle" }
  | { state: "looking"; domain: string }
  | { state: "found"; domain: string; route: SsoRoute }
  | { state: "failed"; domain: string }

function methodOf(route: SsoRoute) {
  if (!route.workspace) return null
  const n = route.connections.length
  if (n === 1) return route.connections[0].name
  if (n > 1) return `${n} options`
  if (route.workspace?.google) return "Google Workspace"
  if (route.workspace?.password) return "Password"
  return "None yet"
}

function rolesOf(route: SsoRoute) {
  if (!route.workspace) return null
  const from = route.workspace.roles_from
  return from ? `${from} groups` : "Your admin"
}

// The method's logo: the workspace provider's own (Google, Microsoft, Okta...),
// or Google's for the server-wide Google sign-in. None when it isn't one we know.
function MethodMark({ route }: { route: SsoRoute }) {
  const kind =
    route.connections.length === 1
      ? route.connections[0].kind
      : !route.connections.length && route.workspace?.google
        ? "google"
        : null
  return kind && PROVIDER_PATHS[kind] ? <ProviderIcon id={kind} /> : null
}

function RoutePass({
  lookup,
  onCreate,
}: {
  lookup: Lookup
  onCreate: (suggestedName: string) => void
}) {
  const frame = "rounded-lg border bg-muted/40 text-sm"
  if (lookup.state === "idle" || lookup.state === "failed")
    return (
      <p aria-live="polite" className={cn(frame, "px-3 py-2.5 text-muted-foreground")}>
        {lookup.state === "idle"
          ? "Type your work email to see your workspace and how you'll sign in."
          : `Couldn't check ${lookup.domain} just now. Continue still works.`}
      </p>
    )
  if (lookup.state === "found" && !lookup.route.workspace) {
    const suggested = nameFromDomain(lookup.domain)
    return (
      <div aria-live="polite" className={cn(frame, "flex flex-col gap-2.5 px-3 py-3")}>
        <p>
          <span className="font-medium">{lookup.domain}</span> doesn't have a workspace here yet.
        </p>
        {lookup.route.signup ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="self-start bg-background"
            onClick={() => onCreate(suggested)}
          >
            Create a workspace for {suggested || lookup.domain}
            <ArrowRight data-icon="inline-end" />
          </Button>
        ) : (
          <p className="text-muted-foreground">
            Ask your company's admin, or sign in another way below.
          </p>
        )}
      </div>
    )
  }
  const route = lookup.state === "found" ? lookup.route : null
  const methodMark = route ? <MethodMark route={route} /> : null
  const segments: [string, string | null, React.ReactNode][] = [
    ["Workspace", route?.workspace?.name ?? null, null],
    ["Sign in with", route ? methodOf(route) : null, methodMark],
    ["Roles from", route ? rolesOf(route) : null, null],
  ]
  return (
    <div
      aria-live="polite"
      className={cn(
        frame,
        "flex flex-col divide-y divide-border overflow-hidden transition-colors sm:grid sm:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1fr)] sm:divide-x sm:divide-y-0",
        route && "border-foreground/15 bg-background",
      )}
    >
      {segments.map(([label, value, mark]) => (
        <div
          key={label}
          className="flex min-w-0 items-center justify-between gap-3 px-3 py-2 sm:flex-col sm:items-start sm:justify-start sm:gap-0.5 sm:py-2.5"
        >
          <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
          {!route ? (
            <Skeleton className="h-5 w-24 sm:w-full sm:max-w-20" />
          ) : (
            <span
              key={value}
              title={value ?? undefined}
              className="flex min-w-0 items-center gap-1.5 font-medium leading-snug motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-1 motion-safe:duration-300 [&_svg]:size-3.5 [&_svg]:shrink-0"
            >
              {mark}
              <span className="truncate sm:line-clamp-2 sm:whitespace-normal">{value}</span>
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

// ---- Sign in ----------------------------------------------------------------------
function SignIn({
  options,
  onLogin,
  onCreate,
  error,
}: {
  options: AuthOptions
  onLogin: (user: User) => void
  onCreate: (suggestedName: string) => void
  error: ReturnType<typeof errorFromUrl>
}) {
  const providers = options.providers
  const emailFirst = options.email_sso || providers.length > 0
  const [email, setEmail] = useState("")
  // What each domain looked up to (the pass is derived from it while typing).
  const [routes, setRoutes] = useState<Record<string, SsoRoute | "failed">>({})
  const [choices, setChoices] = useState<SsoConnection[] | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [going, setGoing] = useState(false)
  const [othersOpen, setOthersOpen] = useState(!emailFirst)
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)
  const passwordRef = useRef<HTMLInputElement>(null)

  // Back from the provider's page via the browser's back button: the page can
  // come back from its cache mid-"going", so reset the button.
  useEffect(() => {
    const reset = (e: PageTransitionEvent) => e.persisted && setGoing(false)
    window.addEventListener("pageshow", reset)
    return () => window.removeEventListener("pageshow", reset)
  }, [])

  // Look the domain up once someone pauses on a whole email address.
  const domain = emailFirst && EMAIL.test(email.trim()) ? domainOf(email) : ""
  const known = domain ? routes[domain] : undefined
  const lookup: Lookup = !domain
    ? { state: "idle" }
    : known === undefined
      ? { state: "looking", domain }
      : known === "failed"
        ? { state: "failed", domain }
        : { state: "found", domain, route: known }

  useEffect(() => {
    if (!domain || routes[domain] !== undefined) return
    let live = true
    const timer = window.setTimeout(() => {
      api
        .ssoDiscover(`lookup@${domain}`)
        .then((route) => live && setRoutes((r) => ({ ...r, [domain]: route })))
        .catch(() => live && setRoutes((r) => ({ ...r, [domain]: "failed" })))
    }, 350)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [domain, routes])

  async function go(e: FormEvent) {
    e.preventDefault()
    setMessage(null)
    setChoices(null)
    setGoing(true)
    try {
      // Asked again with the whole address: the provider gets it as a login hint.
      const route = await api.ssoDiscover(email.trim())
      if (route.connections.length === 1) {
        window.location.assign(route.connections[0].url)
        return                                   // keep the spinner while the page leaves
      }
      if (route.connections.length > 1) setChoices(route.connections)
      else if (route.workspace?.google && providers.some((p) => p.id === "google")) {
        window.location.assign(ssoStartUrl("google"))
        return
      } else if (route.workspace?.password && options.password) {
        setUsername(email.trim())
        setOthersOpen(true)
        window.setTimeout(() => passwordRef.current?.focus(), 50)
      } else if (!route.workspace) {
        setMessage(`There's no workspace for ${domainOf(email)} here yet.`)
      } else setMessage("Your workspace has no way to sign in yet. Ask your admin.")
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Couldn't look up your sign-in.")
    }
    setGoing(false)
  }

  async function signInWithPassword(e: FormEvent) {
    e.preventDefault()
    setPasswordError(null)
    setSigningIn(true)
    try {
      const { token, user } = await api.login(username, password)
      session.setToken(token)
      onLogin(user)
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : "Sign-in failed.")
    } finally {
      setSigningIn(false)
    }
  }

  const noWorkspace = lookup.state === "found" && !lookup.route.workspace
  const method = lookup.state === "found" ? methodOf(lookup.route) : null
  const continueLabel =
    method && lookup.state === "found" && lookup.route.connections.length <= 1 && method !== "None yet"
      ? `Continue with ${method}`
      : "Continue"
  const otherProviders = providers.map((p) => (
    <Button key={p.id} variant="outline" asChild>
      <a href={ssoStartUrl(p.id)}>
        <ProviderIcon id={p.id} />
        Continue with {providerLabel(p)}
      </a>
    </Button>
  ))
  const passwordForm = options.password && (
    <form onSubmit={signInWithPassword}>
      <FieldGroup className="gap-4">
        <Field>
          <FieldLabel htmlFor="username">Username or email</FieldLabel>
          <Input
            id="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </Field>
        <Field data-invalid={!!passwordError || undefined}>
          <FieldLabel htmlFor="password">Password</FieldLabel>
          <Input
            ref={passwordRef}
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={!!passwordError || undefined}
            required
          />
          {passwordError && <FieldError>{passwordError}</FieldError>}
        </Field>
        <Button type="submit" variant={emailFirst ? "secondary" : "default"} disabled={signingIn}>
          {signingIn && <Loader2 className="animate-spin" data-icon="inline-start" />}
          Sign in with password
        </Button>
      </FieldGroup>
    </form>
  )

  // A server with only passwords: no email-first step to explain.
  if (!emailFirst)
    return (
      <FieldGroup>
        <ErrorNote error={error} />
        {passwordForm}
        <FieldDescription className="text-center">
          No account? Ask your admin to create one for you.
        </FieldDescription>
      </FieldGroup>
    )

  return (
    <FieldGroup>
      <form onSubmit={go}>
        <FieldGroup className="gap-3">
          <Field data-invalid={!!message || undefined}>
            <FieldLabel htmlFor="work-email">Work email</FieldLabel>
            <Input
              id="work-email"
              type="email"
              autoComplete="email"
              placeholder="you@company.com"
              autoFocus
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                setChoices(null)
                setMessage(null)
              }}
              aria-invalid={!!message || undefined}
              required
            />
          </Field>
          <RoutePass lookup={lookup} onCreate={onCreate} />
          {noWorkspace ? null : choices ? (
            <Field>
              <FieldDescription>Your workspace has more than one way in. Choose yours:</FieldDescription>
              {choices.map((c) => (
                <Button key={c.id} variant="outline" asChild>
                  <a href={c.url}>
                    <KeyRound data-icon="inline-start" />
                    {c.name}
                  </a>
                </Button>
              ))}
            </Field>
          ) : (
            <Button type="submit" disabled={going || lookup.state === "looking"}>
              {going ? (
                <Loader2 className="animate-spin" data-icon="inline-start" />
              ) : (
                lookup.state === "found" &&
                continueLabel !== "Continue" && <MethodMark route={lookup.route} />
              )}
              {continueLabel}
            </Button>
          )}
          {message && <FieldError>{message}</FieldError>}
        </FieldGroup>
      </form>
      <ErrorNote error={error} />
      {(providers.length > 0 || options.password) && (
        <Collapsible open={othersOpen} onOpenChange={setOthersOpen}>
          <FieldSeparator className="my-1">
            <CollapsibleTrigger className="inline-flex items-center gap-1 rounded-sm px-1 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
              Other ways to sign in
              <ChevronDown
                className={cn("size-3.5 transition-transform", othersOpen && "rotate-180")}
                aria-hidden
              />
            </CollapsibleTrigger>
          </FieldSeparator>
          <CollapsibleContent className="flex flex-col gap-4 pt-3">
            {otherProviders.length > 0 && (
              <Field className="gap-2">
                {otherProviders}
                {providers.some((p) => p.id === "google") && (
                  <FieldDescription className="text-xs">
                    Google Workspace is for company Google accounts on a domain your workspace has
                    verified. Personal Gmail won't work.
                  </FieldDescription>
                )}
              </Field>
            )}
            {passwordForm}
          </CollapsibleContent>
        </Collapsible>
      )}
    </FieldGroup>
  )
}

function ErrorNote({ error }: { error: ReturnType<typeof errorFromUrl> }) {
  if (!error) return null
  return (
    <Field className="gap-1">
      <FieldError>{error.message}</FieldError>
      {error.ref && (
        <FieldDescription className="text-xs">
          Reference <span className="font-mono tabular-nums select-all">{error.ref}</span>. If it
          keeps happening, give it to your admin.
        </FieldDescription>
      )}
    </Field>
  )
}

// ---- Create a workspace -----------------------------------------------------------
const STEPS = [
  ["Name your workspace", "Usually your company's name."],
  [
    "Confirm with Google Workspace",
    "Your company Google account proves you own its domain, so your colleagues can sign in straight away.",
  ],
  [
    "Connect sign-in and documents",
    "Add Okta, Microsoft Entra ID or any SAML provider if you use one, then your drives and wiki.",
  ],
] as const

function CreateWorkspace({
  name,
  setName,
  error,
  onSignIn,
}: {
  name: string
  setName: (name: string) => void
  error: ReturnType<typeof errorFromUrl>
  onSignIn: () => void
}) {
  const [going, setGoing] = useState(false)

  useEffect(() => {
    const reset = (e: PageTransitionEvent) => e.persisted && setGoing(false)
    window.addEventListener("pageshow", reset)
    return () => window.removeEventListener("pageshow", reset)
  }, [])

  function start(e: FormEvent) {
    e.preventDefault()
    setGoing(true)
    window.location.assign(signupStartUrl(name.trim()))
  }

  return (
    <form onSubmit={start}>
      <FieldGroup>
        <ol className="flex flex-col gap-4">
          {STEPS.map(([title, body], i) => (
            <li key={title} className="flex gap-3">
              <span
                aria-hidden
                className="flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium tabular-nums text-muted-foreground"
              >
                {i + 1}
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-0.5 pt-0.5">
                {i === 0 ? (
                  <Field data-invalid={error?.code === "name" || undefined} className="gap-1.5">
                    <FieldLabel htmlFor="workspace-name" className="leading-tight">
                      {title}
                    </FieldLabel>
                    <Input
                      id="workspace-name"
                      placeholder="Acme Corp"
                      maxLength={60}
                      autoComplete="organization"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      required
                    />
                    <FieldDescription>{body}</FieldDescription>
                  </Field>
                ) : (
                  <>
                    <span className="text-sm font-medium leading-tight">{title}</span>
                    <span className="text-sm text-muted-foreground">{body}</span>
                  </>
                )}
              </div>
            </li>
          ))}
        </ol>
        <ErrorNote error={error} />
        {(error?.code === "workspace_exists" || error?.code === "account_exists") && (
          <Button type="button" variant="outline" onClick={onSignIn}>
            Sign in instead
          </Button>
        )}
        <Field className="gap-2">
          <Button type="submit" disabled={going || !name.trim()}>
            {going ? (
              <Loader2 className="animate-spin" data-icon="inline-start" />
            ) : (
              <ProviderIcon id="google" />
            )}
            Continue with Google Workspace
          </Button>
          <FieldDescription className="text-xs">
            You'll be the workspace's admin. A personal Gmail account can't create a workspace.
          </FieldDescription>
        </Field>
      </FieldGroup>
    </form>
  )
}

// ---- The card ---------------------------------------------------------------------
export function LoginForm({
  className,
  onLogin,
  ...props
}: React.ComponentProps<"div"> & { onLogin: (user: User) => void }) {
  const [options, setOptions] = useState<AuthOptions | null>(null)
  const [optionsFailed, setOptionsFailed] = useState(false)
  const [error] = useState(errorFromUrl)
  const [tab, setTab] = useState<Tab>(() =>
    window.location.hash.startsWith("#signup") ? "create" : "sign-in",
  )
  const [workspaceName, setWorkspaceName] = useState("")

  useEffect(() => {
    api
      .authOptions()
      .then(setOptions)
      .catch(() => setOptionsFailed(true))
  }, [])

  // The landing page's Sign in / Get started change the hash without reloading.
  useEffect(() => {
    const follow = () =>
      setTab(window.location.hash.startsWith("#signup") ? "create" : "sign-in")
    window.addEventListener("hashchange", follow)
    return () => window.removeEventListener("hashchange", follow)
  }, [])

  function changeTab(next: Tab) {
    setTab(next)
    history.replaceState(null, "", next === "create" ? "#signup" : "#login")
  }

  const signup = options?.signup ?? false
  const shown: Tab = signup ? tab : "sign-in"
  const signInError = shown === "sign-in" ? error : null
  const createError = shown === "create" ? error : null

  return (
    <div className={cn("flex flex-col gap-6", className)} {...props}>
      <div className="flex flex-col gap-1.5 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-balance">
          {shown === "create" ? "Create your workspace" : "Sign in to your workspace"}
        </h1>
        <p className="min-h-10 text-sm text-balance text-muted-foreground sm:min-h-0">
          {shown === "create"
            ? "Then ask your company's documents anything."
            : "Ask your company's documents anything, with sources."}
        </p>
      </div>
      <div className="rounded-xl bg-card p-6 shadow-[0_1px_3px_rgb(0_0_0/0.06),0_12px_32px_-12px_rgb(0_0_0/0.14)]">
        {!options && !optionsFailed && (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        )}
        {optionsFailed && (
          <FieldError>Couldn't load the sign-in options. Refresh the page to try again.</FieldError>
        )}
        {options && (
          <Tabs value={shown} onValueChange={(v) => changeTab(v as Tab)} className="gap-5">
            {signup && (
              <TabsList className="w-full">
                <TabsTrigger value="sign-in">Sign in</TabsTrigger>
                <TabsTrigger value="create">Create a workspace</TabsTrigger>
              </TabsList>
            )}
            <TabsContent value="sign-in">
              <SignIn
                options={options}
                onLogin={onLogin}
                error={signInError}
                onCreate={(suggested) => {
                  setWorkspaceName((n) => n || suggested)
                  changeTab("create")
                }}
              />
            </TabsContent>
            {signup && (
              <TabsContent value="create">
                <CreateWorkspace
                  name={workspaceName}
                  setName={setWorkspaceName}
                  error={createError}
                  onSignIn={() => changeTab("sign-in")}
                />
              </TabsContent>
            )}
          </Tabs>
        )}
      </div>
      {shown === "sign-in" && (options?.email_sso || (options?.providers.length ?? 0) > 0) && (
        <p className="px-6 text-center text-sm text-balance text-muted-foreground">
          No access? Ask your company's admin.
        </p>
      )}
    </div>
  )
}
