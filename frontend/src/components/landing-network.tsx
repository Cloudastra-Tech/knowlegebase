import { useEffect, useRef, useState } from "react"
import { FileText, type LucideIcon } from "lucide-react"

import { GlyphGrid } from "@/components/ascii/glyph-grid"
import { drawIndex, drawLines, drawPackets, routes, type NetLayout, type Node } from "@/components/ascii/network"
import { BrandMark } from "@/components/brand-mark"
import { Badge } from "@/components/ui/badge"
import { subscribe, whenFontsReady } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// The connectors, joined to one index by ASCII lines, with characters flowing along
// them into the index and never out: it reads them, it writes to none of them
// (ascii/network.ts draws it). The connectors are a real list, grouped, with their
// marks: the lines are a canvas behind it, aria-hidden. Pointing at a connector, or
// focusing it, lights its line and says what it last synced (an example). The flow
// runs only on screen; with reduced motion or "Reduce effects" the lines are still.

export type Source = { name: string; brand?: string; icon?: LucideIcon; note?: string; sync: string }
export type SourceGroup = { group: string; items: Source[] }

const FPS = 15
const TONES = [
  "transparent",
  "color-mix(in oklab, var(--foreground) 20%, transparent)", // lines
  "var(--signal)", // a lit line
  "color-mix(in oklab, var(--foreground) 50%, transparent)", // what flows
  "var(--signal)", // what flows on a lit line
  "color-mix(in oklab, var(--foreground) 40%, transparent)", // the index's box
  "color-mix(in oklab, var(--foreground) 85%, transparent)", // its name
]
const WIDE = "(min-width: 1024px)" // lg: the groups either side of the index
const INDEX_W = 11

export function SourceNetwork({ groups, className }: { groups: SourceGroup[]; className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const middle = useRef<HTMLDivElement>(null)
  const [lit, setLit] = useState(-1)
  const litRef = useRef(-1)
  const wake = useRef<() => void>(() => {})
  const items = groups.flatMap((g) => g.items)
  // The groups either side of the index, split where the two sides come out closest
  // in height (a group's rows plus its heading), and both centred on it.
  const height = (g: SourceGroup) => g.items.length + 1.5
  const total = groups.reduce((n, g) => n + height(g), 0)
  let split = 1
  for (let k = 1, run = 0; k < groups.length; k++) {
    run += height(groups[k - 1])
    if (Math.abs(total - 2 * run) < Math.abs(total - 2 * groups.slice(0, split).reduce((n, g) => n + height(g), 0))) split = k
  }
  const left = groups.slice(0, split)
  const right = groups.slice(split)

  useEffect(() => {
    litRef.current = lit
    wake.current()
  }, [lit])

  useEffect(() => {
    const el = box.current
    const hostEl = host.current
    const cv = canvas.current
    if (!el || !hostEl || !cv) return
    const grid = new GlyphGrid(hostEl, cv, TONES)
    const media = window.matchMedia(WIDE)
    let layout: NetLayout | null = null
    let paths: ReturnType<typeof routes> = []

    // Where each connector sits, in cells of the grid.
    const measure = () => {
      grid.measure()
      const origin = el.getBoundingClientRect()
      const cell = (x: number, y: number) => ({ c: (x - origin.left) / grid.cw, r: (y - origin.top) / grid.ch })
      const wide = media.matches
      const lis = [...el.querySelectorAll<HTMLElement>("[data-source]")]
      const mid = middle.current?.getBoundingClientRect()
      const nodes: Node[] = lis.map((li) => {
        const r = li.getBoundingClientRect()
        const side = wide && li.dataset.side === "right" ? 1 : -1
        const at = cell(side === -1 ? r.right : r.left, r.top + r.height / 2)
        return {
          row: Math.floor(at.r),
          col: side === -1 ? Math.ceil(at.c) + 1 : Math.floor(at.c) - 2,
          side,
          soon: li.dataset.soon === "",
        }
      })
      if (wide && mid) {
        const a = cell(mid.left, mid.top + mid.height / 2)
        const b = cell(mid.right, mid.top)
        const l = Math.ceil(a.c) + 1
        const r = Math.floor(b.c) - 2
        layout = {
          nodes,
          index: { col: Math.round((l + r - INDEX_W) / 2) + 1, row: Math.floor(a.r), w: INDEX_W },
          bus: { left: l, right: r },
          below: false,
        }
      } else {
        const bus = grid.cols - 2
        layout = {
          nodes,
          index: { col: bus - Math.floor(INDEX_W / 2), row: grid.rows - 2, w: INDEX_W },
          bus: { left: bus, right: -1 },
          below: true,
        }
      }
      paths = routes(layout)
    }

    const draw = (time: number) => {
      if (!layout) return
      grid.clear()
      drawLines(grid, layout, paths, litRef.current)
      drawIndex(grid, layout)
      drawPackets(grid, layout, paths, time < 0 ? 4 : time, litRef.current)
      grid.flush()
    }
    measure()
    const loop = subscribe(hostEl, draw, { fps: FPS, still: -1, priority: "hero" })
    wake.current = loop.redraw
    const relayout = () => {
      measure()
      loop.redraw()
    }
    const ro = new ResizeObserver(relayout)
    ro.observe(el)
    media.addEventListener("change", relayout)
    let live = true
    whenFontsReady(hostEl).then(() => live && relayout())
    return () => {
      live = false
      wake.current = () => {}
      loop.stop()
      ro.disconnect()
      media.removeEventListener("change", relayout)
    }
  }, [])

  const item = (s: Source, side: "left" | "right") => {
    const i = items.indexOf(s)
    const Icon = s.icon
    return (
      <li
        key={s.name}
        data-source
        data-side={side}
        data-soon={s.note === "soon" ? "" : undefined}
        data-lit={lit === i ? "" : undefined}
        tabIndex={0}
        onPointerEnter={() => setLit(i)}
        onPointerLeave={() => setLit((x) => (x === i ? -1 : x))}
        onFocus={() => setLit(i)}
        onBlur={() => setLit((x) => (x === i ? -1 : x))}
        className={cn(
          "flex w-fit items-center gap-2.5 rounded-md bg-background px-1.5 py-0.5 text-sm outline-none transition-colors duration-(--dur-hover) ease-hover focus-visible:ring-2 focus-visible:ring-ring data-lit:bg-accent",
          side === "left" && "lg:flex-row-reverse lg:self-end",
        )}
      >
        <span className="flex size-4 shrink-0 items-center justify-center text-muted-foreground">
          {s.brand ? <BrandMark slug={s.brand} className="size-4" /> : Icon ? <Icon aria-hidden className="size-4" /> : <FileText aria-hidden className="size-4" />}
        </span>
        <span className="min-w-0">{s.name}</span>
        {s.note && <Badge variant="mono">{s.note}</Badge>}
      </li>
    )
  }

  const group = ({ group: name, items: list }: SourceGroup, side: "left" | "right") => (
    <div key={name} className={cn("flex flex-col", side === "left" && "lg:items-end")}>
      <h3 className="text-sm text-muted-foreground">{name}</h3>
      <ul className={cn("mt-3 flex flex-col gap-1.5", side === "left" && "lg:items-end")}>{list.map((s) => item(s, side))}</ul>
    </div>
  )

  const note = lit >= 0 ? items[lit] : null
  return (
    <div className={cn("flex flex-col gap-6", className)}>
      <div ref={box} className="relative pb-16 lg:pb-0">
        <div ref={host} aria-hidden className="pointer-events-none absolute inset-0 font-mono text-ascii leading-[1.15] select-none">
          <canvas ref={canvas} className="absolute top-0 left-0" />
        </div>
        <div className="relative flex flex-col gap-8 lg:grid lg:grid-cols-[minmax(0,1fr)_15rem_minmax(0,1fr)] lg:gap-0">
          <div className="flex flex-col gap-8 lg:justify-center">{left.map((g) => group(g, "left"))}</div>
          <div ref={middle} aria-hidden className="hidden lg:block" />
          <div className="flex flex-col gap-8 lg:justify-center">{right.map((g) => group(g, "right"))}</div>
        </div>
        <span className="sr-only">Each of these is read into one index; nothing is written back to them.</span>
      </div>
      {/* One line, its height kept whatever it says. */}
      <p className="flex h-6 min-w-0 items-center gap-2 font-mono text-xs text-muted-foreground" aria-live="polite">
        {note ? (
          <>
            <Badge variant="mono">example</Badge>
            <span className="truncate">
              <span className="text-foreground">{note.name}</span> · {note.sync}
            </span>
          </>
        ) : (
          <span className="truncate">Point at a source, or tab to it, to see what it last synced.</span>
        )}
      </p>
    </div>
  )
}
