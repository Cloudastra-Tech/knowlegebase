import { useEffect, useRef, useState, type RefObject } from "react"
import { useLocation } from "react-router"

// The strip at the top of a screen once its title has scrolled away: frosted glass
// (the chrome tier, as the phone's top bar) with the screen's path and title, over the
// content scrolling under it. It fades in when the ScreenHeader's h2 goes under it and
// out when it comes back, lined up with the title. Not on a phone, whose top bar
// already says where you are. aria-hidden: the h2 is the screen's title.
export function ScreenStrip({ scroller }: { scroller: RefObject<HTMLElement | null> }) {
  const { pathname } = useLocation()
  const strip = useRef<HTMLDivElement>(null)
  const [title, setTitle] = useState<string | null>(null)
  const [inset, setInset] = useState(0)

  useEffect(() => {
    const box = scroller.current
    if (!box || typeof IntersectionObserver === "undefined") return
    let io: IntersectionObserver | null = null
    let watched: Element | null = null
    const height = strip.current?.offsetHeight ?? 44
    const watch = () => {
      const h2 = box.querySelector('[data-slot="screen-header"] h2')
      if (!h2 || h2 === watched) return
      io?.disconnect()
      watched = h2
      io = new IntersectionObserver(
        ([e]) => {
          const gone = !e.isIntersecting && e.boundingClientRect.top < (e.rootBounds?.top ?? 0) + height
          setTitle(gone ? (h2.textContent ?? "") : null)
          if (gone) setInset(Math.max(0, h2.getBoundingClientRect().left - box.getBoundingClientRect().left))
        },
        { root: box, rootMargin: `-${height}px 0px 0px 0px` },
      )
      io.observe(h2)
    }
    watch()
    // The screen's code and data arrive after it mounts: watch for its header.
    const mo = new MutationObserver(watch)
    mo.observe(box, { childList: true, subtree: true })
    return () => {
      mo.disconnect()
      io?.disconnect()
    }
  }, [scroller])

  return (
    <div
      ref={strip}
      aria-hidden
      data-slot="screen-strip"
      data-print="hide"
      data-shown={title !== null || undefined}
      className="glass invisible sticky top-0 z-10 -mb-11 flex h-11 items-center gap-3 rounded-none border-x-0 border-t-0 opacity-0 shadow-none transition-[opacity,visibility] duration-(--dur-swap) ease-out-strong data-shown:visible data-shown:opacity-100 max-md:hidden"
      style={{ paddingLeft: inset || undefined, paddingRight: 16 }}
    >
      <span className="shrink-0 font-mono text-xs text-muted-foreground">~{pathname.replace(/\/+$/, "").toLowerCase()}</span>
      <span className="min-w-0 truncate text-sm font-medium">{title}</span>
    </div>
  )
}
