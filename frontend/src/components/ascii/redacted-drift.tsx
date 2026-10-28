import { useEffect, useRef } from "react"

import "@/components/ascii/ascii.css"
import { composerSignal } from "@/components/ascii/composer-signal"
import {
  approach,
  blurTile,
  clamp,
  covered,
  frostSlices,
  hash01,
  inZone,
  lensFalloff,
  lensSpan,
  lineSnippets,
  lineSpans,
  mulberry32,
  nearestLine,
  nextLine,
  pageRows,
  redact,
  union,
  zoneHalfWidth,
  zoneOf,
  type Band,
  type Box,
  type Cover,
  type Mark,
  type Zone,
} from "@/components/ascii/drift"
import { subscribe, whenFontsReady } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// "Redacted drift": the empty new chat's art, in place of the chat's field (one field
// on screen). Faint lines of made-up records drift sideways at three depths, redacted
// (▪▪▪ · ░░, word lengths kept), over the whole window, under the sidebar too: fainter
// under words, at full ink under glass, which frosts them (what's over the art says
// which: data-drift). Round the composer is a readable zone: about every 8s one line
// passing through it decodes into a neutral record, faintly mint on dark,
// and scrambles back as it leaves or after about 5s. Decodes go round the lines.
// On a phone (a column under 640px) nothing decodes by itself, which on so small a
// screen was too busy: a tap decodes a line, as below.
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
// On the shared ASCII loop: 20 fps (30 while a mouse is over it, for the lens), the
// app's budget, resting while the page scrolls, under a dialog, offscreen (off /chat
// the chat is hidden), in a hidden tab, and after 20s with nobody at the page (no
// pointer, key, wheel, touch or focus), at a moment no line is decoding: it holds that
// frame, and drifts on from it at the next input. With
// reduced motion or "Reduce effects" it's one still frame with one line readable, and
// neither the lens nor a click changes it.
// Canvas, DPR-aware, aria-hidden. The chat mounts it on the empty state only
// (chat-backdrop.tsx), and fades it out over 400ms when the first question is sent.
//
// The glass over it (the composer, the recent chats' card, the sidebar's pane) frosts
// it, and a backdrop-filter would blur all of that glass again every frame. So the art
// frosts itself: under each glass, on a small canvas of its own the compositor
// stretches to the glass's size, it draws its lines from tiles blurred once (drift.ts,
// blurTile), as strong as the glass's own blur would leave them, and marks that glass
// `data-drift-frost` (ascii.css), which keeps its tint and edge but drops its
// backdrop-filter while the art is on screen behind it. Leaving, off screen, or glass
// without a blur (Reduce effects, reduced transparency): unmarked, the CSS blur as ever.

// The drift is slow and sideways: 20 frames a second shows it as smoothly as 30, at
// two thirds of the work. The lens follows a pointer, so with one over the art it's 30.
const FPS = 20
const FPS_LENS = 30
const STILL = -1 // the still frame's "time"
const IDLE_MS = 20_000
const FROST_SAMPLES = 4 // samples per sigma in a blurred tile: finer would add nothing
const FROST_EVERY = 2 // the frost under glass is drawn every 2nd frame
const CORNER_PAD = 8 // px rounder than the glass, at its corners only

// Parallax depths: 0 nearest (bigger, a little stronger, faster). Alpha of the text colour.
const DEPTHS = [
  { size: 13, alpha: 0.12, speed: 18 },
  { size: 11.5, alpha: 0.1, speed: 14 },
  { size: 10, alpha: 0.08, speed: 10 },
]
const DEPTH_BY_RANK = [1, 0, 0, 2, 1, 2, 1, 2] // by distance from the composer
const DEPTH_FAR = [2, 1, 2, 0, 2, 1] // and further out, round again
// Under what sits over the art (`data-drift` on it): faint under words (the heading,
// the starters, the fine print), so they always read; at full ink under glass (the
// composer, the recent chats' card, the sidebar's pane), as a blur averages faint
// dashes to a flat tone: the glass, not a faded line, keeps the words on it readable,
// and the lines show through it as soft shapes moving.
const QUIET = 0.3
const UNDER_GLASS = 8 // 0.64–0.96 of the text colour, by depth
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
const LENS_R_SMALL = 84 // under PHONE
const PHONE = 640 // a column narrower than this (px): a phone's sizes, and no zone decodes
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
// What moves when the sidebar folds (components/ui/sidebar.tsx).
const FOLD = '[data-slot="sidebar-container"], [data-slot="sidebar-gap"]'
const BLANK =
  '[data-drift-blank], [data-slot="message-scroller"], [data-slot="message-scroller-viewport"], [data-slot="message-scroller-content"]'

export function RedactedDrift({ leaving = false, className }: { leaving?: boolean; className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const aura = useRef<HTMLDivElement>(null)
  const live = useRef<{ scene: ReturnType<typeof driftScene>; redraw: () => void } | null>(null)

  useEffect(() => {
    const el = box.current
    const cv = canvas.current
    if (!el || !cv) return
    const scene = driftScene(el, cv, aura.current)
    const loop = subscribe(el, scene.draw, {
      fps: FPS,
      still: STILL,
      priority: "ambient",
      idle: IDLE_MS,
      settled: scene.settled,
    })
    scene.onPointer((over) => loop.setFps(over ? FPS_LENS : FPS))
    const relayout = () => {
      scene.dirty()
      loop.redraw()
    }
    live.current = { scene, redraw: loop.redraw }
    // Frosted here only while it's on screen (off /chat the glass frosts what's there).
    const io = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1]
      if (entry && scene.frost({ shown: entry.isIntersecting })) loop.redraw()
    })
    io.observe(el)
    // The column, the composer (it grows with its text) and what sits over the art
    // (the starters and recent chats arrive later) set how lines are drawn.
    const ro = new ResizeObserver(relayout)
    ro.observe(el)
    // Those elements can be swapped for new ones (the sidebar or the composer drawn
    // again), and a detached one never reports: the sidebar folding would then move the
    // composer with no new measure, leaving the lines at full ink where the glass was.
    // So whatever is there now is what's watched (observing a new one measures again).
    let watched: Element[] = []
    const rewatch = () => {
      const now = scene.watched()
      if (now.length === watched.length && now.every((n, i) => n === watched[i])) return
      for (const n of watched) if (!now.includes(n)) ro.unobserve(n)
      for (const n of now) if (!watched.includes(n)) ro.observe(n)
      watched = now
    }
    rewatch()
    let pending = 0
    const swapped = new MutationObserver(() => {
      if (!pending) pending = requestAnimationFrame(() => ((pending = 0), rewatch()))
    })
    swapped.observe(document.body, { childList: true, subtree: true })
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
    // The sidebar folded or opened: the composer and the recent chats move sideways
    // with no change of size, which no ResizeObserver hears. Measured again once the
    // sidebar has settled, so the glass the art frosts is where it now is.
    const onFold = (e: TransitionEvent) => {
      if (e.target instanceof Element && e.target.matches(FOLD)) relayout()
    }
    document.addEventListener("transitionend", onFold, true)
    let mounted = true
    whenFontsReady(el).then(() => mounted && relayout())
    return () => {
      mounted = false
      live.current = null
      loop.stop()
      io.disconnect()
      scene.unmark()
      ro.disconnect()
      mo.disconnect()
      swapped.disconnect()
      cancelAnimationFrame(pending)
      unlisten()
      window.removeEventListener("resize", scene.dirty)
      document.removeEventListener("transitionend", onFold, true)
      clearTimeout(settle)
      root.removeEventListener("scroll", onScroll, { capture: true })
    }
  }, [])

  // Leaving (the first question sent): the glass blurs for itself again, as the chat's
  // own field arrives under it.
  useEffect(() => {
    const at = live.current
    if (at?.scene.frost({ leaving })) at.redraw()
  }, [leaving])

  return (
    <div
      ref={box}
      aria-hidden
      data-slot="ascii-field"
      data-mode="drift"
      data-leaving={leaving || undefined}
      className={cn(
        // Fixed to the window, as the chat's field is: it runs under the sidebar too.
        "pointer-events-none fixed inset-0 overflow-hidden font-mono text-foreground select-none",
        "transition-opacity duration-(--dur-drift-exit) ease-out-strong data-leaving:opacity-0",
        className,
      )}
    >
      {/* The zone's faint light. The lines run on under the composer and the recent
          chats, where those surfaces' own glass frosts them. */}
      <div ref={aura} className="kb-drift-aura absolute" />
      <canvas ref={canvas} className="absolute top-0 left-0" />
    </div>
  )
}

type Depth = (typeof DEPTHS)[number] & { px: number; font: string; cw: number }
// Glass the art frosts itself: its box (in the art's coordinates), corner radius, the
// std-dev of its blur (px), and the element, marked while it's frosted here.
type Frost = { box: Box; round: number; sigma: number; el: Element }
// Where it's drawn: a small canvas just under the glass, at the blurred tiles' own
// coarse scale (px to samples), which the compositor stretches to the glass's size.
type FrostLayer = { canvas: HTMLCanvasElement; g: CanvasRenderingContext2D | null; sx: number; sy: number }
// A blurred tile of a line, for glass of one blur: `pad` px of blur above and below
// the sprite's own band, its alpha scaled by `gain` (blurTile).
type Frosted = { canvas: HTMLCanvasElement; pad: number; gain: number; h: number }
// Where a line shows through one glass: the stretches across it at their strengths,
// and the copies to draw: the line itself, its reflection in the glass's top or bottom
// edge (a backdrop-filter mirrors its edges), or, for a line only partly under it, the
// part inside, reflected, as a line on the edge (`w`, how much of the line that is).
type FrostRun = {
  g: number
  slices: [number, number, number][]
  copies: { at: "self" | "up" | "down" | "top" | "bottom"; w: number }[]
}
type LensCell = { t: number; out: number; seen: boolean }
type Line = {
  baseY: number
  y: number
  d: Depth
  text: string
  snips: { start: number; len: number }[]
  tileW: number
  dpr: number // the sprites' scale
  sprite: HTMLCanvasElement
  offset: number
  // The stretches it's drawn in, and how strongly (lineSpans), for this layout; those
  // drawn sharp (not under glass the art frosts itself); and how it shows under that glass.
  spans: [number, number, number][]
  sharp: [number, number, number][]
  frost: FrostRun[]
  blurred: Map<number, Frosted> // by the glass's blur
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

function driftScene(el: HTMLElement, cv: HTMLCanvasElement, aura: HTMLElement | null) {
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
  let covers: Cover[] = []
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
  let pointerHook: ((over: boolean) => void) | null = null
  // Frosting here: the glass it covers (measure), whether the art is on screen and not
  // leaving, and the elements marked for it.
  let frosts: Frost[] = []
  let frostSkip = 0
  let frostDue = true // a new layout: drawn on the next frame, whatever its turn
  const layers: FrostLayer[] = []
  let shown = false
  let leaving = false
  const marked = new Set<Element>()
  // Frosting needs pixels written (a canvas that can't, as in tests, leaves it to CSS).
  const canFrost = typeof ctx?.createImageData === "function"
  let blurGlass = false // glass with a blur over the art, at the last layout

  const composerEl = () => root.querySelector('[data-drift="composer"]')
  const contentEl = () => root.querySelector('[data-drift="content"]')
  // What sits over the art: the composer and glass (lines stronger), words (fainter).
  // "below" is the home's sections' box, watched for its size: its parts say what they are.
  const belowEl = () => root.querySelector('[data-drift="below"]')
  const coverEls = () => [...root.querySelectorAll('[data-drift]:not([data-drift="below"])')]
  // The sidebar's pane, beside the column (a sheet on a phone, so nothing there then).
  const paneEl = () => document.querySelector('[data-slot="sidebar-inner"]')

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
    for (const ln of lines) {
      ln.sprite = sprite(redact(ln.text), ln.d)
      ln.blurred.clear()
    }
  }

  // A line's redacted text as marks on its sprite, in device px: a block for a letter
  // (solid) or a digit (light), a dot for anything else.
  function marksOf(text: string, d: Depth) {
    const cw = d.cw * dpr
    const height = Math.ceil(d.px * 1.6 * dpr)
    const bw = Math.max(2, Math.round(cw * 0.7))
    const bh = Math.max(2, Math.round(d.px * 0.5 * dpr))
    const mid = Math.round(height / 2)
    const dot = Math.max(2, Math.round(1.6 * dpr))
    const bands: Band[] = [
      { top: mid - (bh >> 1), h: bh },
      { top: mid - (dot >> 1), h: dot },
    ]
    const marks: Mark[] = []
    for (let i = 0; i < text.length; i++) {
      const ch = text[i]
      if (ch === " ") continue
      if (ch === "▪" || ch === "░") marks.push({ x: Math.round(i * cw + (cw - bw) / 2), w: bw, a: ch === "▪" ? 1 : 0.42, band: 0 })
      else marks.push({ x: Math.round(i * cw + (cw - dot) / 2), w: dot, a: 0.8, band: 1 })
    }
    return { width: Math.ceil(text.length * cw) + 2, height, marks, bands }
  }

  // A line's redacted text, drawn once: a frame is then a few drawImage calls.
  function sprite(text: string, d: Depth) {
    const c = document.createElement("canvas")
    const m = marksOf(text, d)
    c.width = m.width
    c.height = m.height
    const g = c.getContext("2d")
    if (!g) return c
    g.fillStyle = ink.fg
    for (const k of m.marks) {
      const band = m.bands[k.band]
      g.globalAlpha = k.a
      g.fillRect(k.x, band.top, k.w, band.h)
    }
    return c
  }

  // The line's tile as glass of blur `sigma` shows it, made the first time it's needed.
  function blurredOf(ln: Line, sigma: number) {
    const have = ln.blurred.get(sigma)
    if (have) return have
    const m = marksOf(redact(ln.text), ln.d)
    const scale = FROST_SAMPLES / sigma
    const t = blurTile(
      ln.tileW,
      m.height / dpr,
      m.marks.map((k) => ({ ...k, x: k.x / dpr, w: k.w / dpr })),
      m.bands.map((b) => ({ top: b.top / dpr, h: b.h / dpr })),
      sigma,
      scale,
    )
    const c = document.createElement("canvas")
    c.width = t.w
    c.height = t.h
    const g = c.getContext("2d")
    if (g) {
      const img = g.createImageData(t.w, t.h)
      for (let i = 0; i < t.data.length; i++) img.data[i * 4 + 3] = Math.round(t.data[i] * 255)
      g.putImageData(img, 0, 0)
      g.globalCompositeOperation = "source-in"
      g.fillStyle = ink.fg
      g.fillRect(0, 0, t.w, t.h)
    }
    const out = { canvas: c, pad: t.pad, gain: t.gain, h: t.h / scale }
    ln.blurred.set(sigma, out)
    return out
  }

  // Frosted here or not: the glass marked to match (its backdrop-filter off, ascii.css).
  function mark(els: Element[]) {
    for (const n of marked) {
      if (!els.includes(n)) {
        n.removeAttribute("data-drift-frost")
        marked.delete(n)
      }
    }
    for (const n of els) {
      if (marked.has(n)) continue
      n.setAttribute("data-drift-frost", "")
      marked.add(n)
    }
  }

  function measure() {
    dirty = false
    dpr = Math.min(2, window.devicePixelRatio || 1)
    const r = el.getBoundingClientRect()
    W = r.width
    H = r.height
    origin = { x: r.left, y: r.top }
    lens.r = W < PHONE ? LENS_R_SMALL : LENS_R
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
    covers = []
    frosts = []
    blurGlass = false
    const pane = paneEl()
    const frosting = canFrost && shown && !leaving
    for (const n of pane ? [...coverEls(), pane] : coverEls()) {
      const b = rel(n)
      if (!b || !b.w || !b.h) continue
      const kind = n === pane ? "glass" : n.getAttribute("data-drift")
      if (kind === "composer" || kind === "glass") {
        // Glass is its own edge.
        const st = getComputedStyle(n)
        const round = n === pane ? 0 : parseFloat(st.borderTopLeftRadius) || 0
        covers.push({ box: b, k: UNDER_GLASS, round })
        const sigma = parseFloat(st.getPropertyValue(n === pane ? "--glass-blur-pane" : "--glass-blur")) || 0
        if (sigma > 0) blurGlass = true
        if (frosting && sigma > 0) frosts.push({ box: b, round, sigma, el: n })
      } else covers.push({ box: { x: b.x - 10, y: b.y - 4, w: b.w + 20, h: b.h + 8 }, k: QUIET }) // words: a little room round them
    }
    mark(frosts.map((f) => f.el))
    layout(frosts)
    frostDue = true
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
    const s = getComputedStyle(el)
    if (!ink.fg) restyle()
    for (const d of depths) {
      // A step smaller on a phone, so a whole record fits the narrower zone.
      d.px = d.size * (W < PHONE ? 0.85 : 1)
      d.font = `${d.px}px ${s.fontFamily}`
      if (ctx) {
        ctx.font = d.font
        d.cw = ctx.measureText("0").width || d.cw
      }
    }
    // Lines keep their place along the way when they're laid out again. They run the
    // whole page; under words and glass they're drawn as those want (lineSpans).
    const prev = lines
    const rand = mulberry32(7)
    const top = viewport ? viewport.y : 44
    lines = pageRows({ top, bottom: H, anchor: comp.y + comp.h / 2 }, H).map(
      (y, rank): Line => {
        const d = depths[DEPTH_BY_RANK[rank] ?? DEPTH_FAR[rank % DEPTH_FAR.length]]
        let text = ""
        const snips: Line["snips"] = []
        for (const sn of lineSnippets(rank)) {
          snips.push({ start: text.length, len: sn.length })
          text += sn + GAP
        }
        const tileW = text.length * d.cw
        // The same line drawn the same size: its sprites carry over (a pane sliding
        // open lays the art out again every frame).
        const was = prev[rank]
        const same = was && was.text === text && was.tileW === tileW && was.d.px === d.px && was.dpr === dpr
        const spans = lineSpans(
          W,
          y,
          d.px * 0.5 + 8, // and the 8px a line may lean towards the composer
          Math.abs(y - zone.cy) < zone.b ? { cx: zone.cx, hx: zoneHalfWidth(zone, y) } : null,
          LENS,
          covers,
        )
        return {
          baseY: y,
          y,
          d,
          text,
          snips,
          tileW,
          dpr,
          sprite: same ? was.sprite : sprite(redact(text), d),
          offset: prev[rank]?.offset ?? rand() * tileW,
          spans,
          ...frostRuns(y, d, spans),
          lens: new Map(),
          blurred: same ? was.blurred : new Map(),
        }
      },
    )
    decode = null
    // For the e2e specs: where the lines run.
    el.dataset.lines = lines.map((ln) => Math.round(ln.y)).join(",")
  }

  // How a line at height y shows under the glass the art frosts: which of its spans
  // are left to draw sharp, and its runs under each glass (FrostRun).
  function frostRuns(y: number, d: Depth, spans: [number, number, number][]) {
    let sharp = spans
    const frost: FrostRun[] = []
    const half = d.px * 0.25 // the ink's half height (its blocks are half the size tall)
    frosts.forEach((f, g) => {
      const top = f.box.y
      const bottom = f.box.y + f.box.h
      const inside = Math.min(bottom, y + half) - Math.max(top, y - half)
      if (inside <= 0) return
      // Under this glass it's the frost layer's: nothing sharp there.
      sharp = cut(sharp, f.box.x, f.box.x + f.box.w)
      const copies: FrostRun["copies"] = []
      const reach = 3 * f.sigma
      if (y >= top && y <= bottom) {
        copies.push({ at: "self", w: 1 })
        if (y - top < reach) copies.push({ at: "up", w: 1 })
        if (bottom - y < reach) copies.push({ at: "down", w: 1 })
      } else copies.push({ at: y < top ? "top" : "bottom", w: Math.min(1, inside / half) })
      frost.push({ g, slices: frostSlices(spans, f.box.x, f.box.x + f.box.w, f.sigma), copies })
    })
    return { sharp, frost }
  }

  // A canvas under each glass the art frosts, placed over it and shaped as it is.
  function layout(fs: Frost[]) {
    while (layers.length > fs.length) layers.pop()!.canvas.remove()
    fs.forEach((f, i) => {
      let layer = layers[i]
      if (!layer) {
        const c = document.createElement("canvas")
        c.dataset.frost = ""
        c.className = "absolute"
        el.appendChild(c)
        layer = layers[i] = { canvas: c, g: c.getContext("2d"), sx: 1, sy: 1 }
      }
      const scale = FROST_SAMPLES / f.sigma
      const c = layer.canvas
      c.width = Math.max(1, Math.ceil(f.box.w * scale))
      c.height = Math.max(1, Math.ceil(f.box.h * scale))
      // Exactly the glass's box: never a sample past its edge.
      layer.sx = c.width / f.box.w
      layer.sy = c.height / f.box.h
      Object.assign(c.style, {
        left: `${f.box.x}px`,
        top: `${f.box.y}px`,
        width: `${f.box.w}px`,
        height: `${f.box.h}px`,
      })
    })
  }

  // The glass the art frosts: the lines under each, blurred, on its own small canvas.
  function drawFrost() {
    frosts.forEach((f, g) => {
      const layer = layers[g]
      const c = layer?.g
      if (!c) return
      const { x, y, w, h } = f.box
      c.setTransform(1, 0, 0, 1, 0, 0)
      c.clearRect(0, 0, layer.canvas.width, layer.canvas.height)
      // Drawn in the art's coordinates, at the layer's scale.
      c.setTransform(layer.sx, 0, 0, layer.sy, -x * layer.sx, -y * layer.sy)
      // The glass's rounded corners, in the drawing (a border-radius on the canvas would
      // be a mask for the compositor to apply every frame). A little rounder than the
      // glass: a coarse sample's soft edge then stays inside its corner.
      c.save()
      if (f.round > 0 && c.roundRect) {
        c.beginPath()
        c.roundRect(x, y, w, h, f.round + CORNER_PAD)
        c.clip()
      }
      for (const ln of lines) {
        for (const run of ln.frost) {
          if (run.g !== g) continue
          const b = blurredOf(ln, f.sigma)
          const sh = ln.sprite.height / dpr
          const { x0 } = place(ln)
          for (const cp of run.copies) {
            const cy =
              cp.at === "self" ? ln.y
              : cp.at === "up" ? 2 * y - ln.y
              : cp.at === "down" ? 2 * (y + h) - ln.y
              : cp.at === "top" ? y
              : y + h
            const top = cy - sh / 2 - b.pad
            for (const [a, z, k] of run.slices) {
              c.globalAlpha = Math.min(1, ln.d.alpha * k) * b.gain * cp.w
              tiles(c, b.canvas, ln.tileW, x0, a, z, top, b.h)
            }
          }
        }
      }
      c.restore()
      c.globalAlpha = 1
    })
  }

  // A line's tile drawn across [a, b) (repeating from x0), from `img`, the tile scaled.
  function tiles(
    c: CanvasRenderingContext2D,
    img: HTMLCanvasElement,
    tileW: number,
    x0: number,
    a: number,
    b: number,
    top: number,
    h: number,
  ) {
    const per = img.width / tileW
    for (let x = x0 + Math.floor((a - x0) / tileW) * tileW; x < b; x += tileW) {
      const from = Math.max(a, x)
      const to = Math.min(b, x + tileW)
      if (to > from) c.drawImage(img, (from - x) * per, 0, (to - from) * per, img.height, from, top, to - from, h)
    }
  }

  // A line can decode inside the zone, but not behind the composer, glass or words.
  function candidate(ln: Line) {
    if (Math.abs(ln.baseY - zone.cy) > zone.b * 0.9) return false
    return !covers.some((c) => ln.baseY > c.box.y - 16 && ln.baseY < c.box.y + c.box.h + 16)
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
      if (x > W || x + cw < 0 || covered(covers, x + cw / 2, ln.y)) {
        close()
        continue // off screen, or under words or glass: alive, not drawn
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
        if (covered(covers, x0 + n * cw - baseAbs + cw / 2, ln.y)) continue
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

  function drawLine(c: CanvasRenderingContext2D, ln: Line, now: number) {
    const d = ln.d
    const cw = d.cw
    const sw = ln.sprite.width / dpr
    const sh = ln.sprite.height / dpr
    const top = Math.round((ln.y - sh / 2) * dpr) / dpr
    const { x0, baseAbs } = place(ln)
    const dc = decode && decode.line === ln ? decode : null
    const cells = dc ? decodeCells(dc, ln, now, x0 + dc.absX - baseAbs) : null
    // Stretch by stretch (lineSpans): full in open space, lighter in the zone, faint
    // under words, stronger under glass. Only the tiles that reach a stretch are drawn.
    for (const [a, b, k] of ln.sharp) {
      const whole = a <= 0 && b >= W
      if (!whole) {
        c.save()
        c.beginPath()
        c.rect(a, top, b - a, sh)
        c.clip()
      }
      c.globalAlpha = Math.min(1, d.alpha * k)
      for (let x = x0 + Math.max(0, Math.floor((a - x0) / ln.tileW)) * ln.tileW; x < b; x += ln.tileW)
        c.drawImage(ln.sprite, x, top, sw, sh)
      if (!whole) c.restore()
      else c.globalAlpha = 1
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
    // Frost at half the frame rate: blurred that much, a line moving half a px more
    // shows nothing, and a canvas left alone isn't sent to the compositor again.
    if (frosts.length && (still || frostDue || ++frostSkip >= FROST_EVERY)) {
      frostSkip = 0
      frostDue = false
      drawFrost()
    }
    if (!still && !decode && W >= PHONE) {
      nextDecodeAt ??= now + FIRST_DECODE
      // Nothing fits the zone just now (often, on a narrow window): look again shortly.
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
      const was = pointer.in
      pointer.in = e.pointerType !== "touch" && hover.matches
      if (pointer.in !== was) pointerHook?.(pointer.in)
      pointer.x = e.clientX - origin.x
      pointer.y = e.clientY - origin.y
    }
    const onLeave = () => {
      if (pointer.in) pointerHook?.(false)
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
    // A moment to hold still for the idle rest: no line decoding, no ring fading.
    settled: () => (!decode || decode.still) && !ripple,
    // On screen (the chat shown) and not leaving: frosting here. A change lays it out again.
    frost(on: { shown?: boolean; leaving?: boolean }) {
      const was = shown && !leaving
      shown = on.shown ?? shown
      leaving = on.leaving ?? leaving
      if (!canFrost || was === (shown && !leaving)) return false
      dirty = true
      return blurGlass // only glass with a blur over it looks any different

    },
    // Told when a mouse comes over the art (the lens follows it) and leaves.
    onPointer(fn: (over: boolean) => void) {
      pointerHook = fn
    },
    unmark: () => {
      mark([])
      layout([])
    },
    root: () => root,
    listen,
    watched: () => [composerEl(), belowEl(), paneEl(), ...coverEls()].filter((n): n is Element => n !== null),
  }
}

const mod = (a: number, n: number) => ((a % n) + n) % n

// Spans less the stretch [a, b).
function cut(spans: [number, number, number][], a: number, b: number) {
  const out: [number, number, number][] = []
  for (const [x0, x1, k] of spans) {
    if (x1 <= a || x0 >= b) out.push([x0, x1, k])
    else {
      if (x0 < a) out.push([x0, a, k])
      if (x1 > b) out.push([b, x1, k])
    }
  }
  return out
}
