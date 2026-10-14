import { useState, type FormEvent } from "react"
import { Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { api, session, type User } from "@/lib/api"
import { cn } from "@/lib/utils"

// shadcn "login-05" block, wired to our username + password login (api.py).
export function LoginForm({
  className,
  onLogin,
  ...props
}: React.ComponentProps<"div"> & { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

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

  return (
    <div className={cn("flex flex-col gap-6", className)} {...props}>
      <form onSubmit={submit}>
        <FieldGroup>
          <div className="flex flex-col items-center gap-2 text-center">
            <h1 className="text-xl font-bold">Welcome back</h1>
            <FieldDescription>Sign in to ask your company docs anything</FieldDescription>
          </div>
          <Field>
            <FieldLabel htmlFor="username">Username</FieldLabel>
            <Input
              id="username"
              autoComplete="username"
              autoFocus
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
        </FieldGroup>
      </form>
      <FieldDescription className="px-6 text-center">
        No account? Ask your admin to create one for you.
      </FieldDescription>
    </div>
  )
}
