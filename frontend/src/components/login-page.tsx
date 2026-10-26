import { LoginForm } from "@/components/login-form"
import type { User } from "@/lib/api"

// The sign-in and sign-up page, after the Watermelon UI "auth-11" block: a dark
// split screen, the picture panel on the left (large screens), the form on the
// right. It's dark on its own (the `dark` class switches the theme tokens), the
// rest of the app stays light.
export function LoginPage({
  onLogin,
  onBack,
}: {
  onLogin: (user: User) => void
  onBack?: () => void
}) {
  return (
    <div className="dark flex min-h-svh w-full flex-col bg-background text-foreground antialiased selection:bg-foreground/20 lg:flex-row">
      <div className="relative hidden p-4 lg:flex lg:min-h-svh lg:w-1/2">
        <div className="relative size-full overflow-hidden rounded-[32px] border">
          <img
            src="/auth/sign-in.avif"
            alt=""
            className="absolute inset-0 size-full object-cover"
          />
          <div className="absolute inset-0 bg-linear-to-t from-background via-background/20 to-transparent" />
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
