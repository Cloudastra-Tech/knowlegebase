import { useEffect, useRef } from "react"

import { measureCell, runLoop } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// Moving ASCII art for the landing page: a rotating brain over a flowing field of
// characters, and a terminal line that asks questions and types the answers.
// Each scene draws into stacked <pre>s (dim field, ink, accent) about 30 times a
// second while it's on screen; with reduced motion it draws one still frame.

// ---- The field ------------------------------------------------------------------
const FIELD = " ..··::--~~"

// A slow interference pattern, with a ripple spreading from the pointer.
function fieldChar(x: number, y: number, t: number, px: number, py: number) {
  let v =
    Math.sin(x * 0.11 + t * 0.55) +
    Math.sin(y * 0.23 - t * 0.4) +
    Math.sin((x + y * 1.8) * 0.06 + t * 0.3) +
    Math.sin(Math.hypot(x - 60, (y - 20) * 2) * 0.09 - t * 0.8)
  const d = Math.hypot(x - px, (y - py) * 2)
  v += 2.2 * Math.sin(d * 0.5 - t * 5) * Math.exp(-d * 0.07)
  const i = Math.floor(((v + 4) / 8) * FIELD.length)
  return FIELD[Math.max(0, Math.min(FIELD.length - 1, i))]
}

// ---- The brain ------------------------------------------------------------------
// A side view of a brain, as points on its surface: x towards the front, y up, z
// across (the camera looks along z, so it is seen in profile and only sways). The
// cerebrum is a union of lobes (frontal, parietal, occipital, temporal), with the
// cerebellum under the back and the brainstem below. Its folds are the level set
// of a gyroid, which draws the maze of gyri and sulci a brain has.
//
// Per point: position, normal, part (0 cortex, 1 cerebellum, 2 stem), and crease
// (1 in a sulcus or a fissure, drawn dark).
const STRIDE = 8
let brainCache: Float32Array | null = null

type Lobe = { c: [number, number, number]; r: [number, number, number]; n: number; part: number }

const LOBES: Lobe[] = [
  { c: [0.05, 0.16, 0], r: [0.98, 0.64, 0.7], n: 15000, part: 0 }, // parietal / main dome
  { c: [0.46, 0.04, 0], r: [0.6, 0.56, 0.66], n: 9000, part: 0 }, // frontal
  { c: [-0.56, 0.04, 0], r: [0.5, 0.52, 0.6], n: 7000, part: 0 }, // occipital
  { c: [0.2, -0.3, 0], r: [0.6, 0.3, 0.58], n: 7000, part: 0 }, // temporal
  { c: [-0.6, -0.46, 0], r: [0.44, 0.28, 0.56], n: 7000, part: 1 }, // cerebellum
]

const inside = (p: number[], l: Lobe) =>
  ((p[0] - l.c[0]) / l.r[0]) ** 2 + ((p[1] - l.c[1]) / l.r[1]) ** 2 + ((p[2] - l.c[2]) / l.r[2]) ** 2

function gyroid(x: number, y: number, z: number) {
  const k = 14
  const u = x * k + 0.9 * Math.sin(y * 4.1 + z * 2.3)
  const v = y * k + 0.9 * Math.sin(z * 3.7 + x * 2.9)
  const w = z * k + 0.9 * Math.sin(x * 4.3 + y * 2.1)
  return Math.sin(u) * Math.cos(v) + Math.sin(v) * Math.cos(w) + Math.sin(w) * Math.cos(u)
}

function brainPoints() {
  if (brainCache) return brainCache
  const out: number[] = []
  const golden = Math.PI * (3 - Math.sqrt(5))
  LOBES.forEach((lobe, li) => {
    for (let i = 0; i < lobe.n; i++) {
      const ey = 1 - (2 * (i + 0.5)) / lobe.n
      const rad = Math.sqrt(1 - ey * ey)
      const ex = Math.cos(golden * i) * rad
      const ez = Math.sin(golden * i) * rad
      const p = [lobe.c[0] + ex * lobe.r[0], lobe.c[1] + ey * lobe.r[1], lobe.c[2] + ez * lobe.r[2]]
      // Only the outer surface of the union: drop points inside another lobe.
      let nearest = Infinity // to another lobe of the same part
      let cerebrum = Infinity // to the cerebrum, for the cerebellum
      let hidden = false
      LOBES.forEach((other, oi) => {
        if (oi === li) return
        const e = inside(p, other)
        if (e < 1) hidden = true
        if (other.part === lobe.part) nearest = Math.min(nearest, e)
        if (other.part === 0) cerebrum = Math.min(cerebrum, e)
      })
      if (hidden) continue
      const nx = ex / lobe.r[0], ny = ey / lobe.r[1], nz = ez / lobe.r[2]
      const nl = Math.hypot(nx, ny, nz)
      let crease = 0
      if (lobe.part === 0) {
        if (Math.abs(gyroid(p[0], p[1], p[2])) < 0.42) crease = 1 // sulci
        if (li === 3 && nearest < 1.3 && p[1] > -0.3) crease = 1 // the lateral (Sylvian) fissure
        if (Math.abs(p[2]) < 0.035 && p[1] > 0) crease = 1 // between the hemispheres
      } else if (cerebrum < 1.35 || Math.abs(Math.sin(34 * p[1] + 2.5 * p[0])) < 0.5) {
        crease = 1 // where it tucks under the cerebrum, and its fine folia
      }
      out.push(p[0], p[1], p[2], nx / nl, ny / nl, nz / nl, lobe.part, crease)
    }
  })
  // The brainstem: from under the middle, down and back, in front of the cerebellum.
  for (let k = 0; k < 34; k++) {
    for (let a = 0; a < 48; a++) {
      const ang = (2 * Math.PI * a) / 48
      const f = k / 33
      const y = -0.3 - f * 0.55
      const x = -0.12 - f * 0.16
      const r = 0.13 - f * 0.025
      const p = [x + r * Math.cos(ang), y, r * Math.sin(ang)]
      if (LOBES.some((l) => inside(p, l) < 1)) continue
      out.push(p[0], p[1], p[2], Math.cos(ang), 0.15, Math.sin(ang), 2, 0)
    }
  }
  brainCache = new Float32Array(out)
  return brainCache
}

// Gyri are drawn in mid-weight characters so the folds between them read.
const RAMP = ":;-=+*#%"
// Light from above, in front of the brain and towards the viewer.
const LIGHT = (() => {
  const l = [0.45, 0.7, -0.6]
  const n = Math.hypot(...l)
  return l.map((v) => v / n)
})()

// "hero" puts the brain right of the headline on wide screens and above it on
// narrow ones; "panel" centres a larger brain in the upper part of its box.
export function AsciiBrain({ className, framing = "hero" }: { className?: string; framing?: "hero" | "panel" }) {
  const box = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLPreElement>(null)
  const ink = useRef<HTMLPreElement>(null)
  const sparks = useRef<HTMLPreElement>(null)

  useEffect(() => {
    const el = box.current
    if (!el || !field.current || !ink.current || !sparks.current) return
    const [fieldPre, inkPre, sparkPre] = [field.current, ink.current, sparks.current]
    const pts = brainPoints()
    let cell = measureCell(inkPre)
    let cols = 0
    let rows = 0
    let zbuf = new Float32Array(0)
    let shade = new Float32Array(0)
    let spark = new Uint8Array(0)
    const resize = () => {
      cell = measureCell(inkPre)
      cols = Math.max(1, Math.floor(el.clientWidth / cell.w))
      rows = Math.max(1, Math.floor(el.clientHeight / cell.h))
      zbuf = new Float32Array(cols * rows)
      shade = new Float32Array(cols * rows)
      spark = new Uint8Array(cols * rows)
    }
    resize()

    // The pointer tilts the brain and ripples the field; eased towards it.
    const target = { x: 0, y: 0, cx: -1000, cy: -1000 }
    const pointer = { x: 0, y: 0 }
    const onMove = (e: PointerEvent) => {
      const r = el.getBoundingClientRect()
      target.x = ((e.clientX - r.left) / r.width) * 2 - 1
      target.y = ((e.clientY - r.top) / r.height) * 2 - 1
      target.cx = (e.clientX - r.left) / cell.w
      target.cy = (e.clientY - r.top) / cell.h
    }
    window.addEventListener("pointermove", onMove, { passive: true })

    const draw = (t: number) => {
      pointer.x += (target.x - pointer.x) * 0.06
      pointer.y += (target.y - pointer.y) * 0.06
      const w = el.clientWidth
      const h = el.clientHeight
      const wide = framing === "hero" && w >= 1024
      const panel = framing === "panel"
      const centerX = (wide ? 0.72 : 0.5) * w
      const centerY = (wide ? 0.46 : panel ? 0.36 : 0.25) * h
      const radius = wide
        ? Math.min(0.17 * w, 0.36 * h)
        : panel
          ? Math.min(0.3 * w, 0.2 * h)
          : Math.min(0.36 * w, 0.17 * h)
      const scale = radius * 3.4

      zbuf.fill(0)
      spark.fill(0)
      // Seen from its left side; it sways, and the pointer turns it a little more.
      const ay = 0.38 * Math.sin(t * 0.45) + pointer.x * 0.35
      const ax = 0.12 + 0.05 * Math.sin(t * 0.3) + pointer.y * 0.2
      const ca = Math.cos(ay), sa = Math.sin(ay), cb = Math.cos(ax), sb = Math.sin(ax)
      for (let i = 0; i < pts.length; i += STRIDE) {
        const x = pts[i], y = pts[i + 1], z = pts[i + 2]
        const x1 = x * ca + z * sa
        const z1 = -x * sa + z * ca
        const y2 = y * cb - z1 * sb
        const z2 = y * sb + z1 * cb
        const inv = 1 / (z2 + 3.4)
        const cx = Math.floor((centerX + x1 * inv * scale) / cell.w)
        const cy = Math.floor((centerY - y2 * inv * scale) / cell.h)
        if (cx < 0 || cy < 0 || cx >= cols || cy >= rows) continue
        const k = cy * cols + cx
        if (inv <= zbuf[k]) continue
        zbuf[k] = inv
        const nx = pts[i + 3], ny = pts[i + 4], nz = pts[i + 5]
        const nx1 = nx * ca + nz * sa
        const nz1 = -nx * sa + nz * ca
        const ny2 = ny * cb - nz1 * sb
        const nz2 = ny * sb + nz1 * cb
        const lum = Math.max(0, nx1 * LIGHT[0] + ny2 * LIGHT[1] + nz2 * LIGHT[2])
        const crease = pts[i + 7] === 1
        shade[k] = crease ? -1 : 0.3 + 0.68 * lum - (pts[i + 6] === 2 ? 0.1 : 0)
        // Signals travelling along the folds, back to front, with the odd lone flash.
        const wave = Math.sin(x * 2.6 - t * 2.2 + Math.sin(y * 3)) * Math.sin(y * 4 + z * 3 + t * 1.3)
        const flash = Math.sin(i * 12.9898 + Math.floor(t * 3) * 78.233) > 0.9993
        spark[k] = !crease && pts[i + 6] === 0 && (wave > 0.96 || flash) ? 1 : 0
      }

      let fieldOut = ""
      let inkOut = ""
      let sparkOut = ""
      for (let r = 0; r < rows; r++) {
        let f = ""
        let a = ""
        let s = ""
        for (let c = 0; c < cols; c++) {
          const k = r * cols + c
          if (!zbuf[k]) {
            f += fieldChar(c, r, t, pointer.x === 0 ? -1000 : target.cx, target.cy)
            a += " "
            s += " "
          } else if (spark[k]) {
            f += " "
            a += " "
            s += "*"
          } else if (shade[k] < 0) {
            f += " "
            a += " " // a fold: a dark gap between gyri
            s += " "
          } else {
            const v = Math.min(0.999, shade[k])
            f += " "
            a += RAMP[Math.floor(v * RAMP.length)]
            s += " "
          }
        }
        fieldOut += f + "\n"
        inkOut += a + "\n"
        sparkOut += s + "\n"
      }
      fieldPre.textContent = fieldOut
      inkPre.textContent = inkOut
      sparkPre.textContent = sparkOut
    }

    const loop = runLoop(el, draw, 2.4)
    const ro = new ResizeObserver(() => {
      resize()
      loop.redraw()
    })
    ro.observe(el)
    return () => {
      loop.stop()
      ro.disconnect()
      window.removeEventListener("pointermove", onMove)
    }
  }, [framing])

  const layer = "absolute inset-0 m-0 overflow-hidden font-mono text-[11px] leading-[1.15] select-none"
  return (
    <div ref={box} aria-hidden className={cn("pointer-events-none relative overflow-hidden", className)}>
      <pre ref={field} className={cn(layer, "text-foreground/[0.16]")} />
      <pre ref={ink} className={cn(layer, "text-foreground/85")} />
      <pre ref={sparks} className={cn(layer, "text-[var(--ascii-accent)]")} />
    </div>
  )
}

// A strip of the field on its own (behind the closing call to action).
export function AsciiField({ className }: { className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const pre = useRef<HTMLPreElement>(null)

  useEffect(() => {
    const el = box.current
    const out = pre.current
    if (!el || !out) return
    let cell = measureCell(out)
    const draw = (t: number) => {
      const cols = Math.floor(el.clientWidth / cell.w)
      const rows = Math.floor(el.clientHeight / cell.h)
      let text = ""
      for (let r = 0; r < rows; r++) {
        let line = ""
        for (let c = 0; c < cols; c++) line += fieldChar(c, r, t * 0.8, -1000, -1000)
        text += line + "\n"
      }
      out.textContent = text
    }
    const loop = runLoop(el, draw, 3)
    const ro = new ResizeObserver(() => {
      cell = measureCell(out)
      loop.redraw()
    })
    ro.observe(el)
    return () => {
      loop.stop()
      ro.disconnect()
    }
  }, [])

  return (
    <div ref={box} aria-hidden className={cn("pointer-events-none relative overflow-hidden", className)}>
      <pre
        ref={pre}
        className="absolute inset-0 m-0 overflow-hidden font-mono text-[11px] leading-[1.15] text-current select-none"
      />
    </div>
  )
}

// One line of a terminal, asking each question in turn and typing the answer.
export function AsciiTicker({
  items,
  className,
}: {
  items: { question: string; answer: string; source: string }[]
  className?: string
}) {
  const box = useRef<HTMLDivElement>(null)
  const q = useRef<HTMLSpanElement>(null)
  const a = useRef<HTMLSpanElement>(null)
  const src = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const el = box.current
    if (!el || !q.current || !a.current || !src.current) return
    const [qEl, aEl, sEl] = [q.current, a.current, src.current]
    const each = 7
    const draw = (time: number) => {
      const item = items[Math.floor(time / each) % items.length]
      const t = time % each
      const cursor = Math.floor(time * 3) % 2 === 0 ? "▌" : " "
      const qn = Math.min(item.question.length, Math.floor(t * 28))
      const an = Math.max(0, Math.min(item.answer.length, Math.floor((t - 1.8) * 45)))
      qEl.textContent = item.question.slice(0, qn) + (qn < item.question.length ? cursor : "")
      aEl.textContent = t > 1.6 ? item.answer.slice(0, an) + (an < item.answer.length ? cursor : "") : ""
      sEl.textContent = an >= item.answer.length ? `[${item.source}]` : ""
    }
    const loop = runLoop(el, draw, 4.5)
    return loop.stop
  }, [items])

  return (
    <div ref={box} className={cn("font-mono text-xs leading-relaxed sm:text-sm", className)}>
      <p className="truncate">
        <span aria-hidden className="text-[var(--ascii-accent)]">&gt; </span>
        <span ref={q} />
      </p>
      <p className="min-h-[2lh] text-muted-foreground sm:min-h-[1lh]">
        <span ref={a} /> <span ref={src} className="text-[var(--ascii-accent)]" />
      </p>
    </div>
  )
}
