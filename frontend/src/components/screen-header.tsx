import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"
import { useLocation } from "react-router"

import { cn } from "@/lib/utils"

// The head of every app screen: a mono path eyebrow ("~/admin/access", from the URL
// unless given), the screen's one title as an h2 (the e2e specs wait for it), a muted
// description, and the screen's actions (tabs, buttons, stats) at the end, wrapping
// under the title on a phone. The eyebrow mirrors the address, so it's hidden from
// assistive technology, which has the title.
export type ScreenHeaderProps = {
  title: ReactNode
  eyebrow?: string
  icon?: LucideIcon
  description?: ReactNode
  actions?: ReactNode
  className?: string
}

function pathEyebrow(pathname: string) {
  const path = pathname.replace(/\/+$/, "").toLowerCase()
  return path ? `~${path}` : "~"
}

export function ScreenHeader({ title, eyebrow, icon: Icon, description, actions, className }: ScreenHeaderProps) {
  const { pathname } = useLocation()
  return (
    <header
      data-slot="screen-header"
      className={cn("flex flex-wrap items-end justify-between gap-x-6 gap-y-3", className)}
    >
      <div className="min-w-0 space-y-1">
        <p aria-hidden className="truncate font-mono text-xs text-muted-foreground">
          {eyebrow ?? pathEyebrow(pathname)}
        </p>
        <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-balance">
          {Icon && <Icon aria-hidden className="size-5 shrink-0 text-muted-foreground" />}
          {title}
        </h2>
        {description && <div className="max-w-2xl text-sm text-pretty text-muted-foreground">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}
