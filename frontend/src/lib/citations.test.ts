import { describe, expect, it } from "vitest"

import { updatedAgo, updatedOn } from "@/lib/citations"

describe("updatedAgo", () => {
  const now = new Date("2026-10-27T12:00:00Z")

  it.each([
    ["2026-04-27T10:00:00+00:00", "Updated 6 months ago"],
    ["2026-09-26T10:00:00+00:00", "Updated 1 month ago"],
    ["2025-11-15T10:00:00+00:00", "Updated 11 months ago"],
    ["2023-06-01T10:00:00+00:00", "Updated 3 years ago"],
  ])("says how long ago %s was", (modified, label) => {
    expect(updatedAgo(modified, now)).toBe(label)
  })

  it("gives the date under a month, and nothing without one", () => {
    const recent = "2026-10-20T10:00:00+00:00"
    expect(updatedAgo(recent, now)).toBe(updatedOn(recent))
    expect(updatedAgo(null, now)).toBe("")
    expect(updatedAgo("not a date", now)).toBe("")
  })
})
