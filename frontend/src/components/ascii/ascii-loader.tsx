import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { cn } from "@/lib/utils"

// A page or a panel still loading (LazyPage, route fallbacks): the spinner and a mono
// "loading…". It keeps data-slot="skeleton", which the e2e specs wait to see gone.
export function AsciiLoader({ label = "Loading", className }: { label?: string; className?: string }) {
  return (
    <div
      data-slot="skeleton"
      className={cn("flex items-center gap-2 font-mono text-xs text-muted-foreground", className)}
    >
      <AsciiSpinner label={label} size="sm" />
      <span aria-hidden className="lowercase">
        {label}…
      </span>
    </div>
  )
}
