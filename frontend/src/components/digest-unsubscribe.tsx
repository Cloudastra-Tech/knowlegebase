import { useEffect, useRef, useState } from "react"
import { Loader2 } from "lucide-react"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { digests, type UnsubscribeResult } from "@/lib/api"

// The sign-in page's control sizes (login-form.tsx).
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

// /unsubscribe/:token — the link at the bottom of every digest email. Opening it
// unsubscribes (one click, no sign-in); the same page can undo it.
export function DigestUnsubscribe({ token }: { token: string }) {
  const navigate = useNavigate()
  const [result, setResult] = useState<UnsubscribeResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Once, even when StrictMode runs the effect twice.
  const asked = useRef<Promise<UnsubscribeResult> | null>(null)

  useEffect(() => {
    let stopped = false
    asked.current ??= digests.unsubscribe(token)
    asked.current
      .then((r) => !stopped && setResult(r))
      .catch((err) => !stopped && setError(err instanceof Error ? err.message : "Something went wrong."))
    return () => {
      stopped = true
    }
  }, [token])

  async function toggle() {
    if (!result) return
    setBusy(true)
    try {
      setResult(await (result.enabled ? digests.unsubscribe(token) : digests.resubscribe(token)))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Something went wrong.")
    } finally {
      setBusy(false)
    }
  }

  if (error) {
    return (
      <div className="flex w-full max-w-[400px] flex-col gap-6">
        <Heading title={<>This link <span className="font-serif font-light italic">doesn't work.</span></>}>
          {error} You can manage your digests after signing in.
        </Heading>
        <Button className={pill} onClick={() => navigate("/digests")}>
          Manage digests
        </Button>
      </div>
    )
  }
  if (!result) {
    return (
      <div className="flex w-full max-w-[400px] flex-col gap-3">
        <Skeleton className="h-10 w-full rounded-xl" />
        <Skeleton className="h-11 w-full rounded-full" />
      </div>
    )
  }
  const what = `the ${result.frequency} digest${result.workspace_name ? ` of ${result.workspace_name}` : ""}`
  return (
    <div className="flex w-full max-w-[400px] flex-col gap-6">
      {result.enabled ? (
        <Heading title={<>You're <span className="font-serif font-light italic">subscribed</span> again.</>}>
          You'll get {what} as before.
        </Heading>
      ) : (
        <Heading title={<>You're <span className="font-serif font-light italic">unsubscribed.</span></>}>
          You won't get {what} any more.
        </Heading>
      )}
      <div className="flex flex-col gap-3">
        <Button variant="outline" className={pill} onClick={toggle} disabled={busy}>
          {busy && <Loader2 className="animate-spin" data-icon="inline-start" />}
          {result.enabled ? "Unsubscribe" : "Subscribe again"}
        </Button>
        <Button className={pill} onClick={() => navigate("/digests")}>
          Manage digests
        </Button>
      </div>
    </div>
  )
}
