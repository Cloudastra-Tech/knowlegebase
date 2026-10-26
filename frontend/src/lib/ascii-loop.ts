// The animation loop the landing page's moving pieces share: drawn about 30
// times a second while on screen and the tab is in front; one still frame with
// reduced motion.

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches

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

// Draws `draw(seconds)` while `el` is visible and the tab is in front. The clock
// only runs while it's drawing, so a scene starts from the top when it's first seen.
export function runLoop(el: HTMLElement, draw: (t: number) => void, still: number) {
  if (reducedMotion()) {
    draw(still)
    return { redraw: () => draw(still), stop: () => {} }
  }
  let raf = 0
  let visible = false
  let last = -1
  let elapsed = 0
  const frame = (now: number) => {
    raf = 0
    if (!visible || document.hidden) {
      last = -1
      return
    }
    if (last < 0) last = now - 33
    if (now - last >= 33) {
      elapsed += Math.min(now - last, 100) / 1000
      last = now
      draw(elapsed)
    }
    raf = requestAnimationFrame(frame)
  }
  const wake = () => {
    if (visible && !document.hidden && !raf) raf = requestAnimationFrame(frame)
  }
  const io = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting
    wake()
  })
  io.observe(el)
  document.addEventListener("visibilitychange", wake)
  return {
    redraw: () => draw(elapsed),
    stop: () => {
      cancelAnimationFrame(raf)
      io.disconnect()
      document.removeEventListener("visibilitychange", wake)
    },
  }
}
