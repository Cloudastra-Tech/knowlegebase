import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"

import { Breadcrumbs } from "@/components/breadcrumbs"
import { useTrail, type CrumbData, type Trail } from "@/lib/breadcrumbs"
import { cn } from "@/lib/utils"

// The head of every app screen: breadcrumbs (where it sits, from the URL and what the
// screen passes in `crumbs`: a space's name, its open tab; on a phone, a detail view's
// "← Spaces"), the screen's one title as an h2 (the e2e specs wait for it), a muted
// description, and the screen's actions (tabs, buttons, stats) at the end, wrapping
// under the title on a phone.
export type ScreenHeaderProps = {
  title: ReactNode
  crumbs?: CrumbData
  trail?: Trail // a trail of its own instead (the kit's samples)
  icon?: LucideIcon
  description?: ReactNode
  actions?: ReactNode
  className?: string
}

export function ScreenHeader({
  title,
  crumbs,
  trail,
  icon: Icon,
  description,
  actions,
  className,
}: ScreenHeaderProps) {
  const fromAddress = useTrail(crumbs)
  return (
    <header
      data-slot="screen-header"
      className={cn("flex flex-wrap items-end justify-between gap-x-6 gap-y-3", className)}
    >
      <div className="min-w-0 space-y-1">
        <Breadcrumbs trail={trail ?? fromAddress} />
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
