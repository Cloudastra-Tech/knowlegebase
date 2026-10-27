import { cn } from "@/lib/utils"

// A connection's or a job's state as a dot with its words beside it (a colour never
// stands alone). Only "live" moves: a ring that pings outwards, by opacity and scale,
// and not with reduced motion.
export type StatusDotProps = {
  status: "live" | "syncing" | "warning" | "error" | "off"
  label: string
  className?: string
}

const DOTS = {
  live: "bg-signal",
  syncing: "bg-signal/60",
  warning: "bg-warning",
  error: "bg-destructive",
  off: "bg-muted-foreground/50",
}

export function StatusDot({ status, label, className }: StatusDotProps) {
  return (
    <span data-slot="status-dot" data-status={status} className={cn("inline-flex items-center gap-1.5 text-xs", className)}>
      <span aria-hidden className="relative inline-flex size-2">
        {status === "live" && (
          <span className="absolute inset-0 rounded-full bg-signal/60 motion-safe:animate-ping [:root[data-effects=reduced]_&]:animate-none" />
        )}
        <span className={cn("relative inline-flex size-2 rounded-full", DOTS[status])} />
      </span>
      <span>{label}</span>
    </span>
  )
}
