import { AsciiBrain } from "@/components/ascii-art"
import { LoginForm } from "@/components/login-form"
import type { User } from "@/lib/api"

// The sign-in and sign-up page, after the Watermelon UI "auth-11" block: a dark
// split screen, the landing page's moving ASCII brain on the left (large
// screens), the form on the right. It's dark on its own (the `dark` class
// switches the theme tokens), the rest of the app stays light.
export function LoginPage({
  onLogin,
  onBack,
}: {
  onLogin: (user: User) => void
  onBack?: () => void
}) {
  return (
    <div className="dark flex min-h-svh w-full flex-col bg-background text-foreground antialiased [--ascii-accent:oklch(0.82_0.15_160)] selection:bg-[var(--ascii-accent)]/30 lg:flex-row">
      <div className="relative hidden p-4 lg:flex lg:min-h-svh lg:w-1/2">
        <div className="relative size-full overflow-hidden rounded-[32px] border bg-[radial-gradient(60%_45%_at_50%_36%,color-mix(in_oklch,var(--ascii-accent)_9%,transparent),transparent)]">
          <AsciiBrain framing="panel" className="absolute inset-0" />
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
          className="self-start rounded-md px-1 text-base font-semibold tracking-tight transition-opacity hover:opacity-70 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring disabled:hover:opacity-100"
        >
          Knowledge<span className="font-normal text-muted-foreground"> assistant</span>
        </button>
        <main className="flex flex-1 items-start justify-center pt-[8vh] pb-8 lg:items-center lg:pt-0">
          <LoginForm onLogin={onLogin} className="w-full max-w-[400px]" />
        </main>
        <footer className="text-center text-xs text-muted-foreground">By Cloudastra.</footer>
      </div>
    </div>
  )
}
