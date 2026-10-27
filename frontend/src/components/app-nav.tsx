import { useLayoutEffect, useRef } from "react"
import { Check, ChevronDown } from "lucide-react"
import { Link } from "react-router"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { isInstant } from "@/lib/instant-nav"
import { cn } from "@/lib/utils"

// `section`: every URL under it is this screen (Access has a tab per URL).
export interface NavItem {
  to: string
  section: string
  label: string
}

// The app's screens in the header, without ever widening the page:
//   phones         one menu, labelled with the screen you're on (when there are more than two)
//   sm             Chat and Knowledge, the rest under More
//   lg             the first five, the rest under More
//   xl             every screen
// Tailwind needs whole class names, so each step's are spelled out.
const SHOWN = ["sm:flex", "lg:flex", "xl:flex"] as const
const IN_MORE = ["", "lg:hidden", "xl:hidden"] as const
// More reads as the open screen while that screen is under it.
const MORE_OPEN = ["", "text-foreground lg:text-muted-foreground", "text-foreground"] as const
const step = (i: number) => (i < 2 ? 0 : i < 5 ? 1 : 2)

function TabLink({ item, open, className }: { item: NavItem; open: boolean; className?: string }) {
  return (
    <Link
      to={item.to}
      aria-current={open ? "page" : undefined}
      data-nav-current={open || undefined}
      className={cn(
        "relative h-full shrink-0 items-center whitespace-nowrap text-sm transition-colors duration-(--dur-hover) ease-hover",
        open ? "text-foreground" : "text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      {item.label}
    </Link>
  )
}

function MenuItems({ items, current, classOf, paths = false }: {
  items: NavItem[]
  current: string | undefined
  classOf?: (i: number) => string
  paths?: boolean
}) {
  return items.map((item, i) => (
    <DropdownMenuItem key={item.to} asChild className={classOf?.(i)}>
      <Link to={item.to} aria-current={item.section === current ? "page" : undefined}>
        <span className="flex-1">{item.label}</span>
        {/* The phone menu's route hint, mono like the screens' eyebrows (~/admin/insights). */}
        {paths && (
          <span aria-hidden className="font-mono text-xs text-muted-foreground">
            ~{item.to}
          </span>
        )}
        {item.section === current && <Check className="text-muted-foreground" />}
      </Link>
    </DropdownMenuItem>
  ))
}

// One signal-mint line under the open screen, sliding from tab to tab (a transform
// only: translateX to the tab, scaleX to its width, 220ms). It jumps instead when the
// change came from the keyboard (focus is visible in the nav, or the palette or a
// shortcut navigated: lib/instant-nav.ts), with reduced motion, and when it first
// appears or the header resizes.
// Under More when the open screen is in that menu at this width; none on a phone.
function useIndicator(key: string) {
  const navRef = useRef<HTMLElement>(null)
  const barRef = useRef<HTMLSpanElement>(null)
  const placed = useRef("") // where it last went, "" while hidden
  useLayoutEffect(() => {
    const el = navRef.current
    const line = barRef.current
    if (!el || !line) return
    const place = (slide: boolean) => {
      // Tabs come before More in the nav: the open tab wins wherever it's showing.
      const target = [...el.querySelectorAll<HTMLElement>("[data-nav-current]")].find(
        (t) => t.getClientRects().length > 0,
      )
      if (!target) {
        line.style.opacity = "0"
        placed.current = ""
        return
      }
      const transform = `translateX(${target.offsetLeft}px) scaleX(${target.offsetWidth})`
      if (transform === placed.current) return // a resize that didn't move it
      const jump = !slide || !placed.current || isInstant()
      if (jump) line.style.transition = "none"
      line.style.transform = transform
      line.style.opacity = "1"
      if (jump) {
        void line.offsetWidth // commit the jump before the transition comes back
        line.style.transition = ""
      }
      placed.current = transform
    }
    place(true)
    // A resize (the window, a breakpoint, the web font arriving) moves it without a slide.
    const ro = new ResizeObserver(() => place(false))
    ro.observe(el)
    for (const t of el.querySelectorAll("a, button")) ro.observe(t)
    return () => ro.disconnect()
  }, [key])
  return { navRef, barRef }
}

export function AppNav({ items, current }: { items: NavItem[]; current: string | undefined }) {
  const { navRef, barRef } = useIndicator(`${current}|${items.length}`)
  const open = (item: NavItem) => item.section === current
  const indicator = (
    <span
      ref={barRef}
      aria-hidden
      data-slot="nav-indicator"
      className="pointer-events-none absolute bottom-0 left-0 h-0.5 w-px origin-left bg-signal opacity-0 transition-transform duration-[220ms] ease-in-out-strong group-has-[:focus-visible]/nav:transition-none motion-reduce:transition-none"
    />
  )
  // Two screens (a member's) fit anywhere: plain tabs.
  if (items.length <= 2) {
    return (
      <nav ref={navRef} aria-label="Screens" className="group/nav relative flex h-full min-w-0 items-center gap-5 sm:gap-6">
        {items.map((item) => (
          <TabLink key={item.to} item={item} open={open(item)} className="flex" />
        ))}
        {indicator}
      </nav>
    )
  }
  const here = items.findIndex(open)
  const hereLabel = here >= 0 ? items[here].label : "Menu"
  const overflow = items.slice(2)
  // More is needed up to lg with five screens or fewer, up to xl with more.
  const moreUntil = items.length <= 5 ? "lg:hidden" : "xl:hidden"
  return (
    <nav ref={navRef} aria-label="Screens" className="group/nav relative flex h-full min-w-0 items-center gap-5 sm:gap-6">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`${hereLabel}, switch screen`}
            className="-ml-2 gap-1 px-2 font-normal sm:hidden"
          >
            {hereLabel}
            <ChevronDown className="size-3.5 opacity-60" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <MenuItems items={items} current={current} paths />
        </DropdownMenuContent>
      </DropdownMenu>

      {items.map((item, i) => (
        <TabLink
          key={item.to}
          item={item}
          open={open(item)}
          className={cn("hidden", SHOWN[step(i)])}
        />
      ))}

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            data-nav-current={here >= 2 || undefined}
            className={cn(
              "relative hidden h-full shrink-0 items-center gap-1 text-sm whitespace-nowrap outline-none transition-colors duration-(--dur-hover) ease-hover sm:flex focus-visible:text-foreground",
              moreUntil,
              "text-muted-foreground hover:text-foreground",
              here >= 2 && MORE_OPEN[step(here)],
            )}
          >
            More
            <ChevronDown className="size-3.5 opacity-60" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48">
          <MenuItems
            items={overflow}
            current={current}
            classOf={(i) => IN_MORE[step(i + 2)]}
          />
        </DropdownMenuContent>
      </DropdownMenu>
      {indicator}
    </nav>
  )
}
