// The motion tokens (index.css, "Motion") for Motion (motion/react), so a component
// animated in JS keeps the same curves and timings as the CSS ones. DESIGN.md,
// "Motion", says which to use where.

import { useCallback, useEffect, useState, useSyncExternalStore, type CSSProperties } from "react"

// Curves, as cubic-bezier points.
export const ease = {
  out: [0.23, 1, 0.32, 1], // enter, exit, feedback (--ease-out-strong)
  inOut: [0.77, 0, 0.175, 1], // movement on screen (--ease-in-out-strong)
  drawer: [0.32, 0.72, 0, 1], // sheets, the sidebar (--ease-drawer)
  hover: [0.25, 0.1, 0.25, 1], // colour and background (--ease-hover)
} as const

// Durations, in seconds (the --dur-* properties are in ms).
export const dur = {
  instant: 0,
  press: 0.12,
  hover: 0.15,
  tooltip: 0.14,
  popover: 0.18,
  popoverExit: 0.12,
  dialog: 0.22,
  dialogExit: 0.15,
  sheet: 0.28,
  sheetExit: 0.2,
  screen: 0.2,
  swap: 0.15,
  reveal: 0.24,
  // the chat (chat-and-tools plan, 3)
  jump: 0.15, // "Jump to latest" in
  jumpExit: 0.1, // and out
  step: 0.18, // a step line enters
  collapse: 0.25, // the steps fold to one line
  pop: 0.16, // a citation chip resolves
  driftExit: 0.4, // the empty chat's art fades as the first question goes
} as const

// For anything a person can interrupt. No bounce in the app (0.15 is for marketing).
export const spring = { type: "spring", duration: 0.35, bounce: 0 } as const

// Lists on first mount: 40ms apart, the rest arriving with the sixth. An answer's
// sources: 30ms apart, the first four.
export const stagger = { each: 0.04, max: 6, sources: 0.03, sourcesMax: 4 } as const

// A list or grid's first draw: each item fades up `stagger.each` apart, the ones past
// the sixth with the sixth, once. `ready` is when the items are there (the data has
// loaded). Call the result with an item's index and spread it on the item (merge the
// className with cn()). Once the first draw has played, it gives nothing, so a refetch,
// a filter or a new item doesn't animate; taking the classes off a finished animation
// changes nothing on screen. CSS only (tw-animate), motion-safe: nothing moves with
// reduced motion, and :root[data-instant] skips it. Never for tables.
export type StaggerItem = { className?: string; style?: CSSProperties }
const STAGGER_ENTER =
  "motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-1.5 motion-safe:fill-mode-backwards motion-safe:duration-(--dur-reveal) motion-safe:ease-out-strong"
export function useStaggerOnce(ready = true): (index: number) => StaggerItem {
  const [played, setPlayed] = useState(false)
  useEffect(() => {
    if (!ready || played) return
    // The last item's delay plus the reveal, and a frame to spare.
    const t = setTimeout(() => setPlayed(true), (stagger.max - 1) * stagger.each * 1000 + dur.reveal * 1000 + 50)
    return () => clearTimeout(t)
  }, [ready, played])
  return useCallback(
    (index: number) =>
      played || !ready
        ? {}
        : {
            className: STAGGER_ENTER,
            style: { animationDelay: `${Math.round(Math.min(index, stagger.max - 1) * stagger.each * 1000)}ms` },
          },
    [played, ready],
  )
}

// Keyboard-driven changes are instant: call before navigate() from a shortcut or the
// command palette; index.css turns transitions and animations off until two frames on.
export function markInstant() {
  const root = document.documentElement
  root.dataset.instant = ""
  requestAnimationFrame(() => requestAnimationFrame(() => delete root.dataset.instant))
}

// prefers-reduced-motion, following the setting live: for what JS decides (a scroll's
// `behavior`), where CSS has motion-safe: and motion-reduce:.
const REDUCED = "(prefers-reduced-motion: reduce)"
function onReducedChange(onChange: () => void) {
  const mql = window.matchMedia(REDUCED)
  mql.addEventListener("change", onChange)
  return () => mql.removeEventListener("change", onChange)
}
export function usePrefersReducedMotion() {
  return useSyncExternalStore(
    onReducedChange,
    () => window.matchMedia(REDUCED).matches,
    () => false,
  )
}
