import { describe, expect, it } from "vitest"

import {
  covered,
  DRIFT_SNIPPETS,
  lensFalloff,
  lensSpan,
  lineSnippets,
  lineSpans,
  nearestLine,
  nextLine,
  pageRows,
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

  it("runs lines down the whole page, evenly, one through the composer's middle", () => {
    const centred = { x: 160, y: 330, w: 736, h: 86 }
    const mid = centred.y + centred.h / 2
    const ys = pageRows({ top: 44, bottom: 728, anchor: mid }, 728)
    // the nearest first: the composer's own line
    expect(ys[0]).toBe(mid)
    const sorted = [...ys].sort((a, b) => a - b)
    // from just under the top to just over the bottom, nothing past either
    expect(sorted[0]).toBeGreaterThan(44 + 12)
    expect(sorted[0]).toBeLessThan(44 + 12 + 60)
    expect(sorted[sorted.length - 1]).toBeLessThan(728 - 10)
    expect(sorted[sorted.length - 1]).toBeGreaterThan(728 - 10 - 60)
    // calm: an even step of 42–60px
    const steps = sorted.slice(1).map((y, i) => y - sorted[i])
    for (const d of steps) {
      expect(d).toBeCloseTo(steps[0], 5)
      expect(d).toBeGreaterThanOrEqual(42)
      expect(d).toBeLessThanOrEqual(60)
    }
    // a phone's page has its lines too, top to bottom
    const phone = pageRows({ top: 48, bottom: 844, anchor: 400 }, 844)
    expect(Math.min(...phone)).toBeLessThan(48 + 12 + 60)
    expect(Math.max(...phone)).toBeGreaterThan(844 - 10 - 60)
  })
})

describe("lineSpans", () => {
  const heading = { box: { x: 300, y: 100, w: 400, h: 40 }, k: 0.3 }
  const card = { box: { x: 160, y: 500, w: 736, h: 200 }, k: 1.8 }

  it("draws a line in open space at full strength", () => {
    expect(lineSpans(1000, 300, 6, null, 0.45, [heading, card])).toEqual([[0, 1000, 1]])
  })

  it("draws it faint under words and stronger under glass", () => {
    expect(lineSpans(1000, 120, 6, null, 0.45, [heading, card])).toEqual([
      [0, 300, 1],
      [300, 700, 0.3],
      [700, 1000, 1],
    ])
    expect(lineSpans(1000, 600, 6, null, 0.45, [heading, card])).toEqual([
      [0, 160, 1],
      [160, 896, 1.8],
      [896, 1000, 1],
    ])
  })

  it("draws it lighter in the readable zone, a cover over the zone winning", () => {
    expect(lineSpans(1000, 300, 6, { cx: 500, hx: 300 }, 0.45, [])).toEqual([
      [0, 200, 1],
      [200, 800, 0.45],
      [800, 1000, 1],
    ])
    expect(lineSpans(1000, 120, 6, { cx: 500, hx: 300 }, 0.45, [heading])).toEqual([
      [0, 200, 1],
      [200, 300, 0.45],
      [300, 700, 0.3],
      [700, 800, 0.45],
      [800, 1000, 1],
    ])
  })

  it("counts a cover the line's band only touches", () => {
    expect(lineSpans(1000, 96, 6, null, 0.45, [heading])).toHaveLength(3)
    expect(lineSpans(1000, 90, 6, null, 0.45, [heading])).toEqual([[0, 1000, 1]])
  })

  it("says where no cell reads: under any cover", () => {
    expect(covered([heading, card], 400, 120)).toBe(true)
    expect(covered([heading, card], 400, 600)).toBe(true)
    expect(covered([heading, card], 100, 600)).toBe(false)
    expect(covered([heading, card], 400, 300)).toBe(false)
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
