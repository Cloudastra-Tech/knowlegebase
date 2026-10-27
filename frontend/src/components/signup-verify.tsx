import { useRef, useState, type FormEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router"

import { CopyField } from "@/components/sign-in-provider-dialog"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type User } from "@/lib/api"

// The sign-in page's control sizes (login-form.tsx).
const control = "h-11 rounded-xl px-4"
const pill = "h-11 rounded-full"

function Heading({ title, children }: { title: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="mb-2 flex flex-col gap-2 text-center">
      <h1 className="text-3xl leading-tight font-medium tracking-tight text-balance md:text-4xl">
        {title}
      </h1>
      {children && <p className="text-sm text-balance text-muted-foreground">{children}</p>}
    </div>
  )
}

// /signup/verify?token=... — the link a founder signing up with their work email
// was sent. Opening it proves the address; then they add a DNS TXT record for their
// domain, and the workspace is made once it checks out (kb/services/signup.py). No
// workspace exists until then.
export function SignupVerify({
  token,
  onCreated,
}: {
  token: string
  onCreated: (user: User, sessionToken: string) => void
}) {
  const navigate = useNavigate()
  // The link works until the workspace is made: confirming it again is harmless.
  const confirmed = useRef<ReturnType<typeof api.signupEmailConfirm> | null>(null)
  const pending = useQuery({
    queryKey: ["signup-verify", token],
    queryFn: () => (confirmed.current ??= api.signupEmailConfirm(token)),
    retry: false,
    staleTime: Infinity,
    meta: { silent: true },
  })
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  async function complete(e: FormEvent) {
    e.preventDefault()
    if (password.length < 8) {
      setError("Choose a password of at least 8 characters.")
      return
    }
    setChecking(true)
    setError(null)
    try {
      const done = await api.signupEmailComplete(token, password)
      onCreated(done.user, done.token)
      navigate("/admin/sign-in", { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.")
      setChecking(false)
    }
  }

  if (pending.isPending)
    return (
      <div className="flex w-full max-w-[400px] flex-col gap-3">
        <Skeleton className="h-10 w-full rounded-xl" />
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-11 w-full rounded-full" />
      </div>
    )

  if (pending.isError || !pending.data)
    return (
      <div className="flex w-full max-w-[400px] flex-col gap-6">
        <Heading title={<>This link <span className="font-serif font-light italic">can't be used.</span></>}>
          {pending.error instanceof Error ? pending.error.message : "Something went wrong."}
        </Heading>
        <Button className={pill} onClick={() => navigate("/signup", { replace: true })}>
          Start again
        </Button>
      </div>
    )

  const p = pending.data
  return (
    <form onSubmit={complete} className="flex w-full max-w-[400px] flex-col gap-6">
      <Heading
        title={
          <>
            Prove you own <span className="font-serif font-light italic">{p.domain}.</span>
          </>
        }
      >
        Your email <span className="font-medium text-foreground">{p.email}</span> is confirmed. Add
        this TXT record at your domain's DNS host, then create{" "}
        <span className="font-medium text-foreground">{p.workspace_name}</span>.
      </Heading>
      <div className="flex flex-col gap-3 glass-subtle rounded-xl p-4">
        <CopyField label="Name (host)" value={p.txt_name} />
        <CopyField label="Value" value={p.txt_value} />
        <p className="text-xs text-muted-foreground">
          Type TXT. DNS changes can take a few minutes to show. This link works until{" "}
          {new Date(p.expires_at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}.
        </p>
      </div>
      <FieldGroup className="gap-4">
        <Field data-invalid={!!error || undefined}>
          <FieldLabel htmlFor="signup-password">Choose a password</FieldLabel>
          <Input
            id="signup-password"
            type="password"
            autoComplete="new-password"
            className={control}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={!!error || undefined}
            minLength={8}
            maxLength={200}
            required
          />
          <FieldDescription>
            At least 8 characters. You'll be the workspace's admin and sign in with {p.email} and
            this; add your identity provider after.
          </FieldDescription>
        </Field>
        {error && <FieldError>{error}</FieldError>}
        <Button type="submit" className={pill} loading={checking}>
          Check the record and create the workspace
        </Button>
      </FieldGroup>
    </form>
  )
}
