import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { ScrambleText } from "@/components/ascii/scramble-text"
import { cn } from "@/lib/utils"

// The assistant at work: a braille spinner and a status line ("Searching 4 sources")
// that decodes into place each time it changes, in the signal mint. Assistive
// technology hears only the status itself, politely, once per change: the spinner and
// the decoding characters are aria-hidden.
export function AsciiThinking({ status, className }: { status: string; className?: string }) {
  return (
    <div
      data-slot="ascii-thinking"
      role="status"
      className={cn("flex items-center gap-2 font-mono text-xs text-signal", className)}
    >
      <AsciiSpinner label="" size="sm" />
      <ScrambleText text={status} className="truncate" />
    </div>
  )
}
