import { markInstant } from "@/lib/motion"

// A screen change from the keyboard (the command palette, ⌘⇧O) lands without motion:
// no screen entrance (screens.tsx, Screen).
// markInstant() alone holds for two frames, and a router navigation commits later than
// that (it matches the route, then renders in a transition). So this also marks <html>
// with data-instant-nav until the new screen has been painted (App ends it once the
// location changes) or, if nothing changes, for a second.
let timer: ReturnType<typeof setTimeout> | undefined

export function markInstantNav() {
  markInstant()
  document.documentElement.dataset.instantNav = ""
  clearTimeout(timer)
  timer = setTimeout(endInstantNav, 1000)
}

export function endInstantNav() {
  clearTimeout(timer)
  delete document.documentElement.dataset.instantNav
}

// Is a keyboard change landing right now? Asked while rendering the new screen.
export function isInstant() {
  const d = document.documentElement.dataset
  return "instant" in d || "instantNav" in d
}
