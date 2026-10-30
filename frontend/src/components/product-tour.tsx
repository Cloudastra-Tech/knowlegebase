import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react"
import { createPortal } from "react-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { animate } from "motion/react"
import { Popover as PopoverPrimitive } from "radix-ui"
import { useLocation, useNavigate, useNavigationType } from "react-router"

import { Button } from "@/components/ui/button"
import { useSidebar } from "@/components/ui/sidebar"
import { onboarding, type Onboarding, type Tours } from "@/lib/api"
import { useActiveRole, useUser } from "@/lib/auth-context"
import { useChatState } from "@/lib/chat-bridge"
import { ease } from "@/lib/motion"
import { queries } from "@/lib/queries"
import { isChatPath } from "@/lib/routes"
import {
  bodyOf,
  nextIndex,
  pageFor,
  shouldAutoStart,
  START_TOUR,
  stepsFor,
  type TourContext,
  type TourId,
  type TourOutcome,
  type TourStep,
} from "@/lib/tour"
import { cn } from "@/lib/utils"

// The product tour (lib/tour.ts has the steps and the rules): the app dims, the
// step's element is lit through a soft cut-out, and a small card beside it says what
// it is, with "n of N", Skip, Back and Next (Done on the last).
//
// - One fixed overlay, portalled: nothing is inserted in the page, so nothing in it
//   moves. The cut-out is a box with a page-sized shadow (the dim), at the element's
//   rect plus a little padding and rounded as it is; it follows scrolling and
//   resizing (a ResizeObserver and scroll listeners, one measure per frame).
// - The card is a modal Radix Popover anchored to that rect (a virtual anchor), so
//   it flips to stay on screen: a labelled dialog that holds the focus and gives it
//   back on close. ← and → are Back and Next, Esc skips (and does nothing else: an
//   answer being written keeps going). The dim takes every click.
//   On a phone it docks to the bottom (or the top, when the element is low) as a
//   sheet-like panel.
// - Motion: the cut-out glides to the next element (220ms, ease-out-strong) and the
//   card fades in from 0.97 toward it. With reduced motion or "Reduce effects", the
//   cut-out jumps and the card only fades.
// - It starts by itself once, on a member's first sign-in (shouldAutoStart), after
//   the first paint has settled, never over a dialog or an answer being written; the
//   account menu's Take the tour and Admin tour start it again (START_TOUR), at once,
//   even while starting by itself still waits. Finished or skipped, the server keeps
//   that they saw it. The person going to another page in the middle (Back in the
//   browser, a link) ends it, as skipped; the app's own redirect (a replace: a chat
//   that failed to load) doesn't: the step is looked for again on the new page.

const PAD = 6 // round the lit element
const EDGE = 4 // the cut-out stays this far inside the window
const GAP = 12 // between the cut-out and the card
const WAIT_SAME_PAGE = 250 // a step's element on the page the tour is on
const WAIT_NEW_PAGE = 4000 // ...on a page the tour just went to (it loads its data)
const SETTLE = 900 // after the first paint, before it starts by itself

// Something holding the page: a dialog, a sheet, a menu (but not the tour's own card).
const BUSY =
  '[role="dialog"][data-state="open"]:not([data-tour-card]), [role="alertdialog"][data-state="open"], [role="menu"][data-state="open"]'

const quiet = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
  document.documentElement.dataset.effects === "reduced"

// On screen to look at: laid out, not hidden, not in an inert part of the app (the
// chat while another screen is open), not disabled.
function onScreen(el: Element): boolean {
  if (el.closest("[inert]")) return false
  if ((el as HTMLButtonElement).disabled || el.getAttribute("aria-disabled") === "true") return false
  const r = el.getBoundingClientRect()
  if (r.width <= 0 || r.height <= 0) return false
  if (typeof el.checkVisibility === "function") return el.checkVisibility({ visibilityProperty: true })
  const style = getComputedStyle(el)
  return style.visibility !== "hidden" && style.display !== "none"
}

function findTarget(step: TourStep): Element | null {
  for (const selector of step.target ?? []) {
    const found = [...document.querySelectorAll(selector)].filter(onScreen)
    if (found.length) return step.pick === "last" ? found[found.length - 1] : found[0]
  }
  return null
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// The step's element, once it's there (a page the tour went to draws it after loading).
async function waitForTarget(step: TourStep, ms: number, alive: () => boolean) {
  const until = performance.now() + ms
  await sleep(32)
  while (alive()) {
    const el = findTarget(step)
    if (el || performance.now() >= until) return el
    await sleep(50)
  }
  return null
}

// No dialog, sheet or menu open, twice in a row (one closing may still be there), and
// nothing else `busy` says (an answer being written).
async function waitForCalm(ms: number, alive: () => boolean, busyToo: () => boolean = () => false) {
  const until = performance.now() + ms
  let calm = 0
  while (alive() && performance.now() < until) {
    const busy =
      document.querySelector(BUSY) || document.body.hasAttribute("data-scroll-locked") || busyToo()
    calm = busy ? 0 : calm + 1
    if (calm >= 2) return true
    await sleep(100)
  }
  return false
}

interface Spot {
  x: number
  y: number
  w: number
  h: number
  r: number
}

// The cut-out round an element: its rect plus PAD, kept inside the window, rounded
// as the element is.
function spotOf(el: Element): Spot {
  const b = el.getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight
  const x1 = Math.max(b.left - PAD, EDGE)
  const y1 = Math.max(b.top - PAD, EDGE)
  const x2 = Math.min(b.right + PAD, vw - EDGE)
  const y2 = Math.min(b.bottom + PAD, vh - EDGE)
  const w = Math.max(0, x2 - x1)
  const h = Math.max(0, y2 - y1)
  const radius = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0
  return {
    x: Math.round(x1),
    y: Math.round(y1),
    w: Math.round(w),
    h: Math.round(h),
    r: Math.round(Math.min(radius + PAD, w / 2, h / 2)),
  }
}

const rect = (x: number, y: number, width: number, height: number) =>
  ({ x, y, width, height, top: y, left: x, right: x + width, bottom: y + height, toJSON: () => ({}) }) as DOMRect

const sameSpot =(a: Spot | null, b: Spot | null) =>
  a === b || (!!a && !!b && a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h && a.r === b.r)

// Scrolls the element into view first, if any of it is out of it, and waits for the
// scroll to end (the card comes once the element is still).
async function bringIntoView(el: Element, alive: () => boolean) {
  const b = el.getBoundingClientRect()
  const vh = window.innerHeight
  if (b.top >= EDGE && b.bottom <= vh - EDGE) return
  el.scrollIntoView({
    block: b.height > vh * 0.6 ? "start" : "center",
    inline: "nearest",
    behavior: quiet() ? "auto" : "smooth",
  })
  const until = performance.now() + 1500
  let top = NaN
  while (alive() && performance.now() < until) {
    await sleep(50)
    const now = el.getBoundingClientRect().top
    if (now === top) return
    top = now
  }
}

interface View {
  tour: TourId
  steps: TourStep[]
  index: number
  target: Element | null
  seeking: boolean // going to a step: the card is hidden, its keys do nothing
}

export function ProductTour({ chatAt }: { chatAt: string }) {
  const user = useUser()
  const role = useActiveRole()
  const { isMobile, setOpenMobile } = useSidebar()
  const location = useLocation()
  const navigationType = useNavigationType()
  const navigate = useNavigate()
  const answering = useChatState().busy
  const queryClient = useQueryClient()
  const welcome = useQuery(queries.onboarding()).data
  const titleId = useId()
  const bodyId = useId()

  const [view, setView] = useState<View | null>(null)
  const [spot, setSpot] = useState<Spot | null>(null)
  const [moving, setMoving] = useState(false) // the cut-out glides (a step change)
  const [, setViewport] = useState(0) // the window resized: the anchors move

  const ctx: TourContext = { admin: user.role === "admin" && role === "hr", phone: isMobile }
  // What the async steps below read, kept current.
  const live = useRef({ ctx, chatAt, path: location.pathname, view, answering })
  useLayoutEffect(() => {
    live.current = { ctx, chatAt, path: location.pathname, view, answering }
  })
  const active = useRef(false)
  const waiting = useRef(false) // started by itself, still waiting for the page to be calm
  const seek = useRef(0) // the latest move; an older one stops where it is
  const toward = useRef<{ steps: TourStep[]; index: number; dir: 1 | -1 } | null>(null) // the step it's at, or going to
  const expected = useRef<string | null>(null) // the page the tour is on, or went to
  const startedAt = useRef("/chat") // where it started, to go back to
  const returnFocus = useRef<HTMLElement | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const nextRef = useRef<HTMLButtonElement>(null)

  const save = useCallback(
    (tour: TourId, outcome: TourOutcome) => {
      if (user.impersonator) return // support looking in doesn't put it away for them
      const seen = (tours: Tours | undefined): Tours | undefined =>
        tours && {
          ...tours,
          [tour]: { finished_at: null, skipped_at: null, ...tours[tour], seen: true },
        }
      queryClient.setQueriesData<Onboarding>({ queryKey: ["onboarding"] }, (old) =>
        old ? { ...old, tours: seen(old.tours) } : old,
      )
      onboarding
        .tourSeen(tour, outcome)
        .then((tours) =>
          queryClient.setQueriesData<Onboarding>({ queryKey: ["onboarding"] }, (old) =>
            old ? { ...old, tours } : old,
          ),
        )
        .catch(() => {
          /* kept in this tab; it may start again on another sign-in */
        })
    },
    [queryClient, user.impersonator],
  )

  const end = useCallback(
    (outcome: TourOutcome, goBack = true) => {
      const current = live.current.view
      if (!active.current || !current) return
      active.current = false
      seek.current++
      expected.current = null
      toward.current = null
      setView(null)
      setSpot(null)
      setMoving(false)
      save(current.tour, outcome)
      if (goBack && live.current.path !== startedAt.current.split("?")[0]) navigate(startedAt.current)
    },
    [navigate, save],
  )

  // Show step i of `steps`, going in direction dir: to its page first, if it needs
  // another, then to its element, once it's there. An element that never comes skips
  // the step (it's dropped from the count), or shows it without a light. `here`: look
  // for the first one on the page the app just redirected to, without going anywhere.
  const go = useCallback(
    async (from: TourStep[], first: number, firstDir: 1 | -1, here = false) => {
      const id = ++seek.current
      const alive = () => active.current && seek.current === id
      let steps = from
      let i = first
      let dir = firstDir
      let stay = here
      setView((v) => v && { ...v, seeking: true })
      for (;;) {
        const step = steps[i]
        toward.current = { steps, index: i, dir }
        const page = stay ? null : pageFor(step, live.current.path, live.current.chatAt)
        if (page) {
          expected.current = page.split("?")[0]
          navigate(page)
        }
        const target = step.target
          ? await waitForTarget(step, page || stay ? WAIT_NEW_PAGE : WAIT_SAME_PAGE, alive)
          : null
        stay = false
        if (!alive()) return
        if (target || step.missing !== "skip") {
          if (target) await bringIntoView(target, alive)
          if (!alive()) return
          setMoving(true)
          setView((v) => v && { ...v, steps, index: i, target, seeking: false })
          return
        }
        // Not there: dropped, and on to the next one that way.
        steps = steps.filter((_, k) => k !== i)
        if (dir > 0 && i >= steps.length) {
          end("finished")
          return
        }
        if (dir < 0) i -= 1
        if (i < 0) {
          i = 0
          dir = 1
        }
        const left = steps
        setView((v) => v && { ...v, steps: left })
      }
    },
    [end, navigate],
  )

  const start = useCallback(
    async (tour: TourId, auto = false) => {
      // Running already; but asked for from the menu, it takes over from starting by
      // itself while that still waits (which stops: its move is no longer the latest).
      if (active.current && (auto || !waiting.current)) return
      const all = stepsFor(tour, live.current.ctx)
      if (!all.length) return
      active.current = true
      waiting.current = auto
      const id = ++seek.current
      const alive = () => active.current && seek.current === id
      if (live.current.ctx.phone) setOpenMobile(false)
      // Over nothing: the account menu (or the phone's sheet) it was started from
      // closes first; started by itself, it waits for whatever is open to close and
      // for an answer being written to end (Esc would stop it).
      const calm = await waitForCalm(auto ? 60_000 : 3000, alive, () => auto && live.current.answering)
      if (!alive()) return
      waiting.current = false
      const path = live.current.path
      // By itself, only over nothing and still on the chat; it tries no more this visit.
      if (auto && (!calm || !isChatPath(path))) {
        active.current = false
        return
      }
      // Steps on this page whose element isn't there are left out of the count now.
      const steps = all.filter(
        (s) => !(s.missing === "skip" && s.target && !pageFor(s, path, live.current.chatAt) && !findTarget(s)),
      )
      returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      startedAt.current = `${path}${window.location.search}`
      expected.current = path
      setView({ tour, steps, index: 0, target: null, seeking: true })
      void go(steps, 0, 1)
    },
    [go, setOpenMobile],
  )

  const move = useCallback(
    (dir: 1 | -1) => {
      const v = live.current.view
      if (!v || v.seeking) return
      const path = live.current.path
      const present = (s: TourStep) => !!pageFor(s, path, live.current.chatAt) || !!findTarget(s)
      const j = nextIndex(v.steps, v.index, dir, present)
      if (j === -1) {
        if (dir > 0) end("finished")
        return
      }
      void go(v.steps, j, dir)
    },
    [end, go],
  )

  // The account menu's Take the tour and Admin tour.
  useEffect(() => {
    const onStart = (e: Event) => void start((e as CustomEvent<TourId>).detail)
    window.addEventListener(START_TOUR, onStart)
    return () => window.removeEventListener(START_TOUR, onStart)
  }, [start])

  // By itself, once: a member's first sign-in, on the chat, once the page has settled.
  const autoTried = useRef(false)
  const auto = shouldAutoStart({
    seen: welcome?.tours?.members.seen,
    firstRun: welcome?.first_run,
    path: location.pathname,
    impersonating: !!user.impersonator,
  })
  useEffect(() => {
    if (!auto || autoTried.current) return
    const t = setTimeout(() => {
      autoTried.current = true
      void start("members", true)
    }, SETTLE)
    return () => clearTimeout(t)
  }, [auto, start])

  // Somewhere else in the middle. The person went there (Back or Forward in the
  // browser, a link): it ends there, as skipped. The app redirected (a replace, as a
  // chat that failed to load does): the step it's at, or going to, is looked for on
  // the new page, which is now the one to go back to if it started on the old one.
  useEffect(() => {
    if (!active.current || expected.current === null || location.pathname === expected.current) return
    const at = toward.current
    if (navigationType !== "REPLACE" || !at) {
      end("skipped", false)
      return
    }
    if (startedAt.current.split("?")[0] === expected.current) {
      startedAt.current = `${location.pathname}${window.location.search}`
    }
    expected.current = location.pathname
    void go(at.steps, at.index, at.dir, true)
  }, [location.pathname, navigationType, end, go])

  // The cut-out follows its element: scrolling, resizing, the element changing size.
  const target = view?.target ?? null
  const lit = target ? spot : null // a step without an element: no light
  useLayoutEffect(() => {
    if (!target) return
    let raf = 0
    const measure = () => {
      raf = 0
      const next = target.isConnected ? spotOf(target) : null
      setSpot((prev) => (sameSpot(prev, next) ? prev : next))
    }
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(measure)
    }
    measure()
    const observer = new ResizeObserver(schedule)
    observer.observe(target)
    observer.observe(document.documentElement)
    window.addEventListener("scroll", schedule, { capture: true, passive: true })
    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
      window.removeEventListener("scroll", schedule, { capture: true })
    }
  }, [target])

  useEffect(() => {
    if (!view) return
    const onResize = () => setViewport((n) => n + 1)
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [view])

  // A step change glides the cut-out; then it follows its element at once.
  useEffect(() => {
    if (!moving) return
    const t = setTimeout(() => setMoving(false), 260)
    return () => clearTimeout(t)
  }, [moving, view?.index])

  // The card: hidden while the tour goes to a step, then in, from 0.97 toward the
  // element (a fade alone with reduced motion), a little after the cut-out leaves.
  const index = view?.index
  const seeking = view?.seeking
  useEffect(() => {
    const card = cardRef.current
    if (!card) return
    if (seeking) {
      card.style.opacity = "0"
      return
    }
    const still = quiet()
    const controls = animate(
      card,
      still ? { opacity: [0, 1] } : { opacity: [0, 1], scale: [0.97, 1] },
      { duration: still ? 0.12 : 0.18, delay: still ? 0 : 0.06, ease: ease.out },
    )
    // The focus stays in the card: on Next, or wherever it was, if that's still there.
    if (!card.contains(document.activeElement)) nextRef.current?.focus()
    return () => controls.stop()
  }, [index, seeking])

  if (!view) return null

  const step = view.steps[view.index]
  const last = view.index === view.steps.length - 1
  const offerAdmin = last && view.tour === "members" && ctx.admin
  const vw = window.innerWidth
  const vh = window.innerHeight
  // The card's anchor: the cut-out; the middle of the window when there's none; on a
  // phone, the bottom edge (the top one when the element is low).
  let anchor: DOMRect
  let side: "top" | "right" | "bottom" | "left"
  let offset = GAP
  if (isMobile) {
    const low = !!lit && lit.y + lit.h / 2 > vh / 2
    anchor = low ? rect(0, 0, vw, 0) : rect(0, vh, vw, 0)
    side = low ? "bottom" : "top"
    offset = 16
  } else if (lit) {
    anchor = rect(lit.x, lit.y, lit.w, lit.h)
    side = step?.side ?? "bottom"
  } else {
    anchor = rect(vw / 2, vh * 0.4, 0, 0)
    side = "bottom"
    offset = 0
  }
  const shown = lit ?? { x: vw / 2, y: vh / 2, w: 0, h: 0, r: 0 }
  const glide = moving && !quiet()

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "ArrowRight") {
      e.preventDefault()
      move(1)
    } else if (e.key === "ArrowLeft") {
      e.preventDefault()
      move(-1)
    }
    // The app's own shortcuts (⌘K, ⌘B...) wait until the tour is over (Esc: below).
    if (e.key !== "Tab" && e.key !== "Escape") e.stopPropagation()
  }

  return (
    <>
      {createPortal(
        <div
          data-tour-overlay=""
          data-print="hide"
          aria-hidden
          className="pointer-events-auto fixed inset-0 z-60 animate-in duration-(--dur-dialog) ease-out-strong fade-in-0"
        >
          <div
            className={cn(
              "absolute top-0 left-0",
              glide &&
                "transition-[transform,width,height,border-radius] duration-(--dur-dialog) ease-out-strong",
            )}
            style={{
              transform: `translate(${shown.x}px, ${shown.y}px)`,
              width: shown.w,
              height: shown.h,
              borderRadius: shown.r,
              background: "var(--tour-lift)",
              boxShadow: "inset 0 0 0 1px var(--tour-edge), 0 0 0 200vmax var(--tour-dim)",
            }}
          />
        </div>,
        document.body,
      )}
      <PopoverPrimitive.Root open modal onOpenChange={(open) => !open && end("skipped")}>
        <PopoverPrimitive.Anchor virtualRef={{ current: { getBoundingClientRect: () => anchor } }} />
        <PopoverPrimitive.Portal>
          <PopoverPrimitive.Content
            data-tour-card=""
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={bodyId}
            side={side}
            align="center"
            sideOffset={offset}
            collisionPadding={16}
            onOpenAutoFocus={(e) => {
              e.preventDefault()
              nextRef.current?.focus()
            }}
            onCloseAutoFocus={(e) => {
              e.preventDefault()
              const back = returnFocus.current
              if (back?.isConnected) back.focus({ preventScroll: true })
            }}
            onInteractOutside={(e) => e.preventDefault()}
            // Esc skips the tour and that's all: it goes no further (in the capture
            // phase, before the page sees it), so chat-view's "Esc stops an answer
            // wherever the focus is" doesn't stop one being written.
            onEscapeKeyDown={(e) => {
              e.preventDefault()
              e.stopPropagation()
              end("skipped")
            }}
            onKeyDown={onKeyDown}
            className="z-61 outline-none"
          >
            <div
              ref={cardRef}
              style={{ opacity: 0 }}
              aria-busy={view.seeking || undefined}
              className={cn(
                "flex origin-(--radix-popover-content-transform-origin) flex-col gap-3 rounded-2xl glass-strong p-4 text-sm text-popover-foreground",
                isMobile ? "w-[calc(100vw-2rem)]" : "w-80",
                view.seeking && "pointer-events-none",
              )}
            >
              {step && (
                <>
                  <div className="flex items-baseline justify-between gap-3">
                    <h2 id={titleId} className="font-semibold tracking-tight text-balance">
                      {step.title}
                    </h2>
                    <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                      {view.index + 1} of {view.steps.length}
                    </span>
                  </div>
                  <p id={bodyId} className="text-pretty text-muted-foreground">
                    {bodyOf(step, ctx)}
                  </p>
                  {/* Next keeps the focus: the new step is read out from here. */}
                  <p className="sr-only" aria-live="polite">
                    {view.seeking ? "" : `${step.title}. ${bodyOf(step, ctx)}`}
                  </p>
                  <div className="flex items-center gap-2 pt-1">
                    {!last && (
                      <Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground" onClick={() => end("skipped")}>
                        Skip
                      </Button>
                    )}
                    <div className="ml-auto flex items-center gap-2">
                      {view.index > 0 && (
                        <Button variant="outline" size="sm" onClick={() => move(-1)}>
                          Back
                        </Button>
                      )}
                      {offerAdmin && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            end("finished")
                            void start("admin")
                          }}
                        >
                          Admin tour
                        </Button>
                      )}
                      <Button ref={nextRef} size="sm" onClick={() => move(1)}>
                        {last ? "Done" : "Next"}
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </div>
          </PopoverPrimitive.Content>
        </PopoverPrimitive.Portal>
      </PopoverPrimitive.Root>
    </>
  )
}
