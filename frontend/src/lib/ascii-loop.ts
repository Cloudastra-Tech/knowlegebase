// The one animation loop every moving piece of ASCII shares (DESIGN.md, "Motion"):
// a single requestAnimationFrame for all of them, each drawn at its own frame rate
// while it's on screen and the tab is in front. With reduced motion (or the app's
// "Reduce effects") each draws one still frame, and that follows the setting live.
// The app's background field ("ambient") also rests while the page scrolls and while
// a dialog is open. Drawing has a budget per frame: past it the loop steps frame
// rates down, and back up once frames are cheap again ("The budget", below). A piece
// can also rest while nobody is using the page (`idle`): it keeps its last frame, and
// moves again on the next pointer, key, wheel, touch or focus.

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react"

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)"
const EFFECTS_KEY = "effects" // localStorage; public/theme-init.js reads it before paint

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
// idle: ms without input after which it rests, holding its frame, once `settled()`
// says it's at a good moment to hold (default: any moment).
// visibleAt: how much of the element must be on screen before it moves (0 to 1; a
// scene that tells a story starts once it can be seen, not at its first pixel). An
// element taller than the window needs at most nine tenths of what fits.
export type LoopOptions = {
  fps?: number
  still: number
  priority?: LoopPriority
  idle?: number
  settled?: () => boolean
  visibleAt?: number
}
// setFps: a new frame rate from the next frame (the budget still caps it).
// rest(ms): hold the frame for `ms` (the clock waits, no frames are drawn); wake():
// move again now, as at the end of a rest.
export type LoopHandle = {
  redraw(): void
  stop(): void
  setFps(fps: number): void
  rest(ms: number): void
  wake(): void
}

type Sub = {
  el: HTMLElement
  draw: (t: number) => void
  still: number
  fps: number
  priority: LoopPriority
  idle: number // 0: never rests for want of input
  settled?: () => boolean
  visibleAt: number
  until: number // performance.now() before which it rests (rest(ms)); 0 for none
  timer?: ReturnType<typeof setTimeout> // ends the rest
  visible: boolean
  last: number // when it last drew (rAF time), -1 while resting
  elapsed: number // its own clock, in seconds: runs only while it draws
  draws: number // frames drawn so far; the first few don't count towards the budget
}

const WINDOW = 30 // drawn frames per budget check
const WARMUP = 10 // a subscriber's first frames: cold code, a font still arriving
const SCROLL_REST_MS = 150
const RETRY_MS = 10_000 // a field gone still tries moving again after this, then 2x, ...
const RETRY_MAX_MS = 160_000

const subs = new Set<Sub>()
let raf = 0
let reduced = false
let effects: "full" | "reduced" | null = null // null: not read from the page yet
let pausedUntil = 0
let pauseTimer: ReturnType<typeof setTimeout> | undefined
let modalOpen = false
let warned = false
let lastInput = 0 // performance.now() of the last pointer, key, wheel, touch or focus
let idling = false // a piece rests for want of input: the next input wakes the loop

// The budget. A governor watches the 95th percentile of the cost of 30 drawn frames:
// over `over` ms it steps its pieces down a level, under `under` it steps back up (the
// gap keeps it from flapping). There are two, because their pieces never share a page:
// - app: the background field, spinners and the thinking line. They share frames with
//   the app's own work (an answer streaming in, typing), so 4ms between them. Levels:
//   the field at 12 fps, then 6, then still. A still field has no frames to measure,
//   so it tries moving again after a wait that doubles each time it has to stop.
// - hero: the landing's brain and ticker, the only things moving on the landing and
//   sign-in pages. A lower frame rate can't make a draw cheaper (the brain costs the
//   same at 20 fps as at 30), so held to the app's 4ms a brain that costs 5 stepped
//   down and never came back. It gets 8ms, half a 60 Hz frame: past that (a busy CPU,
//   a slow phone) it runs at 20 fps until its draws are under 6ms again.
type Governor = {
  over: number
  under: number
  max: number // its last level
  level: number
  costs: number[]
  overruns: number
  retry: number // ms the still level waits before trying again
  timer?: ReturnType<typeof setTimeout>
}

const governor = (over: number, under: number, max: number): Governor => ({
  over,
  under,
  max,
  level: 0,
  costs: [],
  overruns: 0,
  retry: RETRY_MS,
})
let app = governor(4, 2, 2)
let hero = governor(8, 6, 1)

let io: IntersectionObserver | null = null
let mql: MediaQueryList | null = null
let bodyObserver: MutationObserver | null = null

// The page may already say "Reduce effects" (theme-init.js, from the saved choice).
function effectsMode() {
  effects ??= document.documentElement.dataset.effects === "reduced" ? "reduced" : "full"
  return effects
}

const isStill = () => reduced || effectsMode() === "reduced"

function fpsOf(sub: Sub) {
  if (sub.priority === "ambient") {
    if (app.level >= 2) return 0
    if (app.level === 1) return Math.min(sub.fps, 6)
  }
  if (sub.priority === "hero" && hero.level >= 1) return Math.min(sub.fps, 20)
  return sub.fps
}

function resting(sub: Sub, now: number) {
  if (!sub.visible || document.hidden || isStill() || fpsOf(sub) <= 0) return true
  if (now < sub.until) return true
  if (sub.priority === "ambient" && (modalOpen || now < pausedUntil)) return true
  // Nobody's here: hold the frame (at a moment the piece says it can), once there is one.
  if (sub.idle && sub.draws > 0 && now - lastInput > sub.idle && (sub.settled?.() ?? true)) {
    idling = true
    return true
  }
  return false
}

function frame(now: number) {
  raf = 0
  let appCost = 0
  let heroCost = 0
  let appDrew = false
  let heroDrew = false
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
    const cost = performance.now() - start
    if (++sub.draws <= WARMUP) continue
    if (sub.priority === "hero") {
      heroCost += cost
      heroDrew = true
    } else {
      appCost += cost
      appDrew = true
    }
  }
  if (appDrew) account(app, appCost)
  if (heroDrew) account(hero, heroCost)
  wake()
}

function account(g: Governor, cost: number) {
  g.overruns = cost > g.over ? g.overruns + 1 : 0
  if (g.overruns > 2 && !warned && import.meta.env.DEV) {
    warned = true
    console.warn(`ascii-loop: ${g.overruns} frames in a row over ${g.over}ms (${cost.toFixed(1)}ms)`)
  }
  g.costs.push(cost)
  if (g.costs.length < WINDOW) return
  const p95 = g.costs.sort((a, b) => a - b)[Math.ceil(WINDOW * 0.95) - 1]
  g.costs = []
  if (p95 > g.over && g.level < g.max) {
    g.level++
    if (g === app && g.level === g.max) {
      // Still: nothing left to measure, so try again later, and later each time.
      const wait = g.retry
      g.retry = Math.min(g.retry * 2, RETRY_MAX_MS)
      clearTimeout(g.timer)
      g.timer = setTimeout(() => {
        g.level = g.max - 1
        wake()
      }, wait)
    }
  } else if (p95 < g.under) {
    if (g.level > 0) g.level--
    else g.retry = RETRY_MS // cheap at full speed: a later stop starts the wait over
  }
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

// Someone is using the page: what rested for want of input moves again.
const INPUT = ["pointermove", "pointerdown", "keydown", "wheel", "touchstart", "focusin"] as const
function onInput() {
  lastInput = performance.now()
  if (!idling) return
  idling = false
  wake()
}
// Back to the tab counts as input, so the art isn't found resting.
function onVisibility() {
  if (!document.hidden) lastInput = performance.now()
  wake()
}

// Radix marks <body> while a modal (dialog, sheet, menu) holds the page.
function onBodyChange() {
  const open = document.body.hasAttribute("data-scroll-locked")
  if (open === modalOpen) return
  modalOpen = open
  wake()
}

// Whether an observed element is on screen enough for `sub`.
function seen(sub: Sub, entry: IntersectionObserverEntry) {
  if (!entry.isIntersecting) return false
  if (!sub.visibleAt || entry.intersectionRatio === undefined) return true
  const fits = entry.rootBounds && entry.boundingClientRect.height
    ? entry.rootBounds.height / entry.boundingClientRect.height
    : 1
  return entry.intersectionRatio >= Math.min(sub.visibleAt, fits * 0.9) - 0.001
}

const THRESHOLDS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]

function start() {
  io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        for (const sub of subs) if (sub.el === entry.target) sub.visible = seen(sub, entry)
      }
      wake()
    },
    { threshold: THRESHOLDS },
  )
  mql = window.matchMedia(REDUCED_QUERY)
  reduced = mql.matches
  if (mql.addEventListener) mql.addEventListener("change", onReducedChange)
  else mql.addListener(onReducedChange)
  document.addEventListener("visibilitychange", onVisibility)
  document.addEventListener("scroll", onScroll, { capture: true, passive: true })
  lastInput = performance.now()
  for (const type of INPUT) window.addEventListener(type, onInput, { capture: true, passive: true })
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
  document.removeEventListener("visibilitychange", onVisibility)
  document.removeEventListener("scroll", onScroll, { capture: true })
  for (const type of INPUT) window.removeEventListener(type, onInput, { capture: true })
  idling = false
  bodyObserver?.disconnect()
  bodyObserver = null
  clearTimeout(pauseTimer)
  pausedUntil = 0
  clearTimeout(app.timer)
  app = governor(4, 2, 2)
  hero = governor(8, 6, 1)
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
    idle: opts.idle ?? 0,
    settled: opts.settled,
    visibleAt: opts.visibleAt ?? 0,
    until: 0,
    visible: false,
    last: -1,
    elapsed: 0,
    draws: 0,
  }
  subs.add(sub)
  io?.observe(el)
  if (isStill()) draw(opts.still)
  return {
    redraw: () => draw(isStill() ? sub.still : sub.elapsed),
    setFps: (fps: number) => {
      sub.fps = fps
    },
    rest: (ms: number) => {
      sub.until = performance.now() + ms
      clearTimeout(sub.timer)
      sub.timer = setTimeout(() => {
        sub.until = 0
        wake()
      }, ms)
    },
    wake: () => {
      clearTimeout(sub.timer)
      sub.until = 0
      wake()
    },
    stop: () => {
      clearTimeout(sub.timer)
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

// The app's "Reduce effects" (the account menu's theme items): every piece of ASCII
// still, and the glass opaque (index.css, :root[data-effects="reduced"]). Kept in
// localStorage, so the next visit starts with it before the first paint.
export function setEffects(mode: "full" | "reduced") {
  effects = mode
  if (mode === "reduced") document.documentElement.dataset.effects = "reduced"
  else delete document.documentElement.dataset.effects
  try {
    if (mode === "reduced") localStorage.setItem(EFFECTS_KEY, mode)
    else localStorage.removeItem(EFFECTS_KEY)
  } catch {
    // No storage (private mode, blocked): it holds for this visit.
  }
  if (isStill()) {
    cancelAnimationFrame(raf)
    raf = 0
    drawStill()
  } else wake()
}

export const getEffects = () => effectsMode()

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
  const { fps, still, priority, idle, visibleAt } = opts
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const h = subscribe(el, (t) => latest.current(t), { fps, still, priority, idle, visibleAt })
    handle.current = h
    return () => {
      h.stop()
      handle.current = null
    }
  }, [ref, fps, still, priority, idle, visibleAt])
  return handle
}
