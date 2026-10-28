import { useState, type FormEvent } from "react"
import { Loader2 } from "lucide-react"
import { useNavigate, useSearchParams } from "react-router"
import { siGoogle } from "simple-icons"

import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

// The sign-in page's control sizes (login-form.tsx).
const control = "h-11 rounded-xl px-4"
const pill = "h-11 rounded-full"

// Why Google sent them back here: /#claim?error=<code> (kb/services/joining.py).
const ERRORS: Record<string, string> = {
  claim_code:
    "That claim code isn't valid, or it has expired or been used. Ask whoever runs this server for a new one.",
  claim_domain:
    "Sign in with a Google Workspace account on the domain the code is for: its primary domain must be that domain.",
  claim_verified:
    "This workspace already has a verified domain, so its own sign-in decides who gets in. Sign in instead.",
  claim_admin: "This workspace still has an admin who can sign in. Ask them to make you an admin.",
  claim_taken: "That domain or your account already belongs to another workspace.",
  workspace_closed: "This workspace is suspended or being deleted.",
  disabled: "Your access to this workspace has been turned off.",
  not_workspace: "Use your company's Google Workspace account, not a personal one.",
  unverified: "Google didn't confirm your email address.",
  cancelled: "Sign-in was cancelled.",
  expired: "That took too long. Enter the code again.",
  sso_failed: "Signing in with Google didn't finish. Please try again.",
}

// /claim — claim a workspace nobody can administer any more (one made by hand,
// whose admins have left): with a one-time code from whoever runs the server, and a
// Google Workspace account on the code's domain. The domain is verified for the
// workspace and they become its admin (join_store.py has the rules).
export function ClaimWorkspace() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const returned = params.get("error")
  const [code, setCode] = useState("")
  const [error, setError] = useState<string | null>(
    returned ? (ERRORS[returned] ?? "That didn't work. Please try again.") : null,
  )
  const [checking, setChecking] = useState(false)

  async function start(e: FormEvent) {
    e.preventDefault()
    setChecking(true)
    setError(null)
    try {
      const { url } = await api.claimStart(code.trim())
      window.location.assign(url)        // to Google, which sends them back signed in
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.")
      setChecking(false)
    }
  }

  return (
    <form onSubmit={start} className="flex w-full max-w-[400px] flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl leading-tight font-medium tracking-tight text-balance md:text-4xl">
          Claim your company's{" "}
          <span className="font-serif font-light italic">workspace.</span>
        </h1>
        <p className="text-sm text-balance text-muted-foreground">
          For a workspace nobody can administer any more: no verified domain, and no admin who
          can still sign in. You'll verify your domain with Google Workspace and become its admin.
        </p>
      </div>
      <FieldGroup className="gap-4">
        <Field data-invalid={!!error || undefined}>
          <FieldLabel htmlFor="claim-code">Claim code</FieldLabel>
          <Input
            id="claim-code"
            className={control}
            autoComplete="off"
            spellCheck={false}
            placeholder="kbclaim-…"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            aria-invalid={!!error || undefined}
            maxLength={200}
            required
            autoFocus
          />
          <FieldDescription>
            Whoever runs this server issues it, for your workspace and your domain. It works
            once, for 72 hours.
          </FieldDescription>
          {error && <FieldError>{error}</FieldError>}
        </Field>
        <Button type="submit" className={pill} disabled={checking || !code.trim()}>
          {checking ? (
            <Loader2 className="animate-spin" data-icon="inline-start" />
          ) : (
            <svg viewBox="0 0 24 24" className="fill-current" aria-hidden data-icon="inline-start">
              <path d={siGoogle.path} />
            </svg>
          )}
          Verify with Google Workspace
        </Button>
        <Button
          type="button"
          variant="ghost"
          className={pill}
          onClick={() => navigate("/login", { replace: true })}
        >
          Back to sign in
        </Button>
      </FieldGroup>
    </form>
  )
}
