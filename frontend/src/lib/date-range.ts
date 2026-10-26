import type { DateRange } from "react-day-picker"

// [from, to) in Unix seconds for a range of whole local days.
export function rangeToEpoch(range: DateRange | undefined) {
  if (!range?.from) return {}
  const from = new Date(range.from)
  from.setHours(0, 0, 0, 0)
  const to = new Date(range.to ?? range.from)
  to.setHours(0, 0, 0, 0)
  to.setDate(to.getDate() + 1)
  return { from: Math.floor(from.getTime() / 1000), to: Math.floor(to.getTime() / 1000) }
}
