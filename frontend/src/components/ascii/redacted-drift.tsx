import { useEffect, useRef } from "react"

import "@/components/ascii/ascii.css"
import { composerSignal } from "@/components/ascii/composer-signal"
import {
  approach,
  clamp,
  hash01,
  inZone,
  lensFalloff,
  lensSpan,
  lineSlots,
  lineSnippets,
  mulberry32,
  nearestLine,
  nextLine,
  redact,
  union,
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
// It answers the pointer, quietly. With a mouse (a fine pointer that hovers), a soft
// round "reading lens" follows it: the redacted cells inside resolve, through a short
// scramble, into the line's own made-up record, and close again as it moves on. A
// click on a line (or the empty space near one; a tap on a touch screen) decodes the
// whole line for a while, with one faint ring where it landed. The pointer is listened
// to on the chat column (the canvas takes no events, so everything above it works as
// ever) and only written into the scene, never into React state. The lens and the
// decodes only ever show the made-up records (drift.ts), never anything of yours.
//
// On the shared ASCII loop: 30 fps, the app's budget, resting while the page scrolls,
// under a dialog, offscreen (off /chat the chat is hidden) and in a hidden tab. With
// reduced motion or "Reduce effects" it's one still frame with one line readable, and
// neither the lens nor a click changes it.
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
// The reading lens: its radius (px), how fast it follows the pointer (s), how long a
// cell scrambles coming into it and going out (ms), and how strongly it reads (below
// a decode, which is the signal's).
const LENS_R = 104
const LENS_R_SMALL = 84 // under 640px
const LENS_FOLLOW = 0.06
const LENS_IN = 170
const LENS_OUT = 150
const LENS_ALPHA = { dark: 0.42, light: 0.48 }
// A click: how near a line it must land (px; a tap takes the nearest), how far the
// reveal runs out from it and how fast, and how long the line then stays readable.
const CLICK_REACH = 30
const CLICK_SPREAD = 520
const CLICK_REVEAL = 380
const CLICK_HOLD = 8000
const RIPPLE_MS = 380
const RIPPLE_R = 14
// Where a press is the art's: the column's blank space (the empty transcript, the
// space round the home's sections), never a control, the composer or any words.
const BLANK =
  '[data-drift-blank], [data-slot="message-scroller"], [data-slot="message-scroller-viewport"], [data-slot="message-scroller-content"]'

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
    // (the starters and recent chats arrive later) set where lines run.
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
    const unlisten = scene.listen()
    window.addEventListener("resize", scene.dirty)
    let live = true
    whenFontsReady(el).then(() => live && relayout())
    return () => {
      live = false
      loop.stop()
      ro.disconnect()
      mo.disconnect()
      unlisten()
      window.removeEventListener("resize", scene.dirty)
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
type LensCell = { t: number; out: number; seen: boolean }
type Line = {
  baseY: number
  y: number
  d: Depth
  text: string
  snips: { start: number; len: number }[]
  tileW: number
  sprite: HTMLCanvasElement
  offset: number
  // Cells in (or just out of) the reading lens, by their index along the line: when
  // each came in, and when it went out (-1 while in).
  lens: Map<number, LensCell>
}
type Decode = {
  line: Line
  absX: number
  len: number
  text: string
  still: boolean
  free: boolean // a click's: the whole line, not held to the zone
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
  let origin = { x: 0, y: 0 } // the box on the page, for the pointer
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
  let stillNow = false
  // The pointer as the listeners leave it: the lens follows it each frame. A click
  // waits for the next frame, which has the scene's clock.
  const pointer = { x: 0, y: 0, in: false }
  const lens = { x: 0, y: 0, r: LENS_R, on: false }
  let click: { x: number; y: number; line: number } | null = null
  let ripple: { x: number; y: number; t0: number } | null = null
  let lensShown = false

  const composerEl = () => root.querySelector('[data-drift="composer"]')
  const contentEl = () => root.querySelector('[data-drift="content"]')
  const belowEl = () => root.querySelector('[data-drift="below"]')

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
    origin = { x: r.left, y: r.top }
    lens.r = W < 640 ? LENS_R_SMALL : LENS_R
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
    const below = rel(belowEl())
    const viewport = rel(root.querySelector('[data-slot="message-scroller-viewport"]'))
    // The heading just over the composer (the centred new chat): the zone is round both.
    const near = content !== null && comp.y - (content.y + content.h) < 80
    zone = zoneOf(near && content ? union(content, comp) : comp, W, H)
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
    // Lines keep their place along the way when they're laid out again. What's below
    // the composer, once it has words in it, they run under, never across.
    const prev = lines
    const rand = mulberry32(7)
    const words = below && below.h > 0 ? below : null
    lines = lineSlots({ top: viewport ? viewport.y : 44, content, composer: comp, below: words }, H).map(
      (y, rank) => {
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
          lens: new Map(),
        }
      },
    )
    decode = null
    // For the e2e specs: where the lines run.
    el.dataset.lines = lines.map((ln) => Math.round(ln.y)).join(",")
  }

  // A line can decode inside the zone, but not behind the composer or the words.
  function candidate(ln: Line) {
    if (Math.abs(ln.baseY - zone.cy) > zone.b * 0.9) return false
    if (ln.baseY > comp.y - 16 && ln.baseY < comp.y + comp.h + 16) return false
    if (content && ln.baseY > content.y - 10 && ln.baseY < content.y + content.h + 10) return false
    return true
  }

  // Where a line's tiles are this frame: the first tile's left edge (whole device
  // pixels, for crisp blocks) and the line's own position of that tile. Cell n (its
  // index along the line) is at x0 + n * cw - baseAbs.
  function place(ln: Line) {
    const off = mod(ln.offset, ln.tileW)
    return { x0: Math.round(-off * dpr) / dpr, baseAbs: ln.offset - off }
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
        free: false,
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

  // A click: the whole line on screen decodes, outwards from where it landed, and
  // stays readable longer than the zone's do. The cells that drift in from the right
  // meanwhile are part of it too.
  function clickDecode(ln: Line, x: number, now: number) {
    const cw = ln.d.cw
    const { x0, baseAbs } = place(ln)
    const n0 = Math.floor((baseAbs - x0) / cw)
    const n1 = Math.ceil((W + baseAbs - x0) / cw) + Math.ceil(((CLICK_HOLD / 1000) * ln.d.speed + 40) / cw)
    const len = n1 - n0
    let text = ""
    for (let n = n0; n < n1; n++) text += ln.text[mod(n, ln.text.length)]
    const at = (x - x0 + baseAbs) / cw
    decode = {
      line: ln,
      absX: n0 * cw,
      len,
      text,
      still: false,
      free: true,
      t0: now,
      revealAt: Array.from(
        { length: len },
        (_, j) => now + 40 + Math.min(1, (Math.abs(n0 + j - at) * cw) / CLICK_SPREAD) * CLICK_REVEAL,
      ),
      exitAt: new Float64Array(len).fill(-1),
      holdUntil: now + 40 + CLICK_REVEAL + CLICK_HOLD,
    }
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
        if (now >= dc.holdUntil) dc.exitAt[j] = now + j * (dc.free ? 5 : 14) // timed out: back, left to right
        else if (!dc.free && now >= dc.revealAt[j] && !inZone(zone, x + cw / 2, ln.y, 0.95)) dc.exitAt[j] = now // left the zone
      }
      const e = dc.exitAt[j]
      const st = e >= 0 && now >= e + SCRAMBLE_OUT ? 0 : (e >= 0 && now >= e) || now < dc.revealAt[j] ? 1 : 2
      if (!st) {
        close()
        continue
      }
      alive = true
      if (x > W || x + cw < 0) {
        close()
        continue // off screen: alive, not drawn
      }
      if (runX < 0) runX = x
      runEnd = x + cw
      glyphs.push([st === 2 ? ch : scrambleChar(j, now, ch), x, st])
    }
    close()
    return { runs, glyphs, alive }
  }

  // The lens on one line: the cells now inside come in (scrambling first), the ones it
  // has left go out (scrambling, then redacted again). Never a decode's cells, which
  // read already. The cells to draw, or null for none.
  function lensLine(ln: Line, now: number, x0: number, baseAbs: number, dc: Decode | null) {
    const cw = ln.d.cw
    const span = lens.on ? lensSpan(lens, ln.y, cw, x0 - baseAbs) : null
    if (!span && !ln.lens.size) return null
    const d0 = dc ? Math.round(dc.absX / cw) : 0
    const d1 = dc ? d0 + dc.len : 0
    for (const cell of ln.lens.values()) cell.seen = false
    if (span) {
      for (let n = span[0]; n <= span[1]; n++) {
        if ((n >= d0 && n < d1) || ln.text[mod(n, ln.text.length)] === " ") continue
        const cell = ln.lens.get(n)
        if (!cell) ln.lens.set(n, { t: now, out: -1, seen: true })
        else {
          cell.seen = true
          if (cell.out >= 0) {
            cell.t = now
            cell.out = -1
          }
        }
      }
    }
    for (const [n, cell] of ln.lens) {
      if (!cell.seen && cell.out < 0) cell.out = now
      if ((cell.out >= 0 && now - cell.out > LENS_OUT) || (n >= d0 && n < d1)) ln.lens.delete(n)
    }
    return ln.lens.size ? ln.lens : null
  }

  function zonePath(c: CanvasRenderingContext2D) {
    c.moveTo(zone.cx + zone.a, zone.cy)
    c.ellipse(zone.cx, zone.cy, zone.a, zone.b, 0, 0, Math.PI * 2)
  }

  function drawLine(c: CanvasRenderingContext2D, ln: Line, now: number) {
    const d = ln.d
    const cw = d.cw
    const sw = ln.sprite.width / dpr
    const sh = ln.sprite.height / dpr
    const top = Math.round((ln.y - sh / 2) * dpr) / dpr
    const { x0, baseAbs } = place(ln)
    const dc = decode && decode.line === ln ? decode : null
    const cells = dc ? decodeCells(dc, ln, now, x0 + dc.absX - baseAbs) : null
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
    // Inside: lighter.
    if (crosses) {
      c.save()
      c.beginPath()
      zonePath(c)
      c.clip()
      c.globalAlpha = d.alpha * LENS
      tiles()
      c.restore()
    }
    // The cells that read (a decode's, the lens's) are cut out of the redacted line.
    // A line is alone in its band (lines are 40px or more apart), so this clears
    // nothing else.
    if (cells) for (let i = 0; i < cells.runs.length; i += 2) c.clearRect(cells.runs[i], top, cells.runs[i + 1], sh)
    const lensCells = lensLine(ln, now, x0, baseAbs, dc)
    if (lensCells) {
      c.font = d.font
      c.textBaseline = "middle"
      c.fillStyle = ink.fg
      const read = LENS_ALPHA[ink.dark ? "dark" : "light"]
      for (const [n, cell] of lensCells) {
        const x = x0 + n * cw - baseAbs
        c.clearRect(x - 0.5, top, cw + 1, sh)
        const ch = ln.text[mod(n, ln.text.length)]
        const scrambling = cell.out >= 0 || now - cell.t < LENS_IN
        const dx = x + cw / 2 - lens.x
        const dy = ln.y - lens.y
        c.globalAlpha = scrambling ? SCRAMBLE_ALPHA : read * lensFalloff(Math.sqrt(dx * dx + dy * dy), lens.r)
        c.fillText(scrambling ? scrambleChar(n, now, ch) : ch, x, ln.y + 0.5)
      }
      c.globalAlpha = 1
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
    stillNow = still
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
    // The lens follows the pointer, from where it came in (never sweeping across); a
    // click decodes its line. Neither in the still frame.
    if (still || !pointer.in) {
      lens.on = false
      if (still) for (const ln of lines) ln.lens.clear()
    } else {
      if (!lens.on) {
        lens.x = pointer.x
        lens.y = pointer.y
      }
      lens.on = true
      lens.x = approach(lens.x, pointer.x, dt, LENS_FOLLOW)
      lens.y = approach(lens.y, pointer.y, dt, LENS_FOLLOW)
    }
    if (click && !still && lines[click.line]) {
      clickDecode(lines[click.line], click.x, now)
      ripple = { x: click.x, y: click.y, t0: now }
    }
    click = null
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
    // A click's one faint ring, fading where it is (no growing).
    if (ripple) {
      const p = (now - ripple.t0) / RIPPLE_MS
      if (still || p >= 1) ripple = null
      else {
        ctx.globalAlpha = 0.2 * (1 - p) * (1 - p)
        ctx.strokeStyle = ink.fg
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.arc(ripple.x, ripple.y, RIPPLE_R, 0, Math.PI * 2)
        ctx.stroke()
        ctx.globalAlpha = 1
      }
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
    // For the e2e specs and a trace: whether a line reads right now, and the lens.
    const reading = !!decode && !decode.still
    if (reading !== el.hasAttribute("data-decoding")) el.toggleAttribute("data-decoding", reading)
    const lensing = lines.some((ln) => ln.lens.size > 0)
    if (lensing !== lensShown) {
      lensShown = lensing
      el.toggleAttribute("data-lens", lensing)
    }
  }

  // The pointer, on the chat column. A move only writes `pointer`. A press on blank
  // space keeps the focus where it is (the composer's, usually); its click decodes the
  // line nearest it. Anywhere else (a control, the composer, words that could be
  // selected) a press is left alone.
  function listen() {
    const hover = window.matchMedia("(hover: hover) and (pointer: fine)")
    let down: { x: number; y: number; type: string } | null = null
    const ours = (target: EventTarget | null) =>
      target === root || (target instanceof Element && target.matches(BLANK))
    const onMove = (e: PointerEvent) => {
      pointer.in = e.pointerType !== "touch" && hover.matches
      pointer.x = e.clientX - origin.x
      pointer.y = e.clientY - origin.y
    }
    const onLeave = () => {
      pointer.in = false
    }
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY, type: e.pointerType }
    }
    const onMouseDown = (e: MouseEvent) => {
      if (e.button === 0 && !stillNow && ours(e.target)) e.preventDefault()
    }
    const onClick = (e: MouseEvent) => {
      const from = down
      down = null
      if (stillNow || !lines.length || e.button !== 0 || e.detail > 1 || !ours(e.target)) return
      if (from && Math.hypot(e.clientX - from.x, e.clientY - from.y) > 6) return // a drag
      const sel = window.getSelection()
      if (sel && !sel.isCollapsed) return
      const x = e.clientX - origin.x
      const y = e.clientY - origin.y
      if (x < 0 || y < 0 || x > W || y > H) return
      const touch = from !== null && from.type !== "mouse"
      const line = nearestLine(
        lines.map((ln) => ln.y),
        y,
        touch ? Infinity : CLICK_REACH,
      )
      if (line >= 0) click = { x, y, line }
    }
    root.addEventListener("pointermove", onMove, { passive: true })
    root.addEventListener("pointerleave", onLeave)
    root.addEventListener("pointerdown", onDown, { passive: true })
    root.addEventListener("mousedown", onMouseDown)
    root.addEventListener("click", onClick)
    return () => {
      root.removeEventListener("pointermove", onMove)
      root.removeEventListener("pointerleave", onLeave)
      root.removeEventListener("pointerdown", onDown)
      root.removeEventListener("mousedown", onMouseDown)
      root.removeEventListener("click", onClick)
    }
  }

  return {
    draw,
    restyle,
    dirty: () => {
      dirty = true
    },
    root: () => root,
    listen,
    watched: () => [composerEl(), contentEl(), belowEl()].filter((n): n is Element => n !== null),
  }
}

const mod = (a: number, n: number) => ((a % n) + n) % n
