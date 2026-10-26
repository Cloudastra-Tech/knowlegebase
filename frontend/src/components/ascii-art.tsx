import { useEffect, useRef } from "react"

import { cn } from "@/lib/utils"

// Moving ASCII art for the landing page: a rotating brain over a flowing field of
// characters, and documents whose letters fly over to become a cited answer.
// Each scene draws into stacked <pre>s (dim field, ink, accent) about 30 times a
// second while it's on screen; with reduced motion it draws one still frame.

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches

// One character cell of `el`'s monospace font, in px.
function measureCell(el: HTMLElement) {
  const probe = document.createElement("span")
  probe.textContent = "0".repeat(40)
  probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre"
  el.appendChild(probe)
  const w = probe.getBoundingClientRect().width / 40
  probe.remove()
  const style = getComputedStyle(el)
  const lh = parseFloat(style.lineHeight)
  return { w, h: Number.isFinite(lh) ? lh : parseFloat(style.fontSize) * 1.2 }
}

// Draws `draw(seconds)` while `el` is visible and the tab is in front. The clock
// only runs while it's drawing, so a scene starts from the top when it's first seen.
function runLoop(el: HTMLElement, draw: (t: number) => void, still: number) {
  if (reducedMotion()) {
    draw(still)
    return { redraw: () => draw(still), stop: () => {} }
  }
  let raf = 0
  let visible = false
  let last = -1
  let elapsed = 0
  const frame = (now: number) => {
    raf = 0
    if (!visible || document.hidden) {
      last = -1
      return
    }
    if (last < 0) last = now - 33
    if (now - last >= 33) {
      elapsed += Math.min(now - last, 100) / 1000
      last = now
      draw(elapsed)
    }
    raf = requestAnimationFrame(frame)
  }
  const wake = () => {
    if (visible && !document.hidden && !raf) raf = requestAnimationFrame(frame)
  }
  const io = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting
    wake()
  })
  io.observe(el)
  document.addEventListener("visibilitychange", wake)
  return {
    redraw: () => draw(elapsed),
    stop: () => {
      cancelAnimationFrame(raf)
      io.disconnect()
      document.removeEventListener("visibilitychange", wake)
    },
  }
}

// Small deterministic random numbers, so a scene is the same on every visit.
function seeded(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

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
// Points on its surface: x right, y up, z towards the back of the head. Per point:
// position, normal, and how far a fold pushes it out (darker in the creases).
const STRIDE = 7
let brainCache: Float32Array | null = null

function brainPoints() {
  if (brainCache) return brainCache
  const out: number[] = []
  const shell = (
    [cx, cy, cz]: number[],
    [rx, ry, rz]: number[],
    nt: number,
    np: number,
    fold: (th: number, ph: number, ey: number) => number,
    keep: (ex: number) => boolean = () => true,
    floor = -Infinity,
  ) => {
    for (let i = 0; i < nt; i++) {
      for (let j = 0; j < np; j++) {
        const th = (Math.PI * (i + 0.5)) / nt
        const ph = (2 * Math.PI * (j + (i % 2) * 0.5)) / np
        const ex = Math.sin(th) * Math.cos(ph)
        const ey = Math.cos(th)
        const ez = Math.sin(th) * Math.sin(ph)
        if (!keep(ex)) continue
        const f = fold(th, ph, ey)
        let y = cy + ey * ry * (1 + f)
        if (y < floor) y = floor + (y - floor) * 0.45 // the flatter underside
        const nl = Math.hypot(ex / rx, ey / ry, ez / rz)
        out.push(
          cx + ex * rx * (1 + f),
          y,
          cz + ez * rz * (1 + f),
          ex / rx / nl,
          ey / ry / nl,
          ez / rz / nl,
          f,
        )
      }
    }
  }
  const gyri = (th: number, ph: number) =>
    0.065 * Math.sin(11 * th + 3 * Math.sin(5 * ph)) * Math.cos(9 * ph + 2 * Math.sin(7 * th))
  for (const s of [-1, 1])
    shell([s * 0.53, 0.08, 0], [0.52, 0.62, 0.95], 84, 168, gyri, (ex) => s * ex > -0.7, -0.32)
  // Cerebellum, finely striped, under the back.
  shell([0, -0.42, 0.55], [0.6, 0.24, 0.32], 36, 110, (_th, _ph, ey) => 0.035 * Math.sin(36 * ey))
  // Brainstem.
  for (let k = 0; k < 22; k++) {
    for (let a = 0; a < 36; a++) {
      const ang = (2 * Math.PI * a) / 36
      const y = -0.38 - k * 0.03
      out.push(0.13 * Math.cos(ang), y, 0.22 + 0.13 * Math.sin(ang) + (-0.38 - y) * 0.3,
        Math.cos(ang), 0, Math.sin(ang), 0)
    }
  }
  brainCache = new Float32Array(out)
  return brainCache
}

const RAMP = " .,:;-=+*#%@"
const LIGHT = (() => {
  const l = [-0.45, 0.6, -0.65]
  const n = Math.hypot(...l)
  return l.map((v) => v / n)
})()

export function AsciiBrain({ className }: { className?: string }) {
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
      const wide = w >= 1024
      const centerX = (wide ? 0.71 : 0.5) * w
      const centerY = (wide ? 0.45 : 0.23) * h
      const radius = wide ? Math.min(0.155 * w, 0.3 * h) : Math.min(0.27 * w, 0.15 * h)
      const scale = radius * 3.4

      zbuf.fill(0)
      spark.fill(0)
      const ay = t * 0.32 + pointer.x * 0.7
      const ax = 0.15 + pointer.y * 0.35
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
        shade[k] = 0.1 + 0.78 * lum + pts[i + 6] * 2.4
        // Activity travelling through it, front to back, with the odd lone flash.
        const wave = Math.sin(x * 3.5 + z * 6 - t * 2.4) * Math.sin(y * 5 + t * 1.6)
        const flash = Math.sin(i * 12.9898 + Math.floor(t * 3) * 78.233) > 0.9994
        spark[k] = wave > 0.94 || flash ? 1 : 0
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
          } else {
            const v = Math.max(0, Math.min(0.999, shade[k]))
            f += " "
            a += RAMP[1 + Math.floor(v * (RAMP.length - 1))]
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
  }, [])

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

// ---- Documents into an answer ----------------------------------------------------
// Three documents on the left; the question types in, the letters the answer
// needs light up in them and fly over to spell it out, with its sources. The
// documents stay as they were: the assistant only reads them.
const W = 66
const H = 20
const DOCS = [
  { title: "handbook.txt", lines: ["refunds: a full", "refund within 30", "days of purchase.", "then credit only."] },
  { title: "billing-faq.md", lines: ["after 30 days we", "give account credit", "for the remainder,", "never cash back."] },
  { title: "outline/policies", lines: ["every request is", "answered within one", "business day, and", "logged for audit."] },
]
const QUESTION = "what's our refund policy?"
const ANSWER = [
  "Customers get a full refund within",
  "30 days of purchase; after that it's",
  "account credit only. [1][2]",
]
const SOURCES = ["[1] handbook.txt", "[2] billing-faq.md"]
const PERIOD = 11

type Scene = {
  base: string[][]
  particles: { sx: number; sy: number; tx: number; ty: number; ch: string; delay: number; gone: number }[]
}

function buildScene(): Scene {
  const base = Array.from({ length: H }, () => Array<string>(W).fill(" "))
  const put = (x: number, y: number, text: string) => {
    for (let i = 0; i < text.length; i++) if (x + i < W) base[y][x + i] = text[i]
  }
  const frame = (x: number, y: number, w: number, h: number, title: string) => {
    put(x, y, `┌─ ${title} ${"─".repeat(Math.max(0, w - title.length - 5))}┐`)
    for (let r = 1; r < h - 1; r++) {
      put(x, y + r, "│")
      put(x + w - 1, y + r, "│")
    }
    put(x, y + h - 1, `└${"─".repeat(w - 2)}┘`)
  }
  const sources: Record<string, [number, number][]> = {}
  DOCS.forEach((doc, d) => {
    const y = d * 7
    frame(0, y, 22, 6, doc.title)
    doc.lines.forEach((line, i) => {
      put(2, y + 1 + i, line)
      for (let c = 0; c < line.length; c++) {
        const ch = line[c].toLowerCase()
        if (ch !== " ") (sources[ch] ??= []).push([2 + c, y + 1 + i])
      }
    })
  })
  frame(26, 0, 40, H, "assistant")
  put(23, 9, "─▶")

  const rand = seeded(7)
  const anywhere = Object.values(sources).flat()
  const particles: Scene["particles"] = []
  const target = (x: number, y: number, text: string) => {
    for (let i = 0; i < text.length; i++) {
      const ch = text[i]
      if (ch === " ") continue
      const pool = sources[ch.toLowerCase()] ?? anywhere
      const [sx, sy] = pool[Math.floor(rand() * pool.length)]
      particles.push({ sx, sy, tx: x + i, ty: y, ch, delay: rand() * 1.3, gone: rand() })
    }
  }
  ANSWER.forEach((line, i) => target(29, 5 + i, line))
  SOURCES.forEach((line, i) => target(29, 11 + i, line))
  return { base, particles }
}

const ease = (p: number) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2)

export function AsciiAnswer({ className }: { className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const ink = useRef<HTMLPreElement>(null)
  const accent = useRef<HTMLPreElement>(null)

  useEffect(() => {
    const el = box.current
    if (!el || !ink.current || !accent.current) return
    const [inkPre, accentPre] = [ink.current, accent.current]
    const scene = buildScene()

    // Scale the font so the 66-column scene fills the width.
    const fit = () => {
      inkPre.style.fontSize = accentPre.style.fontSize = "10px"
      const w10 = measureCell(inkPre).w
      const size = `${Math.min(15, (10 * el.clientWidth) / (W * w10))}px`
      inkPre.style.fontSize = accentPre.style.fontSize = size
    }
    fit()

    const draw = (time: number) => {
      const t = time % PERIOD
      const ink = scene.base.map((row) => row.slice())
      const lit = Array.from({ length: H }, () => Array<string>(W).fill(" "))
      const light = (x: number, y: number, ch: string) => {
        if (x < 0 || y < 0 || x >= W || y >= H) return
        lit[y][x] = ch
        ink[y][x] = " "
      }
      const write = (x: number, y: number, text: string) => {
        for (let i = 0; i < text.length; i++) ink[y][x + i] = text[i]
      }

      const leaving = t > 9.4 ? (t - 9.4) / 1.2 : 0
      const typed = Math.min(QUESTION.length, Math.floor((t / 1.3) * QUESTION.length))
      if (leaving < 1) {
        write(29, 2, "> ")
        write(31, 2, QUESTION.slice(0, typed))
        if (typed < QUESTION.length && Math.floor(time * 3) % 2 === 0) ink[2][31 + typed] = "▌"
      }
      for (const p of scene.particles) {
        if (leaving > p.gone) continue
        const progress = Math.max(0, Math.min(1, (t - 2 - p.delay) / 1.25))
        if (t < 1.4) continue
        if (progress <= 0) {
          light(p.sx, p.sy, scene.base[p.sy][p.sx]) // the letters it will use light up
        } else if (progress < 1) {
          const e = ease(progress)
          const x = Math.round(p.sx + (p.tx - p.sx) * e)
          const y = Math.round(p.sy + (p.ty - p.sy) * e - Math.sin(Math.PI * e) * 2.5)
          light(x, y, p.ch)
        } else {
          ink[p.ty][p.tx] = p.ch
        }
      }
      // The citations flash once the answer has landed.
      if (t > 5 && t < 6.2 && Math.floor(time * 4) % 2 === 0) {
        for (const [x, y, n] of [[29, 11, 3], [29, 12, 3]] as const)
          for (let i = 0; i < n; i++) if (ink[y][x + i] !== " ") light(x + i, y, ink[y][x + i])
      }
      // A dot runs along the arrow while letters are in flight.
      if (t > 2 && t < 4.8) light(23 + (Math.floor(time * 8) % 2), 9, "•")

      inkPre.textContent = ink.map((r) => r.join("")).join("\n")
      accentPre.textContent = lit.map((r) => r.join("")).join("\n")
    }

    const loop = runLoop(el, draw, 6)
    const ro = new ResizeObserver(() => {
      fit()
      loop.redraw()
    })
    ro.observe(el)
    return () => {
      loop.stop()
      ro.disconnect()
    }
  }, [])

  const layer = "m-0 overflow-hidden font-mono leading-[1.3] whitespace-pre select-none"
  return (
    <div ref={box} className={cn("relative", className)}>
      <pre ref={ink} aria-hidden className={cn(layer, "text-foreground/80")} />
      <pre ref={accent} aria-hidden className={cn(layer, "absolute inset-0 text-[var(--ascii-accent)]")} />
      <p className="sr-only">
        An animation: three documents, a handbook, a billing FAQ and a policies page. The question
        "{QUESTION}" is asked, letters from the documents fly over and spell the answer, "
        {ANSWER.join(" ")}", with its two sources.
      </p>
    </div>
  )
}
