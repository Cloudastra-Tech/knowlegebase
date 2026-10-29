import type { CSSProperties } from "react"

import anthropic from "@lobehub/icons-static-svg/icons/anthropic.svg"
import bedrock from "@lobehub/icons-static-svg/icons/bedrock.svg"
import gemini from "@lobehub/icons-static-svg/icons/gemini.svg"
import openai from "@lobehub/icons-static-svg/icons/openai.svg"

import { cn } from "@/lib/utils"

// The model providers' marks (models.py's REGISTRY names), one colour like the icons
// beside them. Each is a file the build serves once and caches.
const MARKS: Record<string, string> = { anthropic, bedrock, gemini, google: gemini, openai }

// A provider's mark, drawn in the current text colour (the SVG as a mask). Nothing for
// a provider without one. Decorative: the model's name is always next to it.
export function ProviderMark({ provider, className }: { provider: string; className?: string }) {
  const src = MARKS[provider]
  if (!src) return null
  const mask = `url("${src}") center / contain no-repeat`
  return (
    <span
      aria-hidden
      data-slot="provider-mark"
      className={cn("inline-block size-3.5 shrink-0 bg-current", className)}
      style={{ mask, WebkitMask: mask } as CSSProperties}
    />
  )
}
