import { useEffect, useRef, useState } from "react"
import type { LucideIcon } from "lucide-react"

import { GlyphGrid } from "@/components/ascii/glyph-grid"
import { drawRoute, layoutRoute, type RouteMode } from "@/components/ascii/route"
import { usePickedBy } from "@/components/landing-scene"
import { ProviderMark } from "@/components/provider-mark"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { subscribe, whenFontsReady } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// Where the model runs, as the path a question and your documents take (the three
// choices on the Assistant screen; ascii/route.ts draws it). Packets of characters
// travel the lanes; picking a choice re-routes them and the boxes' labels re-form.
// It cycles through the choices while it's on screen, until one is picked (from the
// keyboard: at once). With reduced motion or "Reduce effects": the diagram, still.

export type ModelOption = { id: RouteMode; title: string; marks: string[]; icon?: LucideIcon; body: string }

const EVERY = 6.5 // seconds on each choice while it cycles
const FPS = 15 // packets step a cell at a time
const TONES = [
  "transparent",
  "color-mix(in oklab, var(--foreground) 30%, transparent)", // lines
  "color-mix(in oklab, var(--foreground) 55%, transparent)", // titles
  "color-mix(in oklab, var(--foreground) 92%, transparent)", // labels
  "var(--signal)", // a label re-forming, the passages a question quotes
  "color-mix(in oklab, var(--foreground) 62%, transparent)", // document text
  "color-mix(in oklab, var(--foreground) 88%, transparent)", // a question
]
const WIDE = "(min-width: 768px)" // md: the diagram in one row

export function ModelRoute({ options, className }: { options: ModelOption[]; className?: string }) {
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [mode, setMode] = useState<RouteMode>(options[0].id)
  const { by, listeners } = usePickedBy()
  // What the scene reads each frame: the choice, the one before it and when it changed
  // (scene seconds; -Infinity for a change that doesn't animate), and whether to cycle.
  const scene = useRef({ mode: options[0].id, from: options[0].id, at: -Infinity, now: 0, cycling: true })

  useEffect(() => {
    const el = host.current
    const cv = canvas.current
    if (!el || !cv) return
    const grid = new GlyphGrid(el, cv, TONES)
    const media = window.matchMedia(WIDE)
    let layout = layoutRoute(1, media.matches)
    const measure = () => {
      grid.measure()
      layout = layoutRoute(grid.cols, media.matches)
      grid.measure({ cols: grid.cols, rows: layout.rows })
    }
    measure()
    const s = scene.current
    let cycleAt = 0
    const draw = (time: number) => {
      const t = time < 0 ? 0 : time
      s.now = t
      if (time >= 0 && s.cycling && t - cycleAt >= EVERY) {
        const i = options.findIndex((o) => o.id === s.mode)
        const next = options[(i + 1) % options.length].id
        cycleAt = t
        Object.assign(s, { from: s.mode, mode: next, at: t })
        setMode(next)
      }
      grid.clear()
      drawRoute(grid, layout, time < 0 ? 2.2 : t, s.mode, s.from, time < 0 ? Infinity : t - s.at)
      grid.flush()
    }
    const loop = subscribe(el, draw, { fps: FPS, still: -1, priority: "hero", visibleAt: 0.3 })
    const relayout = () => {
      measure()
      loop.redraw()
    }
    const ro = new ResizeObserver(relayout)
    ro.observe(el)
    media.addEventListener("change", relayout)
    let live = true
    whenFontsReady(el).then(() => live && relayout())
    return () => {
      live = false
      loop.stop()
      ro.disconnect()
      media.removeEventListener("change", relayout)
    }
  }, [options])

  const pick = (id: string) => {
    const s = scene.current
    const next = id as RouteMode
    s.cycling = false
    if (next === s.mode) return
    // From the keyboard: the new route at once.
    Object.assign(s, { from: s.mode, mode: next, at: by.current === "keyboard" ? -Infinity : s.now })
    setMode(next)
  }

  return (
    <div className={cn("flex flex-col gap-6 rounded-2xl border bg-card/40 p-5 sm:p-7", className)}>
      <Tabs panels={false} value={mode} onValueChange={pick}>
        <TabsList aria-label="Where the model runs" {...listeners}>
          {options.map(({ id, title, marks, icon: Icon }) => (
            <TabsTrigger key={id} value={id} className="gap-1.5 px-3">
              <span className="hidden items-center gap-1 sm:flex">
                {marks.map((m) => (
                  <ProviderMark key={m} provider={m} className="size-3.5" />
                ))}
                {Icon && <Icon aria-hidden className="size-3.5" />}
              </span>
              {title}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {/* The diagram's rows are fixed (15 stacked, 9 in one row), so its height is
          known before it's drawn. */}
      <div
        ref={host}
        aria-hidden
        data-slot="model-route"
        className="relative h-[calc(15*1.15*11px)] font-mono text-ascii leading-[1.15] select-none md:h-[calc(9*1.15*12px)] md:text-xs md:leading-[1.15]"
      >
        <canvas ref={canvas} className="absolute top-0 left-0" />
      </div>
      <p className="-mt-2 flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-muted-foreground">
        <span>▪ document text</span>
        <span>? a question</span>
        <span>
          <span className="text-signal">¶</span> the passages it quotes
        </span>
      </p>
      <div className="grid">
        {options.map((o) => (
          <p
            key={o.id}
            aria-hidden={o.id !== mode || undefined}
            className={cn("max-w-[68ch] text-pretty text-muted-foreground [grid-area:1/1]", o.id !== mode && "invisible")}
          >
            {o.body}
          </p>
        ))}
      </div>
    </div>
  )
}
