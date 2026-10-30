import { useEffect, useRef, type ReactNode } from "react"
import type { LucideIcon } from "lucide-react"

import { GlyphGrid } from "@/components/ascii/glyph-grid"
import { drawRoute, layoutRoute, type RouteMode } from "@/components/ascii/route"
import { ScrollSteps } from "@/components/landing-steps"
import { ProviderMark } from "@/components/provider-mark"
import { subscribe, whenFontsReady, type LoopHandle } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// Where the model runs, as the path a question and your documents take (the three
// choices on the Assistant screen; ascii/route.ts draws it). Packets of characters
// travel the lanes. Told in steps as the page scrolls (landing-steps.tsx): reaching a
// choice re-routes the packets and the boxes' labels re-form, from the choice before.
// With reduced motion or "Reduce effects": the diagram, still, the new route at once.

export type ModelOption = { id: RouteMode; title: string; marks: string[]; icon?: LucideIcon; body: string }

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
const REROUTE_AT = 0.6 // a phone's card: seconds in view before it re-routes from the choice before

export function ModelRoute({ options, head, className }: { options: ModelOption[]; head: ReactNode; className?: string }) {
  return (
    <ScrollSteps
      head={head}
      className={className}
      columns="lg:grid-cols-[minmax(0,4fr)_minmax(0,8fr)]"
      steps={options.map(({ id, title, marks, icon: Icon, body }) => ({
        id,
        title,
        body,
        label: (
          <>
            {marks.map((m) => (
              <ProviderMark key={m} provider={m} className="size-4" />
            ))}
            {Icon && <Icon aria-hidden className="size-4" />}
          </>
        ),
      }))}
      caption={(i) => options[i].title}
      visual={(active) => <RouteCard mode={options[active].id} />}
      stepVisual={(i) => <RouteCard mode={options[i].id} from={options[i - 1]?.id} />}
    />
  )
}

// The diagram for `mode`; a new mode re-routes from the one shown. `from`: start on
// that choice and re-route from it once the card has been in view a moment.
function RouteCard({ mode, from, className }: { mode: RouteMode; from?: RouteMode; className?: string }) {
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const loop = useRef<LoopHandle | null>(null)
  // What the scene reads each frame: the choice, the one before it and when it changed
  // (scene seconds; -Infinity for a change that doesn't animate).
  const scene = useRef({ mode, from: from ?? mode, at: from && from !== mode ? REROUTE_AT : -Infinity, now: 0 })

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
    const draw = (time: number) => {
      const t = time < 0 ? 0 : time
      s.now = t
      // Still, or not yet at its change: the settled diagram of the choice it shows.
      const settled = time < 0 || t < s.at
      grid.clear()
      drawRoute(grid, layout, time < 0 ? 2.2 : t, settled && time >= 0 ? s.from : s.mode, s.from, settled ? Infinity : t - s.at)
      grid.flush()
    }
    const handle = subscribe(el, draw, { fps: FPS, still: -1, priority: "hero", visibleAt: 0.3 })
    loop.current = handle
    const relayout = () => {
      measure()
      handle.redraw()
    }
    const ro = new ResizeObserver(relayout)
    ro.observe(el)
    media.addEventListener("change", relayout)
    let live = true
    whenFontsReady(el).then(() => live && relayout())
    return () => {
      live = false
      handle.stop()
      loop.current = null
      ro.disconnect()
      media.removeEventListener("change", relayout)
    }
  }, [])

  // A new choice re-routes from the one shown, from the next frame (still: at once).
  useEffect(() => {
    const s = scene.current
    if (mode === s.mode) return
    const shown = s.now < s.at ? s.from : s.mode
    Object.assign(s, { from: shown, mode, at: s.now })
    loop.current?.redraw()
  }, [mode])

  return (
    <div className={cn("flex flex-col gap-6 rounded-2xl border bg-card/40 p-5 sm:p-7", className)}>
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
    </div>
  )
}
