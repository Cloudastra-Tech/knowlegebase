import { useRef } from "react"

import { getEffects, reducedMotion } from "@/lib/ascii-loop"

// What the landing's scenes (landing-visuals.tsx, landing-route.tsx, ...) share.

// Whether the page draws still frames (reduced motion, or "Reduce effects").
export const still = () => reducedMotion() || getEffects() === "reduced"

// How much of a demo is on screen before it plays (the loop's `visibleAt`).
export const SEEN = 0.3

// How the person last picked a tab: from the keyboard, the change is instant (DESIGN.md:
// nothing the keyboard does animates). Spread `listeners` on the TabsList.
export function usePickedBy() {
  const by = useRef<"pointer" | "keyboard">("pointer")
  return {
    by,
    listeners: {
      onPointerDown: () => {
        by.current = "pointer"
      },
      onKeyDown: () => {
        by.current = "keyboard"
      },
    },
  }
}

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
