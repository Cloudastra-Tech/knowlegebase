import { useEffect, useState } from "react"

import "@/components/chat-motion.css"
import { AsciiField } from "@/components/ascii/ascii-field"
import { THINKING_EVENT, type ThinkingDetail } from "@/components/ascii/events"
import { RedactedDrift } from "@/components/ascii/redacted-drift"
import { cn } from "@/lib/utils"

// The moving ASCII field behind the chat: the app's ambient field (12 fps, resting
// while the page scrolls and under a dialog, one still frame with reduced motion or
// "Reduce effects"), faded to about a fifth under the reading column so it never sits
// under text at strength. While the assistant works (kb:thinking, lib/chat-events.ts)
// it breathes, a mint glow rises from the composer, and each step sends a ripple out
// from there. Decoration only: aria-hidden, never printed.
//
// One field at a time. On the empty new chat (`empty`) it's the "Redacted drift" art
// instead. When the first question is sent (`leaving`) the art fades out over 400ms
// while the field fades in; any other way out of the empty state (opening a saved
// chat) swaps them at once, so nothing animates.
export function ChatBackdrop({ empty = false, leaving = false }: { empty?: boolean; leaving?: boolean }) {
  const [thinking, setThinking] = useState(false)
  useEffect(() => {
    const on = (e: Event) => setThinking((e as CustomEvent<ThinkingDetail>).detail?.active === true)
    window.addEventListener(THINKING_EVENT, on)
    return () => window.removeEventListener(THINKING_EVENT, on)
  }, [])
  return (
    <div
      aria-hidden
      data-print="hide"
      data-slot="chat-backdrop"
      data-thinking={thinking || undefined}
      className="kb-chat-backdrop pointer-events-none absolute inset-0 -z-10 overflow-hidden"
    >
      <div className="kb-chat-glow absolute inset-0" />
      {!empty && (
        // Its own box for the fade in, as the field breathes on its own opacity.
        <div className={cn("size-full", leaving && "kb-chat-field-arrive")}>
          <AsciiField
            mode="ambient"
            pulseEvent={THINKING_EVENT}
            className="kb-chat-field size-full text-(--ascii-ambient) dark:text-foreground/11"
          />
        </div>
      )}
      {(empty || leaving) && <RedactedDrift leaving={!empty} />}
    </div>
  )
}
