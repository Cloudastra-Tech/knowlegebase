import type { CSSProperties, ReactNode } from "react"

import "@/components/ascii/ascii.css"
import { ART, type ArtName } from "@/components/ascii/art"
import { cn } from "@/lib/utils"

// Every "No … yet": a small hand-drawn piece of art over the real words and, where
// there's a next step, its action. The art is aria-hidden. It moves at most once every
// 2s (a scan line down it, or a cursor), and not at all with reduced motion or
// "Reduce effects".
export type AsciiEmptyStateProps = {
  art: ArtName | readonly string[]
  title: string
  description?: ReactNode
  action?: ReactNode
  size?: "sm" | "md"
  animate?: "none" | "scan" | "blink"
  headingLevel?: 3 | 4
  className?: string
}

export function AsciiEmptyState({
  art,
  title,
  description,
  action,
  size = "md",
  animate = "none",
  headingLevel = 3,
  className,
}: AsciiEmptyStateProps) {
  const lines = typeof art === "string" ? ART[art] : art
  const Heading = headingLevel === 3 ? "h3" : "h4"
  return (
    <div
      data-slot="ascii-empty-state"
      className={cn(
        "flex flex-col items-center text-center",
        size === "sm" ? "gap-2 py-6" : "gap-3 py-10",
        className,
      )}
    >
      {/* At the full muted ink: axe weighs aria-hidden text on contrast as well. */}
      <div aria-hidden className="relative select-none">
        <pre className="m-0 font-mono text-ascii text-muted-foreground">
          {lines.join("\n")}
          {animate === "blink" && <span className="ascii-blink text-signal">▌</span>}
        </pre>
        {animate === "scan" && (
          <span
            className="ascii-scan pointer-events-none absolute inset-x-0 top-0 h-[1.15em] bg-signal/15 text-ascii"
            style={{ "--ascii-rows": lines.length, "--ascii-scan-to": `${(lines.length - 1) * 1.15}em` } as CSSProperties}
          />
        )}
      </div>
      <div className="space-y-1">
        <Heading className={cn("font-medium text-balance", size === "sm" ? "text-sm" : "text-base")}>{title}</Heading>
        {description && <div className="mx-auto max-w-sm text-sm text-pretty text-muted-foreground">{description}</div>}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}
