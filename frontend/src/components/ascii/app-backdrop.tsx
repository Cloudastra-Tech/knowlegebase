import { AsciiField } from "@/components/ascii/ascii-field"
import { THINKING_EVENT } from "@/components/ascii/events"
import { cn } from "@/lib/utils"

// The app's one background field, behind everything: dim, 12 fps, faded to about 15%
// under the content column, resting while the page scrolls and under a dialog, still
// with reduced motion or "Reduce effects". A ripple goes out from the composer when the
// assistant starts thinking. Never printed.
export function AppBackdrop({ className }: { className?: string }) {
  return (
    <div aria-hidden data-print="hide" className={cn("pointer-events-none fixed inset-0 -z-10", className)}>
      <AsciiField mode="ambient" tone="ambient" mask="reading-column" pulseEvent={THINKING_EVENT} className="size-full" />
    </div>
  )
}
