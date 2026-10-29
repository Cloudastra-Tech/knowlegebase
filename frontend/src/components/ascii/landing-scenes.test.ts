import { describe, expect, it } from "vitest"

import { redact, reveal, span, typed } from "@/components/ascii/decode"
import { drawLines, drawPackets, junction, routes, type NetLayout } from "@/components/ascii/network"
import { drawRoute, LABELS, layoutRoute } from "@/components/ascii/route"

// The landing's ASCII scenes are functions of their clock: what they show at a moment
// is checked here as text, cell by cell, without a canvas.

// A grid of characters that records what a scene puts.
function cells(cols: number, rows: number) {
  const grid = Array.from({ length: rows }, () => Array<string>(cols).fill(" "))
  return {
    put(c: number, r: number, ch: string) {
      if (c >= 0 && r >= 0 && c < cols && r < rows) grid[r][c] = ch
    },
    text: () => grid.map((row) => row.join("").trimEnd()).join("\n"),
  }
}

describe("decoding text", () => {
  it("goes from the redaction to the text, keeping the length and the spaces", () => {
    const text = "Finance approves within 5 days"
    expect(reveal(text, 0, 0)).toBe(redact(text))
    expect(reveal(text, 1, 0)).toBe(text)
    for (const p of [0.2, 0.5, 0.8]) {
      const mid = reveal(text, p, 3)
      expect(mid).toHaveLength(text.length)
      expect([...mid].map((ch, i) => (ch === " ") === (text[i] === " ")).every(Boolean)).toBe(true)
    }
    // Left to right: the start reads before the end does.
    const half = reveal(text, 0.5, 0)
    expect(half.startsWith("Finance")).toBe(true)
    expect(half.endsWith(redact("days"))).toBe(true)
  })

  it("draws the same frame for the same moment", () => {
    expect(reveal("over 15% needs sign-off", 0.4, 7)).toBe(reveal("over 15% needs sign-off", 0.4, 7))
  })

  it("re-forms one text into another without dropping the old one's letters at once", () => {
    const from = "Pay isn't in the documents you can see."
    const to = "Senior engineers are paid 38 to 46 lakh."
    const size = Math.max(from.length, to.length)
    const start = reveal(to.padEnd(size), 0.01, 0, { from: from.padEnd(size), spaces: false })
    expect(start.slice(10)).toBe(from.padEnd(size).slice(10))
  })

  it("types with a cursor until it's done", () => {
    expect(typed("who approves?", 3)).toBe("who▌")
    expect(typed("who approves?", 99)).toBe("who approves?")
    expect(span(1.5, 1, 1)).toBe(0.5)
  })
})

describe("where the model runs", () => {
  it("draws docs, server, chat and search, with the search out on its own", () => {
    const l = layoutRoute(120)
    const g = cells(120, l.rows)
    drawRoute(g, l, 0, "aws", "aws", Infinity)
    const text = g.text()
    for (const word of ["your docs", "this server", "chat", "search"]) expect(text).toContain(word)
    expect(text.split(LABELS.aws.chat).length - 1).toBe(2) // chat and search, both on Bedrock
    // The search box sits under chat, not under the server.
    const searchRow = text.split("\n").findIndex((row) => row.includes("search"))
    expect(searchRow).toBeGreaterThan(1)
    expect(text.split("\n")[searchRow].indexOf("search")).toBeGreaterThan(l.chat.x)
  })

  it("docks the search under the server when it runs there, and no document text leaves", () => {
    const l = layoutRoute(120)
    const g = cells(120, l.rows)
    drawRoute(g, l, 5, "local", "local", Infinity)
    const rows = g.text().split("\n")
    const searchRow = rows.findIndex((row) => row.includes("search"))
    expect(rows[searchRow].indexOf("search")).toBeLessThan(l.chat.x)
    expect(rows[l.server.y + 3]).toContain("├") // one box with two compartments
    expect(g.text()).toContain(LABELS.local.search)
  })

  it("re-forms a box's label when the choice changes", () => {
    const l = layoutRoute(120)
    const g = cells(120, l.rows)
    drawRoute(g, l, 1, "key", "aws", 0.25)
    const text = g.text()
    expect(text).not.toContain(LABELS.key.chat)
    expect(text).not.toContain(LABELS.aws.chat)
  })

  it("stacks on a phone", () => {
    const l = layoutRoute(48)
    expect(l.wide).toBe(false)
    expect(l.server.y).toBeGreaterThan(l.docs.y)
    expect(l.chat.x + l.bw).toBeLessThanOrEqual(48)
  })
})

describe("the sources' network", () => {
  const layout: NetLayout = {
    nodes: [
      { row: 1, col: 2, side: -1 },
      { row: 5, col: 2, side: -1 },
      { row: 2, col: 30, side: 1 },
      { row: 4, col: 30, side: 1, soon: true },
    ],
    index: { col: 14, row: 3, w: 7 },
    bus: { left: 10, right: 26 },
    below: false,
  }

  it("routes every connector into the index, never out of it", () => {
    const paths = routes(layout)
    for (const p of paths) {
      const [c, r] = p[p.length - 1]
      expect(r).toBe(layout.index.row)
      expect(c === layout.index.col - 1 || c === layout.index.col + layout.index.w).toBe(true)
    }
  })

  it("draws junctions where the lines meet, and arrowheads into the index", () => {
    const g = cells(36, 8)
    drawLines(g, layout, routes(layout))
    const rows = g.text().split("\n")
    expect(rows[1][10]).toBe("┐")
    expect(rows[3][10]).toBe("├")
    expect(rows[5][10]).toBe("┘")
    expect(rows[3][13]).toBe("▸")
    expect(rows[3][21]).toBe("◂")
    expect(rows[4]).toContain("┄") // coming soon: dashed, and nothing flows on it
    expect(junction({ u: true, d: true, l: true, r: true })).toBe("┼")
  })

  it("moves what flows towards the index as time passes", () => {
    const paths = routes(layout)
    const at = (t: number) => {
      const g = cells(36, 8)
      drawPackets(g, layout, paths, t)
      return g.text()
    }
    const frames = Array.from({ length: 40 }, (_, i) => at(i * 0.25))
    expect(new Set(frames).size).toBeGreaterThan(5)
    // Nothing on the line of the one coming soon (its own stretch, right of the bus).
    expect(frames.every((f) => !f.split("\n")[4].slice(27).includes("▪"))).toBe(true)
  })
})
