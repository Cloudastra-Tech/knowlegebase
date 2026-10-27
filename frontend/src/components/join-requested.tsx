import { MailCheck } from "lucide-react"
import { useNavigate } from "react-router"

import { Button } from "@/components/ui/button"

// /join-requested — where someone lands after signing in from a verified domain of a
// workspace whose joining policy is "ask an admin" (kb/services/joining.py). They
// aren't a member yet and have no session: their admins were asked to let them in.
export function JoinRequested() {
  const navigate = useNavigate()
  return (
    <div className="flex w-full max-w-[400px] flex-col gap-6">
      <div className="flex size-12 items-center justify-center rounded-2xl border bg-muted/40">
        <MailCheck className="size-6 text-[var(--ascii-accent)]" />
      </div>
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl leading-tight font-medium tracking-tight text-balance md:text-4xl">
          Your request was sent to{" "}
          <span className="font-serif font-light italic">your admins.</span>
        </h1>
        <p className="text-sm text-balance text-muted-foreground">
          Your workspace asks an admin before anyone new joins. Once one of them approves it,
          you'll get an email: then sign in the same way you just did.
        </p>
      </div>
      <ul className="flex flex-col gap-2 rounded-xl border bg-muted/40 p-4 text-sm text-muted-foreground">
        <li>Signing in again before then doesn't send another request.</li>
        <li>A request nobody answers expires after 30 days.</li>
        <li>In a hurry? Ask an admin to invite you instead.</li>
      </ul>
      <Button className="h-11 rounded-full" onClick={() => navigate("/login", { replace: true })}>
        Back to sign in
      </Button>
    </div>
  )
}
