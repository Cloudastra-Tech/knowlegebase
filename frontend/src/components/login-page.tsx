import { LoginForm } from "@/components/login-form"
import type { User } from "@/lib/api"

// The sign-in and sign-up page: the wordmark (back to the landing page), the
// card, and the product's sign-off.
export function LoginPage({
  onLogin,
  onBack,
}: {
  onLogin: (user: User) => void
  onBack?: () => void
}) {
  return (
    <div className="flex min-h-svh flex-col bg-muted/50">
      <header className="flex justify-center px-4 pt-8 md:pt-12">
        <button
          type="button"
          onClick={onBack}
          disabled={!onBack}
          className="rounded-md px-1 text-base font-semibold tracking-tight transition-opacity hover:opacity-70 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring disabled:hover:opacity-100"
        >
          Knowledge<span className="font-normal text-muted-foreground"> assistant</span>
        </button>
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-[9vh] pb-12">
        <LoginForm onLogin={onLogin} className="w-full max-w-[400px]" />
      </main>
      <footer className="px-4 pb-8 text-center text-xs text-muted-foreground">
        By Cloudastra.
      </footer>
    </div>
  )
}
