import { SearchX, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

// The saved prompt in the box answers without searching: removing it searches again.
export function NoSearchChip({ onRemove }: { onRemove: () => void }) {
  return (
    <span className="inline-flex h-7 items-center gap-1.5 rounded-full border border-border/70 bg-muted/40 pr-1 pl-2.5 text-xs">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1.5">
            <SearchX className="size-3.5 shrink-0 text-muted-foreground" />
            <span>No search</span>
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-60">
          This saved prompt answers directly, without searching your documents.
        </TooltipContent>
      </Tooltip>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-5 shrink-0 rounded-full"
        aria-label="Search documents for this question"
        onClick={onRemove}
      >
        <X className="size-3" />
      </Button>
    </span>
  )
}
