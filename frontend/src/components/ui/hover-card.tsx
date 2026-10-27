import * as React from "react"
import { cn } from "@/lib/utils"
import { HoverCard as HoverCardPrimitive } from "radix-ui"

// A preview is for a pointer resting on its trigger, so it stands down in two cases:
// - the trigger was pressed: the person wants what it opens (the passage, the page). It
//   stays closed until the pointer leaves the trigger.
// - the trigger is behind a modal (a dialog or sheet hides the rest of the page with
//   aria-hidden), as when the click just opened the source sheet.
// Otherwise the open delay can run out over the sheet the click opened, and the card
// pops up on top of it and takes the sheet's Escape.
type CardContextValue = {
  trigger: React.RefObject<HTMLElement | null>
  press: () => void
  release: () => void
}
const CardContext = React.createContext<CardContextValue | null>(null)

function HoverCard({
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Root>) {
  const [own, setOwn] = React.useState(defaultOpen)
  const trigger = React.useRef<HTMLElement | null>(null)
  const pressed = React.useRef(false)
  const change = React.useCallback(
    (next: boolean) => {
      const hidden = trigger.current?.closest('[aria-hidden="true"]')
      if (next && (pressed.current || hidden)) return
      setOwn(next)
      onOpenChange?.(next)
    },
    [onOpenChange]
  )
  const card = React.useMemo(
    () => ({
      trigger,
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
    <CardContext.Provider value={card}>
      <HoverCardPrimitive.Root
        data-slot="hover-card"
        open={openProp ?? own}
        onOpenChange={change}
        {...props}
      />
    </CardContext.Provider>
  )
}

function HoverCardTrigger({
  ref,
  onPointerDown,
  onPointerLeave,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Trigger>) {
  const card = React.useContext(CardContext)
  const setRef = React.useCallback(
    (el: HTMLAnchorElement | null) => {
      if (card) card.trigger.current = el
      if (typeof ref === "function") ref(el)
      else if (ref) ref.current = el
    },
    [card, ref]
  )
  return (
    <HoverCardPrimitive.Trigger
      ref={setRef}
      data-slot="hover-card-trigger"
      onPointerDown={(event) => {
        onPointerDown?.(event)
        card?.press()
      }}
      onPointerLeave={(event) => {
        onPointerLeave?.(event)
        card?.release()
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
