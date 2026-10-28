import { useEffect, useRef } from "react"

import "@/components/ascii/ascii.css"
import { FIELD_RAMP, fieldChar, fieldGrid, fillField, type FieldGrid } from "@/components/ascii/field"
import {
  getEffects,
  measureCell,
  reducedMotion,
  subscribe,
  whenFontsReady,
  type LoopHandle,
} from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// A field of characters in a slow interference pattern. Two renderers:
// - "pre", the landing's original: text in a <pre>. A dim <pre> is text to axe, which
//   fails it on contrast where nothing overlaps it, so new uses take the canvas.
// - "canvas", for the app's full-viewport background: a <pre> that size would lay out
//   11k characters a frame. The ramp's glyphs are drawn once into an atlas, and a frame
//   only redraws the cells whose character changed since the last one.
// mode: "hero" 30 fps, "ambient" 12 fps (rests while scrolling and under a dialog),
// "still" one frame. Any of them is one still frame with reduced motion or "Reduce
// effects" (lib/ascii-loop.ts). Always aria-hidden: it's decoration.
export type AsciiFieldProps = {
  mode?: "hero" | "ambient" | "still"
  renderer?: "pre" | "canvas" // ambient defaults to canvas, the others to pre
  palette?: string // the ramp, from empty to dense
  tone?: "ambient" | "backdrop" | "field" | "ink" // the --ascii-* ink; none: the current text colour
  mask?: "none" | "reading-column" | "radial" | "bottom-fade"
  reactive?: boolean // a ripple follows the pointer (the landing; never in the app)
  pulseEvent?: string // a window event that sends one ripple out (canvas only)
  className?: string
}

const FPS = { hero: 30, ambient: 12, still: 0 }
const STILL = 3 // the moment drawn as the still frame
const PULSE_S = 1.2

const TONES = {
  ambient: "text-(--ascii-ambient)",
  backdrop: "text-(--ascii-backdrop)", // the app field, at twice the ink, masked back (ascii.css)
  field: "text-(--ascii-field)",
  ink: "text-(--ascii-ink)",
}

const MASKS = {
  none: "",
  "reading-column": "ascii-mask-reading-column",
  radial: "ascii-mask-radial",
  "bottom-fade": "ascii-mask-bottom-fade",
}

export function AsciiField({
  mode = "hero",
  renderer = mode === "ambient" ? "canvas" : "pre",
  palette = FIELD_RAMP,
  tone,
  mask = "none",
  reactive = false,
  pulseEvent,
  className,
}: AsciiFieldProps) {
  const box = useRef<HTMLDivElement>(null)
  const pre = useRef<HTMLPreElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const el = box.current
    if (!el) return
    const scene =
      renderer === "canvas" && canvas.current
        ? canvasScene(el, canvas.current, palette, reactive, pulseEvent)
        : pre.current
          ? preScene(el, pre.current, palette, reactive)
          : null
    if (!scene) return

    // "still" never joins the loop: it draws its one frame and again on a resize.
    let loop: LoopHandle | null = null
    const redraw = () => (loop ? loop.redraw() : scene.draw(STILL))
    if (mode !== "still") {
      loop = subscribe(el, scene.draw, { fps: FPS[mode], still: STILL, priority: mode === "ambient" ? "ambient" : "hero" })
    } else scene.draw(STILL)

    const ro = new ResizeObserver(() => {
      scene.measure()
      redraw()
    })
    ro.observe(el)
    // The theme changed: the ink is another colour (canvas keeps it in its atlas).
    const mo = new MutationObserver(() => {
      scene.restyle()
      redraw()
    })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    // Measured before Geist Mono arrived, the grid is the fallback font's.
    let live = true
    whenFontsReady(el).then(() => {
      if (!live) return
      scene.measure()
      redraw()
    })
    return () => {
      live = false
      loop?.stop()
      ro.disconnect()
      mo.disconnect()
      scene.dispose()
    }
  }, [mode, renderer, palette, reactive, pulseEvent])

  return (
    <div
      ref={box}
      aria-hidden
      data-slot="ascii-field"
      data-mode={mode}
      className={cn(
        "pointer-events-none relative overflow-hidden font-mono text-ascii select-none",
        tone && TONES[tone],
        MASKS[mask],
        className,
      )}
    >
      {renderer === "canvas" ? (
        <canvas ref={canvas} className="absolute top-0 left-0" />
      ) : (
        <pre ref={pre} className="absolute inset-0 m-0 overflow-hidden font-mono text-current" />
      )}
    </div>
  )
}

type Scene = {
  draw(t: number): void
  measure(): void
  restyle(): void
  dispose(): void
}

function preScene(el: HTMLElement, out: HTMLPreElement, palette: string, reactive: boolean): Scene {
  let cell = measureCell(out)
  const pointer = { x: -1000, y: -1000 }
  const onMove = (e: PointerEvent) => {
    const r = el.getBoundingClientRect()
    pointer.x = (e.clientX - r.left) / cell.w
    pointer.y = (e.clientY - r.top) / cell.h
  }
  if (reactive) window.addEventListener("pointermove", onMove, { passive: true })
  return {
    draw(t) {
      const cols = Math.floor(el.clientWidth / cell.w)
      const rows = Math.floor(el.clientHeight / cell.h)
      let text = ""
      for (let r = 0; r < rows; r++) {
        let line = ""
        for (let c = 0; c < cols; c++) line += fieldChar(c, r, t * 0.8, pointer.x, pointer.y, palette)
        text += line + "\n"
      }
      out.textContent = text
    },
    measure() {
      cell = measureCell(out)
    },
    restyle() {},
    dispose() {
      window.removeEventListener("pointermove", onMove)
    },
  }
}

function canvasScene(
  el: HTMLElement,
  canvas: HTMLCanvasElement,
  palette: string,
  reactive: boolean,
  pulseEvent?: string,
): Scene {
  const ctx = canvas.getContext("2d", { alpha: true })
  const atlas = document.createElement("canvas")
  const actx = atlas.getContext("2d")
  // Device pixels per cell, whole numbers so cells don't leave seams when cleared.
  let dpr = 1
  let cw = 0
  let ch = 0
  let grid: FieldGrid = fieldGrid(0, 0)
  let next = new Uint8Array(0)
  let shown = new Uint8Array(0) // what's on the canvas; 255 is "unknown, draw it"
  let colTerm = new Float32Array(0)
  let lastT = 0
  let pulseAt = -Infinity

  const restyle = () => {
    if (!actx) return
    const s = getComputedStyle(el)
    atlas.width = cw * palette.length
    atlas.height = ch
    actx.clearRect(0, 0, atlas.width, atlas.height)
    actx.fillStyle = s.color
    actx.font = `${s.fontWeight} ${parseFloat(s.fontSize) * dpr}px ${s.fontFamily}`
    actx.textAlign = "center"
    actx.textBaseline = "middle"
    for (let i = 0; i < palette.length; i++) {
      if (palette[i] !== " ") actx.fillText(palette[i], i * cw + cw / 2, ch / 2)
    }
    shown.fill(255)
  }

  const measure = () => {
    dpr = Math.min(2, window.devicePixelRatio || 1)
    const cell = measureCell(el)
    cw = Math.max(1, Math.round(cell.w * dpr))
    ch = Math.max(1, Math.round(cell.h * dpr))
    const cols = Math.max(1, Math.ceil((el.clientWidth * dpr) / cw))
    const rows = Math.max(1, Math.ceil((el.clientHeight * dpr) / ch))
    canvas.width = cols * cw
    canvas.height = rows * ch
    canvas.style.width = `${canvas.width / dpr}px`
    canvas.style.height = `${canvas.height / dpr}px`
    if (cols !== grid.cols || rows !== grid.rows) {
      grid = fieldGrid(cols, rows)
      next = new Uint8Array(cols * rows)
      shown = new Uint8Array(cols * rows)
      colTerm = new Float32Array(cols)
    }
    restyle() // the canvas was cleared by the resize; the atlas follows the cell
  }

  // One ripple per pulse, picked up by the next frame; none on a still field.
  const onPulse = (e: Event) => {
    if ((e as CustomEvent<{ active?: boolean }>).detail?.active === false) return
    if (reducedMotion() || getEffects() === "reduced") return
    pulseAt = lastT
  }
  if (pulseEvent) window.addEventListener(pulseEvent, onPulse)

  // The pointer, in cells, once it has moved over the page.
  let pointer: { x: number; y: number } | undefined
  const onMove = (e: PointerEvent) => {
    const r = el.getBoundingClientRect()
    pointer = { x: ((e.clientX - r.left) * dpr) / cw, y: ((e.clientY - r.top) * dpr) / ch }
  }
  if (reactive) window.addEventListener("pointermove", onMove, { passive: true })

  measure()
  return {
    draw(t) {
      lastT = t
      if (!ctx || !grid.cols) return
      const age = t - pulseAt
      // The ripple starts from the bottom centre, where the composer is.
      const ripple = age >= 0 && age < PULSE_S ? { x: grid.cols / 2, y: grid.rows - 4, age } : undefined
      fillField(grid, t * 0.8, palette.length, next, colTerm, ripple, pointer)
      for (let k = 0; k < next.length; k++) {
        const i = next[k]
        if (i === shown[k]) continue
        const x = (k % grid.cols) * cw
        const y = Math.floor(k / grid.cols) * ch
        ctx.clearRect(x, y, cw, ch)
        if (palette[i] !== " ") ctx.drawImage(atlas, i * cw, 0, cw, ch, x, y, cw, ch)
        shown[k] = i
      }
    },
    measure,
    restyle,
    dispose() {
      if (pulseEvent) window.removeEventListener(pulseEvent, onPulse)
      window.removeEventListener("pointermove", onMove)
    },
  }
}
