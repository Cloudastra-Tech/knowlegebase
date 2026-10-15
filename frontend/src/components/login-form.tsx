import { useEffect, useState, type FormEvent } from "react"
import { KeyRound, Loader2 } from "lucide-react"
import { siAuth0, siAuthentik, siGoogle, siKeycloak, siOkta } from "simple-icons"

import { Button } from "@/components/ui/button"
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
import {
  api,
  session,
  ssoStartUrl,
  type AuthOptions,
  type SsoConnection,
  type User,
} from "@/lib/api"
import { cn, hashParams } from "@/lib/utils"

// Why single sign-on sent someone back here: api.py redirects to /#login?error=<code>.
const SSO_ERRORS: Record<string, string> = {
  not_allowed: "Your account isn't set up for this app. Ask your admin to add it.",
  not_workspace: "Use your company work account, not a personal or guest one.",
  unverified: "Your sign-in provider didn't confirm your email address.",
  cancelled: "Sign-in was cancelled.",
  sso_failed: "Single sign-on didn't finish. Please try again.",
  sso_unavailable: "That sign-in option isn't set up on this server.",
  expired: "That sign-in expired. Please try again.",
}

function ssoErrorFromUrl() {
  const code = hashParams().get("error")
  return code ? (SSO_ERRORS[code] ?? "Sign-in failed. Please try again.") : null
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
  if (!path) return <KeyRound />
  return (
    <svg viewBox="0 0 24 24" className="fill-current" aria-hidden>
      <path d={path} />
    </svg>
  )
}

// Sign in with a work email: its domain finds the workspace's own provider.
// One provider: go straight there. Several: let the person pick.
function EmailSso({ autoFocus }: { autoFocus: boolean }) {
  const [email, setEmail] = useState("")
  const [choices, setChoices] = useState<SsoConnection[] | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [looking, setLooking] = useState(false)

  // Back from the provider's page via the browser's back button: the page can
  // come back from its cache mid-"looking", so reset the button.
  useEffect(() => {
    const reset = (e: PageTransitionEvent) => e.persisted && setLooking(false)
    window.addEventListener("pageshow", reset)
    return () => window.removeEventListener("pageshow", reset)
  }, [])

  async function find(e: FormEvent) {
    e.preventDefault()
    setMessage(null)
    setChoices(null)
    setLooking(true)
    try {
      const { connections } = await api.ssoDiscover(email.trim())
      if (connections.length === 1) {
        window.location.assign(connections[0].url)
        return                                   // keep the spinner while the page leaves
      }
      if (connections.length > 1) setChoices(connections)
      else {
        const domain = email.trim().split("@").pop()
        setMessage(
          `There's no single sign-on for ${domain} yet. Ask your admin, or use another way to sign in.`,
        )
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Couldn't look up your sign-in.")
    }
    setLooking(false)
  }

  return (
    <form onSubmit={find} className="flex flex-col gap-4">
      <Field data-invalid={!!message || undefined}>
        <FieldLabel htmlFor="sso-email">Work email</FieldLabel>
        <Input
          id="sso-email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          autoFocus={autoFocus}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
            setChoices(null)
            setMessage(null)
          }}
          aria-invalid={!!message || undefined}
          required
        />
        {message && <FieldError>{message}</FieldError>}
      </Field>
      {choices ? (
        <Field>
          <FieldDescription>Choose how to sign in:</FieldDescription>
          {choices.map((c) => (
            <Button key={c.id} variant="outline" asChild>
              <a href={c.url}>
                <KeyRound />
                {c.name}
              </a>
            </Button>
          ))}
        </Field>
      ) : (
        <Field>
          <Button type="submit" disabled={looking}>
            {looking && <Loader2 className="animate-spin" />}
            Continue with single sign-on
          </Button>
        </Field>
      )}
    </form>
  )
}

// shadcn "login-05" block, wired to single sign-on + username / password (api.py).
export function LoginForm({
  className,
  onLogin,
  ...props
}: React.ComponentProps<"div"> & { onLogin: (user: User) => void }) {
  const [options, setOptions] = useState<AuthOptions | null>(null)
  const [optionsFailed, setOptionsFailed] = useState(false)
  const [ssoError] = useState(ssoErrorFromUrl)
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api
      .authOptions()
      .then(setOptions)
      .catch(() => setOptionsFailed(true))
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const { token, user } = await api.login(username, password)
      session.setToken(token)
      onLogin(user)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed")
    } finally {
      setLoading(false)
    }
  }

  // Nothing to choose until the server says which sign-in options are on.
  const providers = options?.providers ?? []
  const showPassword = options?.password ?? false
  const emailSso = options?.email_sso ?? false
  const anySso = emailSso || providers.length > 0

  return (
    <div className={cn("flex flex-col gap-6", className)} {...props}>
      <FieldGroup>
        <div className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-xl font-bold">Welcome back</h1>
          <FieldDescription>Sign in to ask your company docs anything</FieldDescription>
        </div>
        {!options && !optionsFailed && <Skeleton className="h-9 w-full" />}
        {optionsFailed && (
          <FieldError>Couldn't load the sign-in options. Refresh the page to try again.</FieldError>
        )}
        {emailSso && <EmailSso autoFocus />}
        {providers.length > 0 && (
          <Field>
            {providers.map((p) => (
              <Button key={p.id} variant="outline" asChild>
                <a href={ssoStartUrl(p.id)}>
                  <ProviderIcon id={p.id} />
                  Continue with {p.name}
                </a>
              </Button>
            ))}
          </Field>
        )}
        {ssoError && <FieldError>{ssoError}</FieldError>}
        {anySso && showPassword && <FieldSeparator>Or</FieldSeparator>}
        {showPassword && (
          <form onSubmit={submit} className="flex flex-col gap-4">
            <Field>
              <FieldLabel htmlFor="username">Username</FieldLabel>
              <Input
                id="username"
                autoComplete="username"
                autoFocus={!emailSso}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </Field>
            <Field data-invalid={!!error || undefined}>
              <FieldLabel htmlFor="password">Password</FieldLabel>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={!!error || undefined}
                required
              />
              {error && <FieldError>{error}</FieldError>}
            </Field>
            <Field>
              <Button type="submit" disabled={loading}>
                {loading && <Loader2 className="animate-spin" />}
                Login
              </Button>
            </Field>
          </form>
        )}
      </FieldGroup>
      <FieldDescription className="px-6 text-center">
        {anySso
          ? "Use your work account. No access? Ask your admin."
          : "No account? Ask your admin to create one for you."}
      </FieldDescription>
    </div>
  )
}
