import { useEffect, useRef } from "react"

import "@/components/ascii/ascii.css"
import { composerSignal } from "@/components/ascii/composer-signal"
import {
  approach,
  clamp,
  hash01,
  inZone,
  lineSlots,
  lineSnippets,
  mulberry32,
  nextLine,
  redact,
  zoneHalfWidth,
  zoneOf,
  type Box,
  type Zone,
} from "@/components/ascii/drift"
import { subscribe, whenFontsReady } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// "Redacted drift": the empty new chat's art, in place of the chat's field (one field
// on screen). Faint lines of made-up records drift sideways at three depths, redacted
// (▪▪▪ · ░░, word lengths kept). Round the composer is a readable zone: about every
// 8s one line passing through it decodes into a neutral record, faintly mint on dark,
// and scrambles back as it leaves or after about 5s. Decodes go round the lines.
// Focus or typing in the composer (composer-signal.ts, no React state) slows the
// drift to about 30% and draws the nearest lines a few px towards it.
//
// On the shared ASCII loop: 30 fps, the app's budget, resting while the page scrolls,
// under a dialog, offscreen (off /chat the chat is hidden) and in a hidden tab. With
// reduced motion or "Reduce effects" it's one still frame with one line readable.
// Canvas, DPR-aware, aria-hidden. The chat mounts it on the empty state only
// (chat-backdrop.tsx), and fades it out over 400ms when the first question is sent.

const FPS = 30
const STILL = -1 // the still frame's "time"

// Parallax depths: 0 nearest (bigger, a little stronger, faster). Alpha of the text colour.
const DEPTHS = [
  { size: 13, alpha: 0.12, speed: 18 },
  { size: 11.5, alpha: 0.1, speed: 14 },
  { size: 10, alpha: 0.08, speed: 10 },
]
const DEPTH_BY_RANK = [1, 0, 0, 2, 1, 2, 1, 2] // by distance from the composer
const GAP = "       "
const SCRAMBLE = "abcdefghijklmnopqrstuvwxyz0123456789"
const DECODE_EVERY = 8000
const FIRST_DECODE = 2200
const REVEAL_LEAD = 110
const REVEAL_MS = 420 // left to right: with the lead, about 500ms
const HOLD_MS = 5000
const SCRAMBLE_OUT = 240
const WAIT_MS = 1500 // how long past due the line decoded last gives way to the others
const PULL_RANGE = 230 // lines nearer the composer than this lean towards it
const LENS = 0.45 // inside the zone the redaction is lighter: the zone's edge
const DECODE_ALPHA = { dark: 0.56, light: 0.62 }
const SCRAMBLE_ALPHA = 0.24

export function RedactedDrift({ leaving = false, className }: { leaving?: boolean; className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const aura = useRef<HTMLDivElement>(null)
  const halo = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = box.current
    const cv = canvas.current
    if (!el || !cv) return
    const scene = driftScene(el, cv, aura.current, halo.current)
    const loop = subscribe(el, scene.draw, { fps: FPS, still: STILL, priority: "ambient" })
    const relayout = () => {
      scene.dirty()
      loop.redraw()
    }
    // The column, the composer (it grows with its text) and the empty state's words
    // (the starters arrive later) set where lines run.
    const ro = new ResizeObserver(relayout)
    ro.observe(el)
    for (const target of scene.watched()) ro.observe(target)
    // Scrolled (a tall empty state on a phone): measured again once it settles.
    let settle: ReturnType<typeof setTimeout> | undefined
    const root = scene.root()
    const onScroll = () => {
      clearTimeout(settle)
      settle = setTimeout(relayout, 160)
    }
    root.addEventListener("scroll", onScroll, { capture: true, passive: true })
    // The theme changed: other inks.
    const mo = new MutationObserver(() => {
      scene.restyle()
      loop.redraw()
    })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    let live = true
    whenFontsReady(el).then(() => live && relayout())
    return () => {
      live = false
      loop.stop()
      ro.disconnect()
      mo.disconnect()
      clearTimeout(settle)
      root.removeEventListener("scroll", onScroll, { capture: true })
    }
  }, [])

  return (
    <div
      ref={box}
      aria-hidden
      data-slot="ascii-field"
      data-mode="drift"
      data-leaving={leaving || undefined}
      className={cn(
        "pointer-events-none absolute inset-0 overflow-hidden font-mono text-foreground select-none",
        "transition-opacity duration-(--dur-drift-exit) ease-out-strong data-leaving:opacity-0",
        className,
      )}
    >
      {/* The zone's faint light, and the soft frost right round the composer (no blur:
          the composer's own glass is the screen's third blurred surface). */}
      <div ref={aura} className="kb-drift-aura absolute" />
      <canvas ref={canvas} className="absolute top-0 left-0" />
      <div ref={halo} className="kb-drift-halo absolute" />
    </div>
  )
}

type Depth = (typeof DEPTHS)[number] & { px: number; font: string; cw: number }
type Line = {
  baseY: number
  y: number
  d: Depth
  text: string
  snips: { start: number; len: number }[]
  tileW: number
  sprite: HTMLCanvasElement
  offset: number
}
type Decode = {
  line: Line
  absX: number
  len: number
  text: string
  still: boolean
  t0: number
  revealAt: number[]
  exitAt: Float64Array
  holdUntil: number
}

function driftScene(el: HTMLElement, cv: HTMLCanvasElement, aura: HTMLElement | null, halo: HTMLElement | null) {
  const ctx = cv.getContext("2d")
  const depths: Depth[] = DEPTHS.map((d) => ({ ...d, px: d.size, font: "", cw: 7 }))
  // The chat column: the composer and the empty state are in it.
  const root = (el.closest('[data-slot="chat-backdrop"]')?.parentElement ?? el.parentElement ?? el) as HTMLElement
  let dirty = true
  let W = 0
  let H = 0
  let dpr = 1
  let comp: Box = { x: 0, y: 0, w: 0, h: 0 }
  let content: Box | null = null
  let zone: Zone = { cx: 0, cy: 0, a: 1, b: 1 }
  let lines: Line[] = []
  let decode: Decode | null = null
  let nextDecodeAt: number | null = null
  let lastLine: number | null = null
  let dueAt: number | null = null // when the decode now waiting fell due
  let lastText = ""
  let lastT = 0
  let focus = 0
  let ink = { fg: "", read: "", dark: true }

  const composerEl = () => root.querySelector('[data-drift="composer"]')
  const contentEl = () => root.querySelector('[data-drift="content"]')

  function restyle() {
    const s = getComputedStyle(el)
    const dark = document.documentElement.classList.contains("dark")
    // The decoded line: a faint mint over the text colour on dark; grey on Paper.
    let read = s.color
    if (dark) {
      const probe = document.createElement("span")
      probe.style.color = "color-mix(in oklab, var(--signal) 40%, var(--foreground))"
      el.appendChild(probe)
      read = getComputedStyle(probe).color || s.color
      probe.remove()
    }
    ink = { fg: s.color, read, dark }
    for (const d of depths) d.font = `${d.px}px ${s.fontFamily}`
    for (const ln of lines) ln.sprite = sprite(redact(ln.text), ln.d)
  }

  // A line's redacted text, drawn once: a frame is then a few drawImage calls.
  function sprite(text: string, d: Depth) {
    const c = document.createElement("canvas")
    const cw = d.cw * dpr
    c.width = Math.ceil(text.length * cw) + 2
    c.height = Math.ceil(d.px * 1.6 * dpr)
    const g = c.getContext("2d")
    if (!g) return c
    g.fillStyle = ink.fg
    const bw = Math.max(2, Math.round(cw * 0.7))
    const bh = Math.max(2, Math.round(d.px * 0.5 * dpr))
    const mid = Math.round(c.height / 2)
    const top = mid - (bh >> 1)
    const dot = Math.max(2, Math.round(1.6 * dpr))
    for (let i = 0; i < text.length; i++) {
      const ch = text[i]
      if (ch === " ") continue
      if (ch === "▪" || ch === "░") {
        g.globalAlpha = ch === "▪" ? 1 : 0.42
        g.fillRect(Math.round(i * cw + (cw - bw) / 2), top, bw, bh)
      } else {
        g.globalAlpha = 0.8
        g.fillRect(Math.round(i * cw + (cw - dot) / 2), mid - (dot >> 1), dot, dot)
      }
    }
    return c
  }

  function measure() {
    dirty = false
    dpr = Math.min(2, window.devicePixelRatio || 1)
    const r = el.getBoundingClientRect()
    W = r.width
    H = r.height
    cv.width = Math.max(1, Math.round(W * dpr))
    cv.height = Math.max(1, Math.round(H * dpr))
    cv.style.width = `${W}px`
    cv.style.height = `${H}px`
    ctx?.setTransform(dpr, 0, 0, dpr, 0, 0)
    const rel = (n: Element | null): Box | null => {
      if (!n) return null
      const b = n.getBoundingClientRect()
      return { x: b.left - r.left, y: b.top - r.top, w: b.width, h: b.height }
    }
    comp = rel(composerEl()) ?? { x: W * 0.1, y: H - 96, w: W * 0.8, h: 72 }
    content = rel(contentEl())
    const viewport = rel(root.querySelector('[data-slot="message-scroller-viewport"]'))
    zone = zoneOf(comp, W, H)
    if (aura) {
      Object.assign(aura.style, {
        left: `${zone.cx - zone.a}px`,
        top: `${zone.cy - zone.b}px`,
        width: `${zone.a * 2}px`,
        height: `${zone.b * 2}px`,
      })
    }
    if (halo) {
      Object.assign(halo.style, {
        left: `${comp.x - 34}px`,
        top: `${comp.y - 22}px`,
        width: `${comp.w + 68}px`,
        height: `${comp.h + 44}px`,
      })
    }
    const s = getComputedStyle(el)
    if (!ink.fg) restyle()
    for (const d of depths) {
      // A step smaller on a phone, so a whole record fits the narrower zone.
      d.px = d.size * (W < 640 ? 0.85 : 1)
      d.font = `${d.px}px ${s.fontFamily}`
      if (ctx) {
        ctx.font = d.font
        d.cw = ctx.measureText("0").width || d.cw
      }
    }
    // Lines keep their place along the way when they're laid out again.
    const prev = lines
    const rand = mulberry32(7)
    lines = lineSlots({ top: viewport ? viewport.y : 44, content, composer: comp }, H).map((y, rank) => {
      const d = depths[DEPTH_BY_RANK[rank] ?? 2]
      let text = ""
      const snips: Line["snips"] = []
      for (const sn of lineSnippets(rank)) {
        snips.push({ start: text.length, len: sn.length })
        text += sn + GAP
      }
      const tileW = text.length * d.cw
      return {
        baseY: y,
        y,
        d,
        text,
        snips,
        tileW,
        sprite: sprite(redact(text), d),
        offset: prev[rank]?.offset ?? rand() * tileW,
      }
    })
    decode = null
  }

  // A line can decode inside the zone, but not behind the composer or the words.
  function candidate(ln: Line) {
    if (Math.abs(ln.baseY - zone.cy) > zone.b * 0.9) return false
    if (ln.baseY > comp.y - 16 && ln.baseY < comp.y + comp.h + 16) return false
    if (content && ln.baseY > content.y - 10 && ln.baseY < content.y + content.h + 10) return false
    return true
  }

  // The record on this line that is wholly inside the zone (and on screen) with the
  // most travel left; not the one decoded last.
  function findSnippet(ln: Line) {
    const hx = zoneHalfWidth(zone, ln.baseY, 0.95)
    if (!hx) return null
    const left = Math.max(8, zone.cx - hx)
    const right = Math.min(W - 8, zone.cx + hx)
    const cw = ln.d.cw
    const off = mod(ln.offset, ln.tileW)
    const baseAbs = ln.offset - off
    let best: { absX: number; sx: number; start: number; len: number } | null = null
    for (let c = 0; c < 3; c++) {
      for (const sn of ln.snips) {
        if (ln.text.substr(sn.start, sn.len) === lastText) continue
        const absX = baseAbs + c * ln.tileW + sn.start * cw
        const sx = absX - ln.offset
        if (sx >= left && sx + sn.len * cw <= right && (!best || sx > best.sx)) best = { absX, sx, ...sn }
      }
    }
    return best
  }

  // The still frame: the first candidate line set so its first record sits mid-zone.
  function placeForStill(ln: Line) {
    const sn = ln.snips[0]
    ln.offset = sn.start * ln.d.cw - (zone.cx - (sn.len * ln.d.cw) / 2)
  }

  function startDecode(now: number, still: boolean) {
    let cands = lines.map((_, i) => i).filter((i) => candidate(lines[i]))
    // The smallest text is hard to read decoded: the nearer depths first.
    const near = cands.filter((i) => lines[i].d !== depths[2])
    if (near.length) cands = near
    if (!cands.length) return false
    const first = nextLine(cands, lastLine)!
    const at = cands.indexOf(first)
    const ordered = cands.slice(at).concat(cands.slice(0, at))
    if (still) placeForStill(lines[ordered[0]])
    for (const i of ordered) {
      // Round the lines: the one decoded last waits while another can go, but not
      // for long (on a phone the other may seldom fit a whole record).
      if (!still && i === lastLine && cands.length > 1 && now - (dueAt ?? now) < WAIT_MS) continue
      const ln = lines[i]
      const hit = findSnippet(ln)
      if (!hit) continue
      const len = hit.len
      decode = {
        line: ln,
        absX: hit.absX,
        len,
        text: ln.text.substr(hit.start, len),
        still,
        t0: now,
        revealAt: Array.from({ length: len }, (_, j) =>
          still ? -Infinity : now + REVEAL_LEAD + (j / Math.max(1, len - 1)) * REVEAL_MS,
        ),
        exitAt: new Float64Array(len).fill(-1),
        holdUntil: still ? Infinity : now + REVEAL_LEAD + REVEAL_MS + HOLD_MS,
      }
      if (!still) {
        lastLine = i
        lastText = decode.text
      }
      return true
    }
    return false
  }

  const scrambleChar = (j: number, now: number, ch: string) =>
    ch === "·" ? ch : SCRAMBLE[Math.floor(hash01(j * 131 + Math.floor(now / 55) * 7919) * SCRAMBLE.length)]

  // For the decoding line: the cells cut out of the redacted sprite (runs) and the
  // glyphs drawn there instead (0 redacted, 1 scrambling, 2 readable).
  function decodeCells(dc: Decode, ln: Line, now: number, base: number) {
    const cw = ln.d.cw
    const runs: number[] = []
    const glyphs: [string, number, number][] = []
    let alive = false
    let runX = -1
    let runEnd = -1
    const close = () => {
      if (runX >= 0) runs.push(runX, runEnd - runX)
      runX = -1
    }
    for (let j = 0; j < dc.len; j++) {
      const ch = dc.text[j]
      if (ch === " ") {
        close()
        continue
      }
      const x = base + j * cw
      if (dc.exitAt[j] < 0) {
        if (now >= dc.holdUntil) dc.exitAt[j] = now + j * 14 // timed out: back, left to right
        else if (now >= dc.revealAt[j] && !inZone(zone, x + cw / 2, ln.y, 0.95)) dc.exitAt[j] = now // left the zone
      }
      const e = dc.exitAt[j]
      const st = e >= 0 && now >= e + SCRAMBLE_OUT ? 0 : (e >= 0 && now >= e) || now < dc.revealAt[j] ? 1 : 2
      if (!st) {
        close()
        continue
      }
      alive = true
      if (runX < 0) runX = x
      runEnd = x + cw
      glyphs.push([st === 2 ? ch : scrambleChar(j, now, ch), x, st])
    }
    close()
    return { runs, glyphs, alive }
  }

  function zonePath(c: CanvasRenderingContext2D) {
    c.moveTo(zone.cx + zone.a, zone.cy)
    c.ellipse(zone.cx, zone.cy, zone.a, zone.b, 0, 0, Math.PI * 2)
  }

  function drawLine(c: CanvasRenderingContext2D, ln: Line, now: number) {
    const d = ln.d
    const sw = ln.sprite.width / dpr
    const sh = ln.sprite.height / dpr
    const top = Math.round((ln.y - sh / 2) * dpr) / dpr
    const off = mod(ln.offset, ln.tileW)
    const x0 = Math.round(-off * dpr) / dpr // whole device pixels: crisp blocks
    const dc = decode && decode.line === ln ? decode : null
    const cells = dc ? decodeCells(dc, ln, now, x0 + dc.absX - (ln.offset - off)) : null
    const tiles = () => {
      for (let x = x0; x < W; x += ln.tileW) c.drawImage(ln.sprite, x, top, sw, sh)
    }
    const crosses = Math.abs(ln.y - zone.cy) < zone.b
    // Outside the zone: full strength.
    c.save()
    if (crosses) {
      c.beginPath()
      c.rect(0, top, W, sh)
      zonePath(c)
      c.clip("evenodd")
    }
    c.globalAlpha = d.alpha
    tiles()
    c.restore()
    // Inside: lighter, less any cells decoding.
    if (crosses) {
      c.save()
      c.beginPath()
      zonePath(c)
      if (cells) for (let i = 0; i < cells.runs.length; i += 2) c.rect(cells.runs[i], top, cells.runs[i + 1], sh)
      c.clip("evenodd")
      c.globalAlpha = d.alpha * LENS
      tiles()
      c.restore()
    }
    if (dc && cells) {
      c.font = d.font
      c.textBaseline = "middle"
      for (const [ch, x, st] of cells.glyphs) {
        c.fillStyle = st === 2 ? ink.read : ink.fg
        c.globalAlpha = st === 2 ? DECODE_ALPHA[ink.dark ? "dark" : "light"] : SCRAMBLE_ALPHA
        c.fillText(ch, x, ln.y + 0.5)
      }
      c.globalAlpha = 1
      if (!cells.alive) {
        nextDecodeAt = Math.max(dc.t0 + DECODE_EVERY, now + 2000)
        decode = null
      }
    }
  }

  function draw(t: number) {
    if (dirty) measure()
    if (!ctx || !W) return
    const still = t < 0
    const now = still ? 0 : t * 1000
    const dt = still ? 0 : clamp(t - lastT, 0, 0.1)
    if (!still) lastT = t
    focus = still ? 0 : approach(focus, composerSignal.active() ? 1 : 0, dt, 0.2) // ~600ms to settle
    if (still && !decode?.still) {
      decode = null
      startDecode(now, true)
    } else if (!still && decode?.still) {
      decode = null
      nextDecodeAt = now + 1200
    }
    ctx.clearRect(0, 0, W, H)
    const speed = 1 - 0.7 * focus // focused: about 30%
    const mid = comp.y + comp.h / 2
    for (const ln of lines) {
      ln.offset += ln.d.speed * speed * dt
      // Lines near the composer lean 4–8px towards it while it's in use.
      const dy = mid - ln.baseY
      const dist = Math.abs(dy)
      const pull = dist > 6 && dist < PULL_RANGE ? Math.min(dist, 4 + 4 * (1 - dist / PULL_RANGE)) : 0
      ln.y = ln.baseY + Math.sign(dy) * pull * focus
      drawLine(ctx, ln, now)
    }
    if (!still && !decode) {
      nextDecodeAt ??= now + FIRST_DECODE
      // Nothing fits the zone just now (often, on a phone): look again shortly.
      if (now >= nextDecodeAt) {
        dueAt ??= now
        if (startDecode(now, false)) dueAt = null
        else nextDecodeAt = now + 300
      }
    }
    // For the e2e specs and a trace: whether a line reads right now.
    const reading = !!decode && !decode.still
    if (reading !== el.hasAttribute("data-decoding")) el.toggleAttribute("data-decoding", reading)
  }

  return {
    draw,
    restyle,
    dirty: () => {
      dirty = true
    },
    root: () => root,
    watched: () => [composerEl(), contentEl()].filter((n): n is Element => n !== null),
  }
}

const mod = (a: number, n: number) => ((a % n) + n) % n
