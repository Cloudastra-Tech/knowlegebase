import { describe, expect, it } from "vitest"

import { previewOf, updatedAgo, updatedOn } from "@/lib/citations"

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

describe("previewOf", () => {
  it("reads a Markdown table and emphasis as plain text", () => {
    const md = "| Persona | Question |\n|---------|----------|\n| **CTO / VP Engineering** | \"Are we on track?\" |"
    expect(previewOf(md)).toBe('Persona · Question CTO / VP Engineering · "Are we on track?"')
  })

  it("drops the title and headings a chunk starts with", () => {
    const text = "DevIQ: Product Guide 2. Who it's for Engineering leaders are accountable"
    expect(previewOf(text, { title: "DevIQ: Product Guide", heading_path: "2. Who it's for" })).toBe(
      "Engineering leaders are accountable",
    )
  })

  it("drops a numbered heading on its own line too", () => {
    const text = "DevIQ: Product Guide\n1. In one paragraph\nEngineering leaders are accountable"
    expect(previewOf(text, { title: "DevIQ: Product Guide", heading_path: "1. In one paragraph" })).toBe(
      "Engineering leaders are accountable",
    )
  })

  it("keeps links' words and drops code ticks and list marks", () => {
    expect(previewOf("- See [the guide](https://x.example) and `kb sync`")).toBe("See the guide and kb sync")
  })
})
