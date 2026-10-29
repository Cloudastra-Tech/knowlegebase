import { useEffect, useRef } from "react"

import type { BrainOverlay } from "@/components/ascii/brain-overlay"
import { FIELD_RAMP, fieldValue, rampIndex } from "@/components/ascii/field"
import { measureCell, subscribe, whenFontsReady } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// The landing's and sign-in's moving brain over the field, drawn in three inks (dim
// field, brain, signal sparks) up to 30 times a second while it's on screen; with
// reduced motion or "Reduce effects", one still frame.
//
// A side view of a brain, as points on its surface: x towards the front, y up, z
// across (the camera looks along z, so it is seen in profile and only sways). The
// cerebrum is a union of lobes (frontal, parietal, occipital, temporal), with the
// cerebellum under the back and the brainstem below. Its folds are the level set
// of a gyroid, which draws the maze of gyri and sulci a brain has.
//
// Per point: position, normal, part (0 cortex, 1 cerebellum, 2 stem), and crease
// (1 in a sulcus or a fissure, drawn dark).
const STRIDE = 8
const STILL = 2.4 // the moment drawn as the still frame
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


// Every glyph the brain draws, as a code per cell (0 is empty): the field's ramp in
// the dim field ink, the brain's ramp in the brain ink, and the spark in the signal.
// Then what an overlay may add where the brain isn't (BrainOverlay): a document's
// redaction (▪ ░ ·) in a half ink, or in the signal.
const FIELD_CODE = 0 // + the field ramp's index (its index 0 is a space: empty)
const INK_CODE = FIELD_RAMP.length // + the brain ramp's index
const SPARK_CODE = INK_CODE + RAMP.length
const FRAG_GLYPHS = "▪░·"
const FRAG_CODE = SPARK_CODE + 1 // + the glyph's index; + 3 more for the signal
type Tone = "field" | "ink" | "signal" | "frag"
const GLYPHS: [string, Tone][] = [
  ...[...FIELD_RAMP].map((ch) => [ch, "field"] as [string, Tone]),
  ...[...RAMP].map((ch) => [ch, "ink"] as [string, Tone]),
  ["*", "signal"],
  ...[...FRAG_GLYPHS].map((ch) => [ch, "frag"] as [string, Tone]),
  ...[...FRAG_GLYPHS].map((ch) => [ch, "signal"] as [string, Tone]),
]

// "hero" puts the brain right of the headline on wide screens and above it on
// narrow ones; "panel" centres a larger brain in the upper part of its box. `fps`
// caps its frame rate (the sign-in panel's is 24: it shares the page with a form).
//
// It draws on a <canvas>: the three <pre>s it had were the page's largest text, so a
// field of dim dots, not the headline, was the landing's Largest Contentful Paint, and
// laying out three screens of text a frame was most of a frame's cost. The glyphs are
// drawn once into an atlas, and a frame redraws only the cells whose glyph changed.
export function AsciiBrain({
  className,
  framing = "hero",
  fps = 30,
  overlay,
}: {
  className?: string
  framing?: "hero" | "panel"
  fps?: number
  overlay?: BrainOverlay
}) {
  const box = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const el = box.current
    const cv = canvas.current
    const ctx = cv?.getContext("2d", { alpha: true })
    if (!el || !cv || !ctx) return
    const pts = brainPoints()
    const atlas = document.createElement("canvas")
    const actx = atlas.getContext("2d")
    let cell = measureCell(el)
    let dpr = 1
    let cols = 0
    let rows = 0
    let aw = 1 // an atlas cell, in device pixels
    let ah = 1
    let xs = new Int32Array(1) // each column's left edge, in whole device pixels
    let ys = new Int32Array(1)
    let zbuf = new Float32Array(0)
    let shade = new Float32Array(0)
    let spark = new Uint8Array(0)
    let next = new Uint8Array(0)
    let shown = new Uint8Array(0) // what's on the canvas; 255 is "unknown, draw it"

    // The inks, from the tokens (so a theme change follows), into the atlas.
    const restyle = () => {
      if (!actx) return
      const s = getComputedStyle(el)
      const probe = document.createElement("span")
      el.appendChild(probe)
      const ink = (token: string) => {
        probe.style.color = `var(--ascii-${token})`
        return getComputedStyle(probe).color
      }
      probe.style.color = "color-mix(in oklab, var(--foreground) 42%, transparent)"
      const frag = getComputedStyle(probe).color
      const colours = { field: ink("field"), ink: ink("ink"), signal: ink("signal"), frag }
      probe.remove()
      atlas.width = aw * GLYPHS.length
      atlas.height = ah
      actx.clearRect(0, 0, atlas.width, atlas.height)
      actx.font = `${s.fontWeight} ${parseFloat(s.fontSize) * dpr}px ${s.fontFamily}`
      actx.textAlign = "center"
      actx.textBaseline = "middle"
      GLYPHS.forEach(([ch, tone], i) => {
        if (ch === " ") return
        actx.fillStyle = colours[tone]
        actx.fillText(ch, i * aw + aw / 2, ah / 2)
      })
      shown.fill(255)
    }

    // Cells stay the font's own size (the art keeps its proportions); their edges are
    // rounded to whole device pixels, so clearing one never leaves a seam or a sliver.
    const resize = () => {
      cell = measureCell(el)
      dpr = Math.min(2, window.devicePixelRatio || 1)
      cols = Math.max(1, Math.floor(el.clientWidth / cell.w))
      rows = Math.max(1, Math.floor(el.clientHeight / cell.h))
      aw = Math.ceil(cell.w * dpr)
      ah = Math.ceil(cell.h * dpr)
      xs = Int32Array.from({ length: cols + 1 }, (_, c) => Math.round(c * cell.w * dpr))
      ys = Int32Array.from({ length: rows + 1 }, (_, r) => Math.round(r * cell.h * dpr))
      cv.width = xs[cols]
      cv.height = ys[rows]
      cv.style.width = `${cv.width / dpr}px`
      cv.style.height = `${cv.height / dpr}px`
      zbuf = new Float32Array(cols * rows)
      shade = new Float32Array(cols * rows)
      spark = new Uint8Array(cols * rows)
      next = new Uint8Array(cols * rows)
      shown = new Uint8Array(cols * rows)
      restyle()
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

      const px = pointer.x === 0 ? -1000 : target.cx
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const k = r * cols + c
          if (!zbuf[k]) next[k] = FIELD_CODE + rampIndex(fieldValue(c, r, t, px, target.cy), FIELD_RAMP.length)
          else if (spark[k]) next[k] = SPARK_CODE
          else if (shade[k] < 0) next[k] = 0 // a fold: a dark gap between gyri
          else next[k] = INK_CODE + Math.floor(Math.min(0.999, shade[k]) * RAMP.length)
        }
      }
      if (overlay?.active && t !== STILL) {
        overlay.draw({
          cols,
          rows,
          cx: centerX / cell.w,
          cy: centerY / cell.h,
          rx: radius / cell.w,
          ry: radius / cell.h,
          wide,
          brain: (c, r) => c >= 0 && r >= 0 && c < cols && r < rows && zbuf[r * cols + c] > 0,
          put: (c, r, glyph, signal) => {
            if (c < 0 || r < 0 || c >= cols || r >= rows) return
            const k = r * cols + c
            if (zbuf[k]) return
            const g = FRAG_GLYPHS.indexOf(glyph)
            if (g >= 0) next[k] = FRAG_CODE + g + (signal ? FRAG_GLYPHS.length : 0)
          },
        })
      }
      for (let k = 0; k < next.length; k++) {
        const code = next[k]
        if (code === shown[k]) continue
        const c = k % cols
        const r = (k - c) / cols
        const x = xs[c], y = ys[r]
        const cw = xs[c + 1] - x, ch = ys[r + 1] - y
        ctx.clearRect(x, y, cw, ch)
        if (code && GLYPHS[code][0] !== " ") ctx.drawImage(atlas, code * aw, 0, cw, ch, x, y, cw, ch)
        shown[k] = code
      }
    }

    const loop = subscribe(el, draw, { fps, still: STILL })
    const ro = new ResizeObserver(() => {
      resize()
      loop.redraw()
    })
    ro.observe(el)
    // The theme changed: the inks are other colours.
    const mo = new MutationObserver(() => {
      restyle()
      loop.redraw()
    })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    // Measured before Geist Mono arrived, the grid is the fallback font's: again once it has.
    let live = true
    whenFontsReady(el).then(() => {
      if (!live) return
      resize()
      loop.redraw()
    })
    return () => {
      live = false
      loop.stop()
      ro.disconnect()
      mo.disconnect()
      window.removeEventListener("pointermove", onMove)
    }
  }, [framing, fps, overlay])

  return (
    <div
      ref={box}
      aria-hidden
      data-slot="ascii-brain"
      className={cn("pointer-events-none relative overflow-hidden font-mono text-ascii select-none", className)}
    >
      <canvas ref={canvas} className="absolute top-0 left-0" />
    </div>
  )
}
