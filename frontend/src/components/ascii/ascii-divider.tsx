import { cn } from "@/lib/utils"

// A section label on a rule, in mono: "── providers ──────────". The label is real
// text (as="h3" makes it the section's heading); the rule is aria-hidden. Without a
// label it's a plain separator.
export type AsciiDividerProps = {
  as?: "div" | "h2" | "h3" | "h4"
  id?: string
  label?: string
  align?: "start" | "center"
  char?: "─" | "·" | "═" | "╌"
  className?: string
}

export function AsciiDivider({ as: Tag = "div", id, label, align = "start", char = "─", className }: AsciiDividerProps) {
  // Longer than any screen; the box clips it.
  const rule = (
    <span aria-hidden className="min-w-0 flex-1 overflow-hidden whitespace-nowrap select-none">
      {char.repeat(240)}
    </span>
  )
  return (
    <Tag
      id={id}
      data-slot="ascii-divider"
      role={label ? undefined : "separator"}
      className={cn(
        "flex items-center gap-2 font-mono text-xs leading-none font-normal text-muted-foreground",
        className,
      )}
    >
      {label ? (
        <>
          {align === "center" ? (
            rule
          ) : (
            <span aria-hidden className="shrink-0 select-none">
              {char.repeat(2)}
            </span>
          )}
          <span className="shrink-0 lowercase">{label}</span>
          {rule}
        </>
      ) : (
        rule
      )}
    </Tag>
  )
}
