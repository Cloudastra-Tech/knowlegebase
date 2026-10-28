import { describe, expect, it } from "vitest"

import { DRIFT_SNIPPETS, lineSlots, lineSnippets, nextLine, redact, zoneOf } from "@/components/ascii/drift"

const words = (s: string) => s.split(" ").map((w) => w.length)

describe("redact", () => {
  it("keeps every word's length, and the spaces between", () => {
    for (const s of DRIFT_SNIPPETS) {
      const r = redact(s)
      expect(r).toHaveLength(s.length)
      expect(words(r)).toEqual(words(s))
      expect(r).toMatch(/^[▪░· ]+$/)
    }
    expect(redact("leave policy · 26 weeks")).toBe("▪▪▪▪▪ ▪▪▪▪▪▪ · ░░ ▪▪▪▪▪")
  })
})

describe("nextLine", () => {
  it("goes round the eligible lines instead of repeating one", () => {
    const eligible = [5, 1, 3]
    const seen: number[] = []
    let last: number | null = null
    for (let i = 0; i < 6; i++) {
      last = nextLine(eligible, last)
      seen.push(last!)
    }
    expect(seen).toEqual([1, 3, 5, 1, 3, 5])
    for (let i = 1; i < seen.length; i++) expect(seen[i]).not.toBe(seen[i - 1])
  })

  it("has nothing to give with no line in the zone", () => {
    expect(nextLine([], 2)).toBeNull()
    expect(nextLine([4], 4)).toBe(4) // the only one: it may go again
  })
})

describe("lineSnippets", () => {
  it("starts neighbouring lines on different records, all of them made up", () => {
    const firsts = [0, 1, 2, 3].map((rank) => lineSnippets(rank)[0])
    for (let i = 1; i < firsts.length; i++) expect(firsts[i]).not.toBe(firsts[i - 1])
    for (const rank of [0, 1, 2]) expect([...lineSnippets(rank)].sort()).toEqual([...DRIFT_SNIPPETS].sort())
  })
})

describe("the layout", () => {
  const composer = { x: 16, y: 730, w: 358, h: 86 }

  it("keeps the readable zone on a phone's screen", () => {
    const z = zoneOf(composer, 390, 844)
    expect(z.cx - z.a).toBeGreaterThanOrEqual(0)
    expect(z.cx + z.a).toBeLessThanOrEqual(390)
    // and lets it reach out past the composer on a wide one
    const wide = zoneOf({ x: 144, y: 655, w: 735, h: 85 }, 1024, 750)
    expect(wide.a).toBeGreaterThan(735 / 2 + 60)
  })

  it("runs lines in the free space, never across the empty state's words", () => {
    const content = { x: 0, y: 100, w: 390, h: 250 }
    const ys = lineSlots({ top: 44, content, composer }, 844)
    expect(ys.length).toBeGreaterThanOrEqual(6)
    expect(ys.length).toBeLessThanOrEqual(8)
    for (const y of ys) expect(y < content.y || y > content.y + content.h).toBe(true)
    // one through the composer, frosted by its glass
    expect(ys[0]).toBe(composer.y + composer.h / 2)
  })
})
