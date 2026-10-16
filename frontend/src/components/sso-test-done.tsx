import { useEffect } from "react"
import { CheckCircle2 } from "lucide-react"

// Where the provider sends the test window of an admin's "Test sign-in" back to.
// The result is already on the admin's Sign-in screen; this window can go.
export function SsoTestDone() {
  useEffect(() => {
    const t = window.setTimeout(() => window.close(), 1200)
    return () => window.clearTimeout(t)
  }, [])

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-3 p-6 text-center">
      <CheckCircle2 className="size-8 text-emerald-600" />
      <h1 className="text-lg font-semibold">Test finished</h1>
      <p className="max-w-xs text-sm text-muted-foreground">
        The result is on the Sign-in screen. You can close this window.
      </p>
    </div>
  )
}
