import * as React from "react"
import { cn } from "cn"
import { HoverCard as HoverCardPrimitive } from "radix-ui"

// Pressing the trigger means the person wants what it opens (the passage, the page), not
// its preview: the card closes, and doesn't open until the pointer has left the trigger.
// Otherwise the open delay can run out after a click has opened a sheet, and the card
// pops up over it and takes the sheet's Escape.
const PressContext = React.createContext<{ press: () => void; release: () => void } | null>(null)

function HoverCard({
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Root>) {
  const [own, setOwn] = React.useState(defaultOpen)
  const pressed = React.useRef(false)
  const change = React.useCallback(
    (next: boolean) => {
      if (next && pressed.current) return
      setOwn(next)
      onOpenChange?.(next)
    },
    [onOpenChange]
  )
  const press = React.useMemo(
    () => ({
      press: () => {
        pressed.current = true
        change(false)
      },
      release: () => {
        pressed.current = false
      },
    }),
    [change]
  )
  return (
    <PressContext.Provider value={press}>
      <HoverCardPrimitive.Root
        data-slot="hover-card"
        open={openProp ?? own}
        onOpenChange={change}
        {...props}
      />
    </PressContext.Provider>
  )
}

function HoverCardTrigger({
  onPointerDown,
  onPointerLeave,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Trigger>) {
  const press = React.useContext(PressContext)
  return (
    <HoverCardPrimitive.Trigger
      data-slot="hover-card-trigger"
      onPointerDown={(event) => {
        onPointerDown?.(event)
        press?.press()
      }}
      onPointerLeave={(event) => {
        onPointerLeave?.(event)
        press?.release()
      }}
      {...props}
    />
  )
}

function HoverCardContent({
  className,
  align = "center",
  sideOffset = 4,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Content>) {
  return (
    <HoverCardPrimitive.Portal data-slot="hover-card-portal">
      <HoverCardPrimitive.Content
        data-slot="hover-card-content"
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 w-64 origin-(--radix-hover-card-content-transform-origin) rounded-lg glass-strong p-2.5 text-sm text-popover-foreground outline-hidden duration-(--dur-popover) ease-out-strong data-[side=bottom]:slide-in-from-top-1 data-[side=left]:slide-in-from-right-1 data-[side=right]:slide-in-from-left-1 data-[side=top]:slide-in-from-bottom-1 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-97 data-closed:animate-out data-closed:duration-(--dur-popover-exit) data-closed:fade-out-0 data-closed:zoom-out-97",
          className,
        )}
        {...props}
      />
    </HoverCardPrimitive.Portal>
  )
}

export { HoverCard, HoverCardTrigger, HoverCardContent }
