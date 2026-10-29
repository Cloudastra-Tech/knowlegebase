import { Fragment, useEffect, useRef } from "react"

import { redact, scrambleChar, span } from "@/components/ascii/decode"
import { lineSnippets } from "@/components/ascii/drift"
import { GlyphGrid } from "@/components/ascii/glyph-grid"
import { subscribe, whenFontsReady } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// The closing band: a still field of redacted records (the empty chat's, drawn once),
// and a heading that comes out of it. The first time the band is on screen its words
// are redaction blocks, and they resolve into the heading left to right, a character
// at a time; then nothing moves. With reduced motion or "Reduce effects" the heading
// is there from the start.

const GAP = 4 // rows between the field's lines
const FIELD = ["transparent", "color-mix(in oklab, var(--foreground) 11%, transparent)"]

// The field: lines of made-up records, redacted, a few rows apart, drawn once and on a
// resize. It never moves, so it may sit under the words.
export function RedactionField({ className }: { className?: string }) {
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const el = host.current
    const cv = canvas.current
    if (!el || !cv) return
    const grid = new GlyphGrid(el, cv, FIELD)
    const draw = () => {
      grid.measure()
      grid.clear()
      for (let r = 1, n = 0; r < grid.rows; r += GAP, n++) {
        const line = lineSnippets(n).map(redact).join("   ")
        const from = (n * 17) % 23
        for (let c = 0; c < grid.cols; c++) {
          const ch = line[(c + from) % line.length]
          if (ch !== " ") grid.put(c, r, ch, 1)
        }
      }
      grid.flush()
    }
    draw()
    const ro = new ResizeObserver(draw)
    ro.observe(el)
    let live = true
    whenFontsReady(el).then(() => live && draw())
    return () => {
      live = false
      ro.disconnect()
    }
  }, [])

  return (
    <div ref={host} aria-hidden className={cn("pointer-events-none overflow-hidden font-mono text-ascii leading-[1.15] select-none", className)}>
      <canvas ref={canvas} className="absolute top-0 left-0" />
    </div>
  )
}

const EACH = 0.035 // the next character starts this much later
const FLICKER = 0.14 // a character flickers before it reads

// Text that resolves out of redaction blocks, once, when it's on screen. The text is
// there for assistive technology from the start (sr-only); what's drawn is
// aria-hidden: each character in place, transparent while a block (or a flicker) sits
// over it, so nothing moves as it resolves.
export function ResolveText({ text, className }: { text: string; className?: string }) {
  const box = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const el = box.current
    if (!el) return
    const chars = [...el.querySelectorAll<HTMLElement>("[data-ch]")]
    const masks = chars.map((c) => c.firstElementChild as HTMLElement)
    const blocks = redact(text).replace(/ /g, "")
    const end = chars.length * EACH + FLICKER
    let finished = false
    const draw = (time: number) => {
      const t = time < 0 ? end : time
      const tick = Math.floor(t * 20)
      chars.forEach((c, i) => {
        const k = span(t, i * EACH, FLICKER)
        const state = k <= 0 ? "redacted" : k < 1 ? "flicker" : "read"
        if (c.dataset.state !== state) c.dataset.state = state
        const glyph = state === "redacted" ? blocks[i] : state === "flicker" ? scrambleChar(i, tick, 5) : ""
        if (masks[i].textContent !== glyph) masks[i].textContent = glyph
      })
      if (time >= 0 && t >= end && !finished) {
        finished = true
        queueMicrotask(() => loop.stop())
      }
    }
    const loop = subscribe(el, draw, { fps: 30, still: -1, priority: "hero", visibleAt: 0.6 })
    return () => loop.stop()
  }, [text])

  const words = text.split(" ")
  return (
    <span className={className}>
      <span className="sr-only">{text}</span>
      <span ref={box} aria-hidden>
        {words.map((word, w) => (
          <Fragment key={w}>
            {w > 0 && " "}
            <span className="whitespace-nowrap">
              {[...word].map((ch, i) => (
                <span
                  key={i}
                  data-ch
                  data-state="redacted"
                  className="relative data-[state=flicker]:text-transparent data-[state=redacted]:text-transparent"
                >
                  <span className="absolute inset-0 flex items-center justify-center font-mono font-normal text-muted-foreground">
                    {redact(ch)}
                  </span>
                  {ch}
                </span>
              ))}
            </span>
          </Fragment>
        ))}
      </span>
    </span>
  )
}
