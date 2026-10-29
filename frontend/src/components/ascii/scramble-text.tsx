import { useEffect, useRef, type ElementType } from "react"

import { subscribe } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// Text that decodes into place: each new value starts as random characters that settle,
// left to right, within `duration` ms (at most 240). Drawn by the shared ASCII loop; with
// reduced motion or "Reduce effects" the new text simply appears.
//
// The decoding is aria-hidden; the real text sits beside it for assistive technology,
// so a screen reader only ever meets the final words. `once` decodes the first value
// only (a workspace name resolving), later ones swap in.
export type ScrambleTextProps = {
  text: string
  duration?: number
  charset?: string
  once?: boolean
  as?: ElementType
  className?: string
}

const CHARSET = "abcdefghijklmnopqrstuvwxyz0123456789#%&*+=-<>/\\"
const MAX_MS = 240

export function ScrambleText({ text, duration = MAX_MS, charset = CHARSET, once = false, as: Tag = "span", className }: ScrambleTextProps) {
  const shown = useRef<HTMLSpanElement>(null)
  const decoded = useRef(false)

  useEffect(() => {
    const el = shown.current
    if (!el) return
    if (once && decoded.current) {
      el.textContent = text
      return
    }
    const total = Math.min(duration, MAX_MS) / 1000
    let stopped = false
    const loop = subscribe(
      el,
      (t) => {
        if (stopped) return
        if (t >= total) {
          el.textContent = text
          stopped = true
          decoded.current = true
          // Done: leave the loop, which goes idle when nothing else moves.
          queueMicrotask(() => loop.stop())
          return
        }
        const settled = Math.floor((t / total) * text.length)
        let out = text.slice(0, settled)
        for (let i = settled; i < text.length; i++) {
          out += text[i] === " " ? " " : charset[Math.floor(Math.random() * charset.length)]
        }
        el.textContent = out
      },
      { fps: 30, still: Infinity, priority: "ui" },
    )
    return () => {
      stopped = true
      loop.stop()
    }
  }, [text, duration, charset, once])

  return (
    <Tag data-slot="scramble-text" className={cn("relative", className)}>
      <span ref={shown} aria-hidden className="whitespace-pre-wrap">
        {text}
      </span>
      <span className="sr-only">{text}</span>
    </Tag>
  )
}
