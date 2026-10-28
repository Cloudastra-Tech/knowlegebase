// "Reads the tools you already use. Writes to none of them." as an ASCII network
// (landing-network.tsx): every connector joined to one index by a line, and
// characters flowing along the lines one way only, into the index. Pure: the page
// measures where each connector sits (in cells), this draws the lines and the
// packets through `put`.

import { hash01 } from "@/components/ascii/drift"

export const N = { line: 1, lit: 2, packet: 3, signal: 4, box: 5, label: 6 } as const

export type Cells = { put(c: number, r: number, ch: string, tone: number): void }

// A connector's place: the row it sits on, the column its line starts at (just past
// its name), and which bus it joins (-1 the one on its right, the left column's; 1 the
// one on its left, the right column's). `soon`: not synced yet, a dashed line and no flow.
export type Node = { row: number; col: number; side: -1 | 1; soon?: boolean }

export type NetLayout = {
  nodes: Node[]
  index: { col: number; row: number; w: number } // the index box: left column, middle row, width
  bus: { left: number; right: number } // the buses' columns (-1: none)
  below: boolean // a phone: the index under the list, the bus coming down into its top
}

type Path = [number, number][]

// Each connector's route into the index, cell by cell, in the direction of flow.
export function routes(l: NetLayout): Path[] {
  return l.nodes.map((n) => {
    const bus = n.side === -1 ? l.bus.left : l.bus.right
    const path: Path = []
    // Along its row to the bus.
    for (let c = n.col; c !== bus; c += n.side === -1 ? 1 : -1) path.push([c, n.row])
    if (l.below) {
      // Down the bus to the index's top.
      const top = l.index.row - 2
      for (let r = n.row; r <= top; r++) path.push([bus, r])
      return path
    }
    // Along the bus to the index's row, then into the box.
    const step = l.index.row > n.row ? 1 : -1
    for (let r = n.row; r !== l.index.row; r += step) path.push([bus, r])
    const edge = n.side === -1 ? l.index.col - 1 : l.index.col + l.index.w
    for (let c = bus; c !== edge; c += n.side === -1 ? 1 : -1) path.push([c, l.index.row])
    path.push([edge, l.index.row])
    return path
  })
}

// The static lines: each route's cells as box-drawing characters, the junctions where
// they meet a bus, and the arrowheads into the index. `lit`: the connector whose
// route is drawn in the signal (hovered or focused).
export function drawLines(g: Cells, l: NetLayout, paths: Path[], lit = -1) {
  // The buses: from their outermost connector to the index's row (on a phone, to the
  // arrowhead over the index), each cell the junction its arms make.
  for (const side of [-1, 1] as const) {
    const bus = side === -1 ? l.bus.left : l.bus.right
    const nodes = l.nodes.map((n, i) => ({ ...n, i })).filter((n) => n.side === side)
    if (bus < 0 || !nodes.length) continue
    const end = l.below ? l.index.row - 2 : l.index.row
    const top = Math.min(...nodes.map((n) => n.row), end)
    const bottom = Math.max(...nodes.map((n) => n.row), end)
    const litRow = nodes.find((n) => n.i === lit)?.row
    for (let r = top; r <= bottom; r++) {
      if (l.below && r === end) continue // the arrowhead
      const at = nodes.some((n) => n.row === r)
      // A connector joins from its side; the index leaves to the other.
      const arms = {
        u: r > top,
        d: r < bottom,
        l: (at && side === -1) || (!l.below && r === end && side === 1),
        r: (at && side === 1) || (!l.below && r === end && side === -1),
      }
      const lit_ = litRow !== undefined && (r - litRow) * (end - r) >= 0
      g.put(bus, r, junction(arms), lit_ ? N.lit : N.line)
    }
  }
  // Each connector's own stretch, and the lane into the index: the lit one last, so a
  // lane they share shows it.
  const order = paths.map((_, i) => i).sort((a, b) => (a === lit ? 1 : b === lit ? -1 : 0))
  for (const i of order) {
    const p = paths[i]
    const n = l.nodes[i]
    const bus = n.side === -1 ? l.bus.left : l.bus.right
    for (let k = 0; k < p.length - 1; k++) {
      const [c, r] = p[k]
      if (c === bus) continue // drawn with the bus
      g.put(c, r, n.soon ? "┄" : "─", i === lit ? N.lit : N.line)
    }
  }
  // The arrowheads: one per bus, into the index.
  if (l.below) {
    g.put(l.bus.left, l.index.row - 2, "▾", lit >= 0 ? N.lit : N.line)
  } else {
    if (l.bus.left >= 0) g.put(l.index.col - 1, l.index.row, "▸", lit >= 0 && l.nodes[lit].side === -1 ? N.lit : N.line)
    if (l.bus.right >= 0) g.put(l.index.col + l.index.w, l.index.row, "◂", lit >= 0 && l.nodes[lit].side === 1 ? N.lit : N.line)
  }
}

const JUNCTIONS: Record<string, string> = {
  ud: "│", udl: "┤", udr: "├", udlr: "┼", dl: "┐", dr: "┌", dlr: "┬", ul: "┘", ur: "└", ulr: "┴", lr: "─", l: "─", r: "─", u: "│", d: "│",
}
export function junction(a: { u: boolean; d: boolean; l: boolean; r: boolean }) {
  const key = (a.u ? "u" : "") + (a.d ? "d" : "") + (a.l ? "l" : "") + (a.r ? "r" : "")
  return JUNCTIONS[key] ?? "│"
}

// The index box, three rows round its middle row (on a phone, under the arrowhead).
export function drawIndex(g: Cells, l: NetLayout, label = "index") {
  const { col, row, w } = l.index
  const top = row - 1
  for (let c = col; c < col + w; c++) {
    g.put(c, top, c === col ? "┌" : c === col + w - 1 ? "┐" : "─", N.box)
    g.put(c, row + 1, c === col ? "└" : c === col + w - 1 ? "┘" : "─", N.box)
  }
  g.put(col, row, "│", N.box)
  g.put(col + w - 1, row, "│", N.box)
  const x = col + Math.floor((w - label.length) / 2)
  for (let i = 0; i < label.length; i++) g.put(x + i, row, label[i], N.label)
}

// What flows: a few characters of a document at a time, from each live connector into
// the index, each line on its own slow beat so only a handful move at once.
const SPEED = 15 // cells a second
const GLYPHS = "▪▪░"

export function beatOf(i: number) {
  return { every: 6 + hash01(i * 31 + 7) * 6, phase: hash01(i * 17 + 3) * 8 }
}

export function drawPackets(g: Cells, l: NetLayout, paths: Path[], t: number, lit = -1) {
  paths.forEach((p, i) => {
    if (l.nodes[i].soon) return
    const { every, phase } = beatOf(i)
    const n = p.length
    const travel = (n + GLYPHS.length) / SPEED
    // The last few packets that can still be on the line.
    const latest = Math.floor((t - phase) / every)
    for (let k = latest; k >= 0 && t - phase - k * every <= travel; k--) {
      const head = Math.floor((t - phase - k * every) * SPEED)
      for (let j = 0; j < GLYPHS.length; j++) {
        const at = head - j
        if (at < 0 || at >= n - 1) continue
        const [c, r] = p[at]
        g.put(c, r, GLYPHS[j], i === lit ? N.signal : N.packet)
      }
    }
  })
}
