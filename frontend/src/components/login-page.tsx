import { ArrowLeft } from "lucide-react"

import { LoginForm } from "@/components/login-form"
import type { User } from "@/lib/api"

// Page shell from the shadcn "login-05" block.
export function LoginPage({
  onLogin,
  onBack,
}: {
  onLogin: (user: User) => void
  onBack?: () => void
}) {
  return (
    <div className="relative flex min-h-svh flex-col items-center justify-center gap-6 bg-background p-6 md:p-10">
      {onBack && (
        <button
          onClick={onBack}
          className="absolute left-4 top-4 flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground md:left-8 md:top-6"
        >
          <ArrowLeft className="size-4" /> Back
        </button>
      )}
      <div className="w-full max-w-sm">
        <LoginForm onLogin={onLogin} />
      </div>
    </div>
  )
}
