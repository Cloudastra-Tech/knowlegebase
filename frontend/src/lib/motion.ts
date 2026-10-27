// The motion tokens (index.css, "Motion") for Motion (motion/react), so a component
// animated in JS keeps the same curves and timings as the CSS ones. DESIGN.md,
// "Motion", says which to use where.

import { useSyncExternalStore } from "react"

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
} as const

// For anything a person can interrupt. No bounce in the app (0.15 is for marketing).
export const spring = { type: "spring", duration: 0.35, bounce: 0 } as const

// Lists on first mount: 40ms apart, the rest arriving with the sixth. An answer's
// sources: 30ms apart, the first four.
export const stagger = { each: 0.04, max: 6, sources: 0.03, sourcesMax: 4 } as const

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
