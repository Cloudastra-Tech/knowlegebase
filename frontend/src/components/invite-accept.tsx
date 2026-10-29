import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type User } from "@/lib/api"
import { ROLE_LABEL } from "@/lib/roles"

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

// /invite/:token — someone opened the link an admin's invite emailed them. With a
// password they're signed in once they accept; through single sign-on, they go on
// to sign in with their work account (the email filled in).
export function InviteAccept({
  token,
  onJoined,
}: {
  token: string
  onJoined: (user: User, sessionToken: string) => void
}) {
  const navigate = useNavigate()
  const lookup = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api.inviteLookup(token),
    retry: false,
    meta: { silent: true },
  })
  const [name, setName] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [joining, setJoining] = useState(false)

  async function accept(e: React.FormEvent) {
    e.preventDefault()
    if (lookup.data?.method === "password" && password.length < 8) {
      setError("Choose a password of at least 8 characters.")
      return
    }
    setJoining(true)
    setError(null)
    try {
      const done = await api.inviteAccept(token, name.trim(), password)
      if ("sso" in done) {
        toast.success("You're in. Sign in with your work account to finish.")
        navigate("/login", { replace: true, state: { email: done.email } })
      } else {
        onJoined(done.user, done.token)
        navigate("/chat", { replace: true })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong")
      setJoining(false)
    }
  }

  if (lookup.isPending) {
    return (
      <div className="flex w-full max-w-[400px] flex-col gap-3">
        <Skeleton className="h-10 w-full rounded-xl" />
        <Skeleton className="h-11 w-full rounded-xl" />
        <Skeleton className="h-11 w-full rounded-full" />
      </div>
    )
  }

  if (lookup.isError || !lookup.data) {
    return (
      <div className="flex w-full max-w-[400px] flex-col gap-6">
        <Heading title={<>This invite <span className="font-serif font-light italic">can't be used.</span></>}>
          {lookup.error instanceof Error ? lookup.error.message : "Something went wrong."}
        </Heading>
        <Button className={pill} onClick={() => navigate("/login", { replace: true })}>
          Go to sign in
        </Button>
      </div>
    )
  }

  const invite = lookup.data
  return (
    <form onSubmit={accept} className="flex w-full max-w-[400px] flex-col gap-6">
      <Heading
        title={
          <>
            Join <span className="font-serif font-light italic">{invite.workspace_name}.</span>
          </>
        }
      >
        You were invited as {ROLE_LABEL[invite.role]}, at{" "}
        <span className="font-medium text-foreground">{invite.email}</span>.
      </Heading>
      <FieldGroup className="gap-4">
        {invite.method === "password" && (
          <>
            <Field>
              <FieldLabel htmlFor="invite-name">Your name</FieldLabel>
              <Input
                id="invite-name"
                autoComplete="name"
                className={control}
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={100}
              />
            </Field>
            <Field data-invalid={!!error || undefined}>
              <FieldLabel htmlFor="invite-password">Choose a password</FieldLabel>
              <Input
                id="invite-password"
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
              <FieldDescription>At least 8 characters. You'll sign in with your email and this.</FieldDescription>
            </Field>
          </>
        )}
        {invite.method === "sso" && (
          <FieldDescription className="text-center">
            Your company signs in with single sign-on: accept, then continue with your work
            account.
          </FieldDescription>
        )}
        {invite.method === null && (
          <FieldError>Password sign-in is off on this server. Ask your admin how to join.</FieldError>
        )}
        {error && <FieldError>{error}</FieldError>}
        <Button type="submit" className={pill} loading={joining} disabled={invite.method === null}>
          {invite.method === "sso" ? "Accept and sign in" : `Join ${invite.workspace_name}`}
        </Button>
      </FieldGroup>
    </form>
  )
}
