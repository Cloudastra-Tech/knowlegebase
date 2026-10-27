import type { ComponentProps, ElementType } from "react"

import { cn } from "@/lib/utils"

// A bare glass surface, one of the three tiers (DESIGN.md, "Glass"):
// - chrome: blurred, for the header, the chat sidebar, the composer, landing cards;
// - floating: blurred and denser, for things that float over the page;
// - subtle: no blur, for panels inside a screen (the field is faded out under content,
//   so blurring there buys nothing).
// No more than three blurred surfaces on screen at once, and never glass in glass: a
// surface inside a GlassPanel is "subtle" or solid. `noise` adds static grain to a
// large blurred surface.
export type GlassTier = "chrome" | "floating" | "subtle"

const TIERS: Record<GlassTier, string> = {
  chrome: "glass",
  floating: "glass-strong",
  subtle: "glass-subtle",
}

export type GlassPanelProps<T extends ElementType = "div"> = {
  as?: T
  tier?: GlassTier
  noise?: boolean
} & Omit<ComponentProps<T>, "as">

export function GlassPanel<T extends ElementType = "div">({
  as,
  tier = "chrome",
  noise = false,
  className,
  ...props
}: GlassPanelProps<T>) {
  const Tag: ElementType = as ?? "div"
  return (
    <Tag
      data-slot="glass-panel"
      data-tier={tier}
      className={cn(TIERS[tier], noise && tier !== "subtle" && "glass-noise", "rounded-2xl", className)}
      {...props}
    />
  )
}
