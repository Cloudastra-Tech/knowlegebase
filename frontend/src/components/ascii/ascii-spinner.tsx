import type { CSSProperties } from "react"

import "@/components/ascii/ascii.css"
import { cn } from "@/lib/utils"

// The app's spinner, in place of lucide's Loader2: a mono character stepping through
// its frames every 80ms. The frames are a column moved one line at a time by a CSS
// steps() animation, so it costs no script. With reduced motion or "Reduce effects" it
// is a still "•••" that pulses its opacity.
//
// The art is aria-hidden. `label` (default "Loading") is read out, in a role="status";
// pass label="" where words beside it already say what's happening (a "Saving…" button).
const FRAMES = {
  braille: "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
  dots: "⣾⣽⣻⢿⡿⣟⣯⣷",
  line: "|/-\\",
}

const SIZES = { xs: "text-xs", sm: "text-sm", md: "text-base" }

export type AsciiSpinnerProps = {
  variant?: keyof typeof FRAMES
  size?: keyof typeof SIZES
  label?: string
  className?: string
}

export function AsciiSpinner({ variant = "braille", size = "sm", label = "Loading", className }: AsciiSpinnerProps) {
  const frames = [...FRAMES[variant]]
  return (
    <span
      data-slot="ascii-spinner"
      role={label ? "status" : undefined}
      className={cn("inline-flex shrink-0 items-center font-mono leading-none", SIZES[size], className)}
    >
      <span aria-hidden className="inline-block h-[1lh] overflow-hidden select-none">
        <span
          className="ascii-spin-frames flex flex-col"
          style={{ "--ascii-frames": frames.length } as CSSProperties}
        >
          {frames.map((f, i) => (
            <span key={i} className="block h-[1lh] text-center">
              {f}
            </span>
          ))}
        </span>
        <span className="ascii-spin-still tracking-tighter">•••</span>
      </span>
      {label && <span className="sr-only">{label}</span>}
    </span>
  )
}
