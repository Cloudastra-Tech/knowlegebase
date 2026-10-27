// The one animation loop every moving piece of ASCII shares (DESIGN.md, "Motion"):
// a single requestAnimationFrame for all of them, each drawn at its own frame rate
// while it's on screen and the tab is in front. With reduced motion (or the app's
// "Reduce effects") each draws one still frame, and that follows the setting live.
// The app's background field ("ambient") also rests while the page scrolls and while
// a dialog is open. All the drawing together gets about 4ms a frame; when it keeps
// taking longer, the loop steps the frame rates down.

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react"

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)"

export const reducedMotion = () => window.matchMedia(REDUCED_QUERY).matches

// One character cell of `el`'s monospace font, in px.
export function measureCell(el: HTMLElement) {
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

// The cell once `el`'s web font has arrived: measured before, a cell is the fallback
// font's, and the art draws a grid that's too wide or too narrow.
export async function whenFontsReady(el: HTMLElement) {
  const fonts = document.fonts
  if (fonts) {
    const s = getComputedStyle(el)
    try {
      await fonts.load(`${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`, "0")
      await fonts.ready
    } catch {
      // A font that can't load: the fallback's cell is the right one then.
    }
  }
  return measureCell(el)
}

// hero: the landing's brain and ticker; ambient: the app's background field, which
// rests while scrolling and under dialogs; ui: spinners and the thinking line.
export type LoopPriority = "hero" | "ambient" | "ui"
export type LoopOptions = { fps?: number; still: number; priority?: LoopPriority }
export type LoopHandle = { redraw(): void; stop(): void }

type Sub = {
  el: HTMLElement
  draw: (t: number) => void
  still: number
  fps: number
  priority: LoopPriority
  visible: boolean
  last: number // when it last drew (rAF time), -1 while resting
  elapsed: number // its own clock, in seconds: runs only while it draws
}

const BUDGET_MS = 4
const WINDOW = 30 // frames per budget check
const SCROLL_REST_MS = 150

const subs = new Set<Sub>()
let raf = 0
let reduced = false
let effects: "full" | "reduced" = "full"
let pausedUntil = 0
let pauseTimer: ReturnType<typeof setTimeout> | undefined
let modalOpen = false
let degrade = 0 // 0 full; 1 ambient 6 fps, hero 20; 2 ambient still too
let costs: number[] = []
let overruns = 0
let warned = false

let io: IntersectionObserver | null = null
let mql: MediaQueryList | null = null
let bodyObserver: MutationObserver | null = null

const isStill = () => reduced || effects === "reduced"

function fpsOf(sub: Sub) {
  if (sub.priority === "ambient") {
    if (degrade >= 2) return 0
    if (degrade === 1) return Math.min(sub.fps, 6)
  }
  if (sub.priority === "hero" && degrade >= 1) return Math.min(sub.fps, 20)
  return sub.fps
}

function resting(sub: Sub, now: number) {
  if (!sub.visible || document.hidden || isStill() || fpsOf(sub) <= 0) return true
  return sub.priority === "ambient" && (modalOpen || now < pausedUntil)
}

function frame(now: number) {
  raf = 0
  let cost = 0
  let drew = false
  for (const sub of subs) {
    if (resting(sub, performance.now())) {
      sub.last = -1
      continue
    }
    const interval = 1000 / fpsOf(sub)
    if (sub.last < 0) sub.last = now - interval
    const since = now - sub.last
    if (since < interval - 1) continue
    sub.elapsed += Math.min(since, 100) / 1000
    sub.last = now
    const start = performance.now()
    sub.draw(sub.elapsed)
    cost += performance.now() - start
    drew = true
  }
  if (drew) account(cost)
  wake()
}

// The budget: past 4ms at the 95th percentile of the last 30 frames, a step down.
function account(cost: number) {
  overruns = cost > BUDGET_MS ? overruns + 1 : 0
  if (overruns > 2 && !warned && import.meta.env.DEV) {
    warned = true
    console.warn(`ascii-loop: ${overruns} frames in a row over ${BUDGET_MS}ms (${cost.toFixed(1)}ms)`)
  }
  costs.push(cost)
  if (costs.length < WINDOW) return
  const sorted = costs.sort((a, b) => a - b)
  if (sorted[Math.ceil(WINDOW * 0.95) - 1] > BUDGET_MS && degrade < 2) degrade++
  costs = []
}

function wake() {
  if (raf) return
  const now = performance.now()
  for (const sub of subs) {
    if (!resting(sub, now)) {
      raf = requestAnimationFrame(frame)
      return
    }
  }
}

function drawStill() {
  for (const sub of subs) sub.draw(sub.still)
}

function onReducedChange(e: MediaQueryListEvent) {
  reduced = e.matches
  if (isStill()) {
    cancelAnimationFrame(raf)
    raf = 0
    drawStill()
  } else wake()
}

function onScroll() {
  for (const sub of subs) {
    if (sub.priority === "ambient") {
      pauseFor(SCROLL_REST_MS)
      return
    }
  }
}

// Radix marks <body> while a modal (dialog, sheet, menu) holds the page.
function onBodyChange() {
  const open = document.body.hasAttribute("data-scroll-locked")
  if (open === modalOpen) return
  modalOpen = open
  wake()
}

function start() {
  io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      for (const sub of subs) if (sub.el === entry.target) sub.visible = entry.isIntersecting
    }
    wake()
  })
  mql = window.matchMedia(REDUCED_QUERY)
  reduced = mql.matches
  if (mql.addEventListener) mql.addEventListener("change", onReducedChange)
  else mql.addListener(onReducedChange)
  document.addEventListener("visibilitychange", wake)
  document.addEventListener("scroll", onScroll, { capture: true, passive: true })
  bodyObserver = new MutationObserver(onBodyChange)
  bodyObserver.observe(document.body, { attributes: true, attributeFilter: ["data-scroll-locked"] })
  modalOpen = document.body.hasAttribute("data-scroll-locked")
}

function end() {
  cancelAnimationFrame(raf)
  raf = 0
  io?.disconnect()
  io = null
  if (mql?.removeEventListener) mql.removeEventListener("change", onReducedChange)
  else mql?.removeListener(onReducedChange)
  mql = null
  document.removeEventListener("visibilitychange", wake)
  document.removeEventListener("scroll", onScroll, { capture: true })
  bodyObserver?.disconnect()
  bodyObserver = null
  clearTimeout(pauseTimer)
  pausedUntil = 0
}

// Draws `draw(seconds)` at up to `fps` frames a second while `el` is on screen. The
// clock only runs while it's drawing, so a scene starts from the top when first seen.
export function subscribe(el: HTMLElement, draw: (t: number) => void, opts: LoopOptions): LoopHandle {
  if (!subs.size) start()
  const sub: Sub = {
    el,
    draw,
    still: opts.still,
    fps: opts.fps ?? 30,
    priority: opts.priority ?? "hero",
    visible: false,
    last: -1,
    elapsed: 0,
  }
  subs.add(sub)
  io?.observe(el)
  if (isStill()) draw(opts.still)
  return {
    redraw: () => draw(isStill() ? sub.still : sub.elapsed),
    stop: () => {
      if (!subs.delete(sub)) return
      if (![...subs].some((s) => s.el === el)) io?.unobserve(el)
      if (!subs.size) end()
    },
  }
}

// The landing's original call: about 30 frames a second.
export function runLoop(el: HTMLElement, draw: (t: number) => void, still: number) {
  return subscribe(el, draw, { still })
}

// The app's "Reduce effects": every piece of ASCII still, and the glass opaque
// (index.css, :root[data-effects="reduced"]).
export function setEffects(mode: "full" | "reduced") {
  effects = mode
  if (mode === "reduced") document.documentElement.dataset.effects = "reduced"
  else delete document.documentElement.dataset.effects
  if (isStill()) {
    cancelAnimationFrame(raf)
    raf = 0
    drawStill()
  } else wake()
}

// Rests the background field for `ms` (scrolling, a burst of heavy work).
export function pauseFor(ms: number) {
  pausedUntil = Math.max(pausedUntil, performance.now() + ms)
  clearTimeout(pauseTimer)
  pauseTimer = setTimeout(wake, pausedUntil - performance.now())
}

// subscribe() for a component: `draw` may change every render; the loop keeps
// running and calls the latest one.
export function useAsciiLoop(
  ref: RefObject<HTMLElement | null>,
  draw: (t: number) => void,
  opts: LoopOptions,
) {
  const latest = useRef(draw)
  useLayoutEffect(() => {
    latest.current = draw
  })
  const handle = useRef<LoopHandle | null>(null)
  const { fps, still, priority } = opts
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const h = subscribe(el, (t) => latest.current(t), { fps, still, priority })
    handle.current = h
    return () => {
      h.stop()
      handle.current = null
    }
  }, [ref, fps, still, priority])
  return handle
}
