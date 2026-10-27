import type { SpaceSummary } from "@/lib/api"

export const ALL_COMPANY = "All company"

// A space's name as the switcher shows it: "My space" for your own.
export const spaceLabel = (space: SpaceSummary | null | undefined) =>
  !space ? ALL_COMPANY : space.kind === "personal" ? "My space" : space.name

// A file's size, as its chip shows it.
export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
