import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Tabs as TabsPrimitive } from "radix-ui"

// panels={false}: tabs used as a switch (a filter, or a URL per tab) with no
// TabsContent. Their triggers then control no panel, so they don't name one
// (aria-controls pointing at nothing is invalid ARIA).
const PanelsContext = React.createContext(true)

function Tabs({
  className,
  orientation = "horizontal",
  panels = true,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root> & { panels?: boolean }) {
  return (
    <PanelsContext.Provider value={panels}>
      <TabsPrimitive.Root
        data-slot="tabs"
        data-orientation={orientation}
        className={cn(
          "group/tabs flex gap-2 data-horizontal:flex-col",
          className
        )}
        {...props}
      />
    </PanelsContext.Provider>
  )
}

const tabsListVariants = cva(
  "group/tabs-list inline-flex w-fit items-center justify-center rounded-lg p-[3px] text-muted-foreground group-data-horizontal/tabs:h-8 group-data-vertical/tabs:h-fit group-data-vertical/tabs:flex-col data-[variant=line]:rounded-none",
  {
    variants: {
      variant: {
        default: "bg-muted",
        line: "gap-1 bg-transparent",
        // A translucent rail; one thumb slides under the active tab (useThumb).
        glass: "relative glass-subtle",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function TabsList({
  className,
  variant = "default",
  children,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List> &
  VariantProps<typeof tabsListVariants>) {
  const ref = React.useRef<HTMLDivElement>(null)
  useThumb(ref, variant === "glass")
  return (
    <TabsPrimitive.List
      ref={ref}
      data-slot="tabs-list"
      data-variant={variant}
      className={cn(tabsListVariants({ variant }), className)}
      {...props}
    >
      {variant === "glass" && (
        <span
          aria-hidden
          data-slot="tabs-thumb"
          className="pointer-events-none absolute top-0 left-0 h-(--thumb-h) w-(--thumb-w) translate-x-(--thumb-x) translate-y-(--thumb-y) rounded-md bg-background shadow-sm group-data-[thumb=ready]/tabs-list:transition-[translate,width,height] group-data-[thumb=ready]/tabs-list:duration-220 group-data-[thumb=ready]/tabs-list:ease-in-out-strong group-has-focus-visible/tabs-list:transition-none motion-reduce:transition-none dark:bg-foreground/10"
        />
      )}
      {children}
    </TabsPrimitive.List>
  )
}

// The glass rail's thumb: it measures the active trigger and moves under it by
// transform, so nothing else lays out. It first appears in place, without sliding, and
// jumps when the tabs are moved by keyboard (focus visible).
function useThumb(ref: React.RefObject<HTMLDivElement | null>, on: boolean) {
  React.useLayoutEffect(() => {
    const list = ref.current
    if (!on || !list) return
    const place = () => {
      const active = list.querySelector<HTMLElement>(
        '[data-slot="tabs-trigger"][data-state="active"]'
      )
      if (!active) return
      list.style.setProperty("--thumb-x", `${active.offsetLeft}px`)
      list.style.setProperty("--thumb-y", `${active.offsetTop}px`)
      list.style.setProperty("--thumb-w", `${active.offsetWidth}px`)
      list.style.setProperty("--thumb-h", `${active.offsetHeight}px`)
    }
    place()
    const ready = requestAnimationFrame(() => (list.dataset.thumb = "ready"))
    const moved = new MutationObserver(place)
    moved.observe(list, { subtree: true, attributeFilter: ["data-state"] })
    const resized =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place)
    resized?.observe(list)
    return () => {
      cancelAnimationFrame(ready)
      moved.disconnect()
      resized?.disconnect()
    }
  }, [ref, on])
}

function TabsTrigger({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  const panels = React.useContext(PanelsContext)
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      {...(panels ? {} : { "aria-controls": undefined })}
      className={cn(
        "relative inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 rounded-md border border-transparent px-1.5 py-0.5 text-sm font-medium whitespace-nowrap text-foreground/60 transition-[color,background-color,border-color,box-shadow] duration-(--dur-hover) ease-hover group-data-vertical/tabs:w-full focus-visible:transition-none group-data-vertical/tabs:justify-start hover:text-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-1 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50 has-data-[icon=inline-end]:pr-1 has-data-[icon=inline-start]:pl-1 dark:text-muted-foreground dark:hover:text-foreground group-data-[variant=default]/tabs-list:data-active:shadow-sm group-data-[variant=line]/tabs-list:data-active:shadow-none [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        "group-data-[variant=glass]/tabs-list:data-active:border-transparent group-data-[variant=glass]/tabs-list:data-active:bg-transparent group-data-[variant=glass]/tabs-list:data-active:shadow-none dark:group-data-[variant=glass]/tabs-list:data-active:border-transparent dark:group-data-[variant=glass]/tabs-list:data-active:bg-transparent",
        "group-data-[variant=line]/tabs-list:bg-transparentgroup-data-[variant=line]/tabs-list:data-active:bg-transparent dark:group-data-[variant=line]/tabs-list:data-active:border-transparent dark:group-data-[variant=line]/tabs-list:data-active:bg-transparent",
        "data-active:bg-background data-active:text-foreground dark:data-active:border-input dark:data-active:bg-input/30 dark:data-active:text-foreground",
        "after:absolute after:bg-foreground after:opacity-0 after:transition-opacity group-data-horizontal/tabs:after:inset-x-0 group-data-horizontal/tabs:after:bottom-[-5px] group-data-horizontal/tabs:after:h-0.5 group-data-vertical/tabs:after:inset-y-0 group-data-vertical/tabs:after:-right-1 group-data-vertical/tabs:after:w-0.5 group-data-[variant=line]/tabs-list:data-active:after:opacity-100",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      className={cn("flex-1 text-sm outline-none", className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent, tabsListVariants }
