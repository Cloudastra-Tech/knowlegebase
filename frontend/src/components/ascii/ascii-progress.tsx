import { cn } from "@/lib/utils"

// Progress as a row of cells, "[██████░░░░] 62%" (indexing, an upload, a plan's usage).
// It moves a whole cell at a time. A real progressbar for assistive technology, named
// by `label`; the cells are aria-hidden.
export type AsciiProgressProps = {
  value: number
  max?: number
  cells?: number
  label: string
  showValue?: boolean
  className?: string
}

export function AsciiProgress({ value, max = 100, cells = 12, label, showValue = true, className }: AsciiProgressProps) {
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0
  const filled = Math.round(ratio * cells)
  const percent = Math.round(ratio * 100)
  return (
    <div
      data-slot="ascii-progress"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.min(max, Math.max(0, value))}
      aria-valuetext={`${percent}%`}
      className={cn("flex w-fit items-center gap-2 font-mono text-xs whitespace-nowrap tabular-nums", className)}
    >
      <span aria-hidden className="select-none">
        <span className="text-muted-foreground">[</span>
        <span className="text-signal">{"█".repeat(filled)}</span>
        <span className="text-muted-foreground">{"░".repeat(cells - filled)}</span>
        <span className="text-muted-foreground">]</span>
      </span>
      {showValue && <span className="text-muted-foreground">{percent}%</span>}
    </div>
  )
}
