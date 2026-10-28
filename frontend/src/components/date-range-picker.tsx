import { useState } from "react"
import { format } from "date-fns"
import { CalendarDays, X } from "lucide-react"
import type { DateRange } from "react-day-picker"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

export type { DateRange }

function label(range: DateRange | undefined) {
  if (!range?.from) return "Any date"
  const from = format(range.from, "d MMM yyyy")
  if (!range.to || range.to.getTime() === range.from.getTime()) return from
  return `${from} – ${format(range.to, "d MMM yyyy")}`
}

// A range of days, picked on a calendar in a popover (the app's date filter; never
// a native date input). Clearing it means any date.
export function DateRangePicker({
  value,
  onChange,
  className,
  id,
}: {
  value: DateRange | undefined
  onChange: (range: DateRange | undefined) => void
  className?: string
  id?: string
}) {
  const [today] = useState(() => new Date())
  return (
    <div className={cn("flex items-center gap-1", className)}>
      <Popover>
        <PopoverTrigger asChild>
          <Button
            id={id}
            variant="outline"
            className={cn(
              "min-w-0 flex-1 justify-start font-normal",
              !value?.from && "text-muted-foreground",
            )}
          >
            <CalendarDays data-icon="inline-start" />
            <span className="truncate tabular-nums">{label(value)}</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar
            mode="range"
            selected={value}
            onSelect={onChange}
            numberOfMonths={1}
            disabled={{ after: today }}
            autoFocus
          />
        </PopoverContent>
      </Popover>
      {value?.from && (
        <Button
          size="icon"
          variant="ghost"
          className="size-8 shrink-0"
          aria-label="Any date"
          onClick={() => onChange(undefined)}
        >
          <X />
        </Button>
      )}
    </div>
  )
}
