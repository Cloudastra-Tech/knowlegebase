import { LoginForm } from "@/components/login-form"
import type { User } from "@/lib/api"

// Page shell from the shadcn "login-05" block.
export function LoginPage({ onLogin }: { onLogin: (user: User) => void }) {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 bg-background p-6 md:p-10">
      <div className="w-full max-w-sm">
        <LoginForm onLogin={onLogin} />
      </div>
    </div>
  )
}
