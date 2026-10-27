import { useId, type ComponentProps, type ReactNode } from "react"

import { cn } from "@/lib/utils"

// A panel inside a screen, in place of the ad-hoc `rounded-2xl border p-5` boxes: an
// optional head (a mono eyebrow, a title, actions at the end) over its content.
// tone: "subtle" (the default) is glass without blur; "glass" is the blurred chrome
// tier, for a panel that sits straight on the field; "solid" is an opaque card, for
// reading and for tables. `corners` puts mono ┌ ┐ └ ┘ marks on it, as decoration.
// With a title it's a region named by it.
export type PanelProps = {
  tone?: "subtle" | "glass" | "solid"
  title?: ReactNode
  titleAs?: "h3" | "h4"
  eyebrow?: string
  actions?: ReactNode
  corners?: boolean
  children: ReactNode
} & Omit<ComponentProps<"section">, "title">

const TONES = {
  subtle: "glass-subtle",
  glass: "glass",
  solid: "border bg-card text-card-foreground",
}

export function Panel({
  tone = "subtle",
  title,
  titleAs: Title = "h3",
  eyebrow,
  actions,
  corners = false,
  className,
  children,
  ...props
}: PanelProps) {
  const id = useId()
  const head = title || eyebrow || actions
  return (
    <section
      data-slot="panel"
      data-tone={tone}
      aria-labelledby={title ? id : undefined}
      className={cn("relative rounded-2xl p-5", TONES[tone], className)}
      {...props}
    >
      {corners && <Corners />}
      {head && (
        <div data-slot="panel-header" className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0 space-y-1">
            {eyebrow && (
              <p aria-hidden={title ? true : undefined} className="font-mono text-xs text-muted-foreground lowercase">
                {eyebrow}
              </p>
            )}
            {title && (
              <Title id={id} className="text-base font-semibold tracking-tight text-balance">
                {title}
              </Title>
            )}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

function Corners() {
  const mark = "pointer-events-none absolute font-mono text-xs leading-none text-muted-foreground/60 select-none"
  return (
    <>
      <span aria-hidden className={cn(mark, "top-1.5 left-2")}>┌</span>
      <span aria-hidden className={cn(mark, "top-1.5 right-2")}>┐</span>
      <span aria-hidden className={cn(mark, "bottom-1.5 left-2")}>└</span>
      <span aria-hidden className={cn(mark, "right-2 bottom-1.5")}>┘</span>
    </>
  )
}
