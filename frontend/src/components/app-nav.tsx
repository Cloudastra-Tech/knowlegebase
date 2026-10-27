import { Check, ChevronDown } from "lucide-react"
import { Link } from "react-router"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
      className={cn(
        "relative h-full shrink-0 items-center whitespace-nowrap text-sm transition-colors",
        open ? "text-foreground" : "text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      {item.label}
      {open && <span className="absolute inset-x-0 bottom-0 h-px bg-foreground" />}
    </Link>
  )
}

function MenuItems({ items, current, classOf }: {
  items: NavItem[]
  current: string | undefined
  classOf?: (i: number) => string
}) {
  return items.map((item, i) => (
    <DropdownMenuItem key={item.to} asChild className={classOf?.(i)}>
      <Link to={item.to} aria-current={item.section === current ? "page" : undefined}>
        <span className="flex-1">{item.label}</span>
        {item.section === current && <Check className="text-muted-foreground" />}
      </Link>
    </DropdownMenuItem>
  ))
}

export function AppNav({ items, current }: { items: NavItem[]; current: string | undefined }) {
  const open = (item: NavItem) => item.section === current
  // Two screens (a member's) fit anywhere: plain tabs.
  if (items.length <= 2) {
    return (
      <nav aria-label="Screens" className="flex h-full min-w-0 items-center gap-5 sm:gap-6">
        {items.map((item) => (
          <TabLink key={item.to} item={item} open={open(item)} className="flex" />
        ))}
      </nav>
    )
  }
  const here = items.findIndex(open)
  const overflow = items.slice(2)
  // More is needed up to lg with five screens or fewer, up to xl with more.
  const moreUntil = items.length <= 5 ? "lg:hidden" : "xl:hidden"
  return (
    <nav aria-label="Screens" className="flex h-full min-w-0 items-center gap-5 sm:gap-6">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="-ml-2 gap-1 px-2 font-normal sm:hidden">
            {here >= 0 ? items[here].label : "Menu"}
            <span className="sr-only">: switch screen</span>
            <ChevronDown className="size-3.5 opacity-60" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48">
          <MenuItems items={items} current={current} />
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
            className={cn(
              "relative hidden h-full shrink-0 items-center gap-1 text-sm whitespace-nowrap outline-none transition-colors sm:flex focus-visible:text-foreground",
              moreUntil,
              "text-muted-foreground hover:text-foreground",
              here >= 2 && MORE_OPEN[step(here)],
            )}
          >
            More
            <ChevronDown className="size-3.5 opacity-60" />
            {here >= 2 && (
              <span
                className={cn(
                  "absolute inset-x-0 bottom-0 h-px bg-foreground",
                  IN_MORE[step(here)],
                )}
              />
            )}
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
    </nav>
  )
}
