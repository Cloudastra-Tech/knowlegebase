import { useEffect, useRef, useState, useSyncExternalStore } from "react"

import { getEffects, reducedMotion } from "@/lib/ascii-loop"

// What the landing's scenes (landing-visuals.tsx, landing-route.tsx, ...) share.

// Whether the page draws still frames (reduced motion, or "Reduce effects").
export const still = () => reducedMotion() || getEffects() === "reduced"

// How much of a demo is on screen before it plays (the loop's `visibleAt`).
export const SEEN = 0.3

// Writes text and on/off attributes only when they change, so a frame where nothing
// changed touches nothing.
export function writer() {
  const shown = new WeakMap<Node, string>()
  return {
    text(node: HTMLElement | null | undefined, text: string) {
      if (!node || shown.get(node) === text) return
      shown.set(node, text)
      node.textContent = text
    },
    on(node: HTMLElement | null | undefined, on: boolean) {
      if (!node || (node.dataset.on === "") === on) return
      if (on) node.dataset.on = ""
      else delete node.dataset.on
    },
    state(node: HTMLElement | null | undefined, state: string) {
      if (node && node.dataset.state !== state) node.dataset.state = state
    },
  }
}

// ---- Sections told in steps (landing-steps.tsx) ------------------------------------

// From lg up the steps scroll past a sticky visual; below it, each has its own.
const WIDE = "(min-width: 1024px)"
const onWideChange = (onChange: () => void) => {
  const mql = window.matchMedia(WIDE)
  mql.addEventListener("change", onChange)
  return () => mql.removeEventListener("change", onChange)
}
export function useWide() {
  return useSyncExternalStore(onWideChange, () => window.matchMedia(WIDE).matches, () => true)
}

// The step crossing the middle tenth of the window is the active one. An observer
// reports only steps entering that band (no reading the layout on scroll), so a step
// stays active until the next one gets there: past the last, it's the last.
export const BAND = "-45% 0px -45% 0px"

export function useActiveStep(enabled: boolean) {
  const [active, setActive] = useState(0)
  const nodes = useRef<(HTMLElement | null)[]>([])
  useEffect(() => {
    if (!enabled) return
    const io = new IntersectionObserver(
      (entries) => {
        // Scrolled fast, two can enter at once: the one nearer the middle wins.
        let best = -1
        let gap = Infinity
        for (const e of entries) {
          if (!e.isIntersecting) continue
          const i = nodes.current.indexOf(e.target as HTMLElement)
          const mid = e.rootBounds ? e.rootBounds.top + e.rootBounds.height / 2 : 0
          const d = Math.abs(e.boundingClientRect.top + e.boundingClientRect.height / 2 - mid)
          if (i >= 0 && d < gap) [best, gap] = [i, d]
        }
        if (best >= 0) setActive(best)
      },
      { rootMargin: BAND },
    )
    for (const n of nodes.current) if (n) io.observe(n)
    return () => io.disconnect()
  }, [enabled])
  const ref = (i: number) => (node: HTMLElement | null) => {
    nodes.current[i] = node
  }
  return { active, ref }
}

// The visual's states trade places on opacity, with a 2px blur on the way out and in
// so the two never read as one: 240ms, ease-out-strong, as transitions, so a fast
// scroll retargets rather than queues. Reduced motion or "Reduce effects": no blur, a
// 150ms fade.
export const SWAP =
  "transition-[opacity,filter] duration-(--dur-reveal) ease-out-strong motion-reduce:duration-(--dur-swap) [:root[data-effects=reduced]_&]:duration-(--dur-swap)"
export const SWAP_OFF = "opacity-0 blur-[2px] motion-reduce:blur-none [:root[data-effects=reduced]_&]:blur-none"
