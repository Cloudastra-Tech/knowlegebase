import { describe, expect, it } from "vitest"

import {
  DRIFT_SNIPPETS,
  lensFalloff,
  lensSpan,
  lineSlots,
  lineSnippets,
  nearestLine,
  nextLine,
  redact,
  zoneOf,
} from "@/components/ascii/drift"

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

  it("with the composer centred, runs lines above the heading and under the recent chats, never across them", () => {
    const centred = { x: 160, y: 330, w: 736, h: 86 }
    const heading = { x: 160, y: 280, w: 736, h: 36 }
    const below = { x: 200, y: 500, w: 640, h: 200 }
    const ys = lineSlots({ top: 44, content: heading, composer: centred, below }, 800)
    expect(ys.some((y) => y < heading.y)).toBe(true)
    expect(ys.some((y) => y > below.y + below.h)).toBe(true)
    for (const y of ys) {
      expect(y < heading.y || y > heading.y + heading.h).toBe(true)
      expect(y < below.y || y > below.y + below.h).toBe(true)
      expect(y).toBeLessThan(800 - 14)
    }
  })
})

describe("the reading lens", () => {
  const lens = { x: 200, y: 100, r: 100 }

  it("takes the cells whose centres are inside its circle, and no others", () => {
    const cw = 7
    // Through the middle: a slice 200px wide, cells 7px.
    const [first, last] = lensSpan(lens, 100, cw, 0)!
    const centre = (n: number) => n * cw + cw / 2
    expect(centre(first)).toBeGreaterThanOrEqual(100)
    expect(centre(first - 1)).toBeLessThan(100)
    expect(centre(last)).toBeLessThanOrEqual(300)
    expect(centre(last + 1)).toBeGreaterThan(300)
    // Near its top edge, a narrower slice; past it, none.
    const [a, b] = lensSpan(lens, 180, cw, 0)!
    expect(b - a).toBeLessThan(last - first)
    for (let n = a; n <= b; n++) expect(Math.hypot(centre(n) - lens.x, 180 - lens.y)).toBeLessThan(lens.r)
    expect(lensSpan(lens, 201, cw, 0)).toBeNull()
    expect(lensSpan(lens, -1, cw, 0)).toBeNull()
  })

  it("follows the line as it drifts: cell indices shift with the line's origin", () => {
    const [f0] = lensSpan(lens, 100, 7, 0)!
    const [f1] = lensSpan(lens, 100, 7, -70)! // the line moved 70px (10 cells) left
    expect(f1 - f0).toBe(10)
  })

  it("reads fully in the middle and softly at the rim", () => {
    expect(lensFalloff(0, 100)).toBe(1)
    expect(lensFalloff(100, 100)).toBeCloseTo(0.35)
    expect(lensFalloff(50, 100)).toBeGreaterThan(lensFalloff(90, 100))
  })
})

describe("nearestLine", () => {
  it("finds the line a click is on or near, within reach", () => {
    const ys = [400, 120, 180, 240]
    expect(nearestLine(ys, 185)).toBe(2)
    expect([1, 2]).toContain(nearestLine(ys, 150, 30)) // 30 from both: either, but one
    expect(nearestLine(ys, 300, 30)).toBe(-1)
    expect(nearestLine(ys, 300)).toBe(3) // a tap: the nearest, however far
  })
})
