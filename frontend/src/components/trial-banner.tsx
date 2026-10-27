import type { ComponentProps } from "react"
import { Link } from "react-router"

import type { User } from "@/lib/api"
import { cn } from "@/lib/utils"

// A thin note under the header (the trial, support signed in, a workspace being
// deleted): a hairline strip of subtle glass with a mono glyph in front. No blur: the
// header above it is the chrome that blurs. The glyph is aria-hidden; the words say it.
export function ShellStrip({
  glyph,
  tone = "muted",
  className,
  children,
  ...props
}: ComponentProps<"div"> & { glyph: string; tone?: "muted" | "warning" | "destructive" }) {
  return (
    <div
      data-print="hide"
      data-slot="shell-strip"
      className={cn(
        "flex items-baseline justify-center gap-2 border-b px-4 py-1.5 text-center text-xs",
        tone === "destructive"
          ? "border-destructive/30 bg-destructive/5 text-destructive"
          : "border-(--glass-border) bg-(--glass-bg-subtle) text-muted-foreground",
        className,
      )}
      {...props}
    >
      <span aria-hidden className={cn("shrink-0 font-mono font-medium", tone === "warning" && "text-warning")}>
        {glyph}
      </span>
      <span className="text-pretty">{children}</span>
    </div>
  )
}

// With BILLING=on (user.billing from /api/auth/me): the trial's countdown for the
// people who can pick a plan, and, for everyone, why a read-only workspace takes no
// questions. Nothing with billing off or for a workspace it doesn't apply to.
export function TrialBanner({ user }: { user: User }) {
  const b = user.billing
  if (!b?.status) return null
  const link = b.can_view && (
    <Link to="/billing" className="font-medium underline underline-offset-2">
      {b.owner ? "Pick a plan" : "See the plan"}
    </Link>
  )

  let text: string | null = null
  if (b.read_only)
    text = b.expired
      ? "The trial has ended, so this workspace is read-only: you can read and export it, but not ask or change anything."
      : "The subscription was canceled, so this workspace is read-only."
  else if (!b.can_view) return null
  else if (b.status === "trialing" && b.trial_days_left !== null)
    text =
      b.trial_days_left <= 0
        ? "Your trial ends today."
        : `${b.trial_days_left} day${b.trial_days_left === 1 ? "" : "s"} left in your trial.`
  else if (b.status === "past_due") text = "Stripe couldn't take the last payment."
  if (!text) return null

  const urgent = b.read_only || b.status === "past_due" || (b.trial_days_left ?? 99) <= 3
  return (
    <ShellStrip role="status" data-testid="trial-banner" glyph="!" tone={urgent ? "destructive" : "warning"}>
      {text} {link}
      {link && "."}
    </ShellStrip>
  )
}
