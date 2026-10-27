import { AsciiBrain } from "@/components/ascii-art"
import { LoginForm } from "@/components/login-form"
import type { User } from "@/lib/api"
import { useForcedDark } from "@/lib/forced-theme"

// The sign-in and sign-up page, after the Watermelon UI "auth-11" block: a dark
// split screen, the landing page's moving ASCII brain on the left (large
// screens), the form on the right. Always dark (lib/forced-theme.ts), so a dialog
// or a toast opened here is dark too, whatever theme the person picked for the app.
// The brain runs at 24 fps here: it shares the page with a form being typed in.
export function LoginPage({
  onLogin,
  onBack,
  children,
}: {
  onLogin?: (user: User) => void
  onBack?: () => void
  children?: React.ReactNode // another form in its place (accepting an invite)
}) {
  useForcedDark()
  return (
    <div className="flex min-h-svh w-full flex-col bg-background text-foreground antialiased selection:bg-signal/30 lg:flex-row">
      <div className="relative hidden p-4 lg:flex lg:min-h-svh lg:w-1/2">
        {/* The subtle glass tier, unblurred: behind the panel is only the plain page. */}
        <div className="glass-subtle relative size-full overflow-hidden rounded-[32px] bg-[radial-gradient(60%_45%_at_50%_36%,color-mix(in_oklch,var(--signal)_9%,transparent),transparent)]">
          <AsciiBrain framing="panel" fps={24} className="absolute inset-0" />
          <div className="absolute inset-x-0 bottom-0 h-1/2 bg-linear-to-t from-background via-background/80 to-transparent" />
          <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 px-10 pb-14 text-center">
            <p className="text-4xl font-medium tracking-tight text-balance xl:text-5xl">
              Your company's knowledge,{" "}
              <span className="font-serif font-light italic">one question away.</span>
            </p>
            <p className="max-w-sm text-sm text-balance text-muted-foreground">
              Answers from your own documents, with the sources they came from.
            </p>
          </div>
        </div>
      </div>

      <div className="flex w-full flex-col p-6 sm:p-12 lg:w-1/2">
        <button
          type="button"
          onClick={onBack}
          disabled={!onBack}
          className="self-start rounded-md px-1 text-base font-semibold tracking-tight transition-opacity duration-(--dur-hover) ease-hover hover:opacity-70 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring disabled:hover:opacity-100"
        >
          Knowledge<span className="font-normal text-muted-foreground"> assistant</span>
        </button>
        <main className="flex flex-1 items-start justify-center pt-[8vh] pb-8 lg:items-center lg:pt-0">
          {children ?? (onLogin && <LoginForm onLogin={onLogin} className="w-full max-w-[400px]" />)}
        </main>
        <footer className="text-center font-mono text-xs text-muted-foreground">by cloudastra</footer>
      </div>
    </div>
  )
}
