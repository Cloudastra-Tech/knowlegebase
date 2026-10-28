import { useId, type ComponentType, type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

// The pieces every Settings section is made of (settings-view.tsx), so the sections
// share one rhythm: a head (the section's title, one line on what it is, its one
// primary action), then groups of rows or a list, 24px apart.

// A section: its title is an h3 under the screen's h2 ("Settings"). On a phone the h2
// is the section's own name, so the h3 is there for assistive technology alone.
export function SettingsSection({
  title,
  description,
  action,
  children,
}: {
  title: string
  description?: ReactNode
  action?: ReactNode
  children: ReactNode
}) {
  const id = useId()
  return (
    <section aria-labelledby={id} data-slot="settings-section" className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1 basis-64">
          <h3 id={id} className="text-base font-semibold tracking-tight text-balance max-md:sr-only">
            {title}
          </h3>
          {description && (
            <p className="max-w-xl text-sm text-pretty text-muted-foreground md:mt-1">{description}</p>
          )}
        </div>
        {action && <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>}
      </div>
      {children}
    </section>
  )
}

// A group of rows under a small label: an opaque card, a hairline between rows.
export function SettingsGroup({
  title,
  action,
  children,
  className,
}: {
  title?: string
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  const id = useId()
  return (
    <section aria-labelledby={title ? id : undefined} className={cn("flex min-w-0 flex-col gap-2", className)}>
      {(title || action) && (
        <div className="flex min-h-7 items-center justify-between gap-3">
          {title && (
            <h4 id={id} className="text-sm font-medium text-muted-foreground">
              {title}
            </h4>
          )}
          {action}
        </div>
      )}
      <div className="divide-y overflow-hidden rounded-2xl border bg-card text-card-foreground">{children}</div>
    </section>
  )
}

// One setting: what it is on the left, its value or control on the right (under it,
// on a phone, when there's no room).
export function SettingsRow({
  label,
  description,
  htmlFor,
  children,
}: {
  label: ReactNode
  description?: ReactNode
  htmlFor?: string
  children?: ReactNode
}) {
  return (
    <div className="flex min-h-14 flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3">
      <div className="min-w-0 flex-1 basis-48">
        {htmlFor ? (
          <Label htmlFor={htmlFor}>{label}</Label>
        ) : (
          <div className="text-sm font-medium">{label}</div>
        )}
        {description && <div className="mt-0.5 text-sm text-pretty text-muted-foreground">{description}</div>}
      </div>
      {children !== undefined && <div className="flex max-w-full min-w-0 items-center gap-2">{children}</div>}
    </div>
  )
}

// A list of things (prompts, keys, digests): one row each, the actions in its menu.
export function SettingsList({ label, children }: { label: string; children: ReactNode }) {
  return (
    <ul aria-label={label} className="divide-y overflow-hidden rounded-2xl border bg-card text-card-foreground">
      {children}
    </ul>
  )
}

// While a section's data loads: rows shaped like the ones coming.
export function SettingsSkeleton({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div aria-busy aria-label={label} className="divide-y overflow-hidden rounded-2xl border bg-card">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3.5">
          <Skeleton className="size-8 shrink-0 rounded-lg" />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <Skeleton className="h-3.5 w-2/5" />
            <Skeleton className="h-3 w-3/5" />
          </div>
        </div>
      ))}
    </div>
  )
}

// Nothing here yet: a lucide icon, a short title, one sentence on what this is and
// why you'd use it, the one action, and (optionally) an example of what one looks
// like. It arrives with a 150ms fade (opacity only: it sits on no glass), once.
export function SettingsEmpty({
  icon: Icon,
  title,
  description,
  action,
  example,
  className,
}: {
  icon: ComponentType
  title: string
  description: ReactNode
  action?: ReactNode
  example?: ReactNode
  className?: string
}) {
  return (
    <Empty
      data-slot="settings-empty"
      className={cn(
        "gap-5 rounded-2xl border border-dashed px-6 py-10 animate-in duration-(--dur-swap) ease-out-strong fade-in",
        className,
      )}
    >
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon />
        </EmptyMedia>
        <EmptyTitle role="heading" aria-level={4} className="text-base">
          {title}
        </EmptyTitle>
        <EmptyDescription className="text-pretty">{description}</EmptyDescription>
      </EmptyHeader>
      {action && <EmptyContent>{action}</EmptyContent>}
      {example && <div className="w-full max-w-md text-left">{example}</div>}
    </Empty>
  )
}

// "For example": what one of these looks like, quiet, under an empty state's action.
export function SettingsExample({ children }: { children: ReactNode }) {
  return (
    <figure className="flex flex-col gap-1.5">
      <figcaption className="text-xs text-muted-foreground">For example</figcaption>
      <div className="rounded-xl border bg-muted/40 px-3.5 py-3 text-sm">{children}</div>
    </figure>
  )
}

// A section that couldn't load: what happened and what to do.
export function SettingsError({ what, onRetry }: { what: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border px-4 py-3 text-sm">
      <p className="min-w-0 flex-1 text-pretty">
        Couldn't load {what}. Check your connection, then try again.
      </p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}
