// "You choose where the model runs" as an ASCII diagram (landing-route.tsx): your
// documents go to this server, which sends chat to one model and search (the index's
// embeddings) to another, as the Assistant screen chooses them apart (models.py).
// Packets of characters travel the lanes: ▪ document text, ? a question, ¶ the
// passages it quotes. With search on this server, the search box docks under the
// server and no document text leaves it. Pure: it writes cells through `put`.

import { bezier, reveal, span } from "@/components/ascii/decode"

export type RouteMode = "aws" | "key" | "local"

// Tones (indices into the grid's palette; 0 is empty).
export const T = { line: 1, title: 2, label: 3, signal: 4, doc: 5, question: 6 } as const

// What each box says, per mode: [title, label].
export const LABELS: Record<RouteMode, { chat: string; search: string }> = {
  aws: { chat: "aws bedrock", search: "aws bedrock" },
  key: { chat: "openai/anthropic", search: "openai" },
  local: { chat: "your chat model", search: "server's own cpu" },
}

export type Cells = { put(c: number, r: number, ch: string, tone: number): void }

type Box = { x: number; y: number }
export type RouteLayout = {
  wide: boolean
  bw: number // a box's width
  iw: number // its inside
  rows: number
  docs: Box
  server: Box
  chat: Box
  remote: Box // the search box out on its own
  docked: Box // and under the server
  tap: number // the column the search lane leaves the server from
}

// Boxes are 4 rows: top, title, label, bottom. Wide: docs, server and chat in a row,
// search under chat. Narrow (a phone): docs over the server, chat beside it, search
// under chat.
export function layoutRoute(cols: number, wide = cols >= 84): RouteLayout {
  if (wide) {
    const iw = 16
    const bw = iw + 4
    const lane = Math.max(8, Math.min(24, Math.floor((cols - 3 * bw) / 2)))
    const x0 = Math.max(0, Math.floor((cols - (3 * bw + 2 * lane)) / 2))
    const server = { x: x0 + bw + lane, y: 0 }
    const chat = { x: x0 + 2 * (bw + lane), y: 0 }
    return {
      wide,
      bw,
      iw,
      rows: 9,
      docs: { x: x0, y: 0 },
      server,
      chat,
      remote: { x: chat.x, y: 5 },
      docked: { x: server.x, y: 3 },
      tap: server.x + bw - 5,
    }
  }
  const iw = Math.max(9, Math.min(16, Math.floor((cols - 4) / 2) - 4))
  const bw = iw + 4
  const chat = { x: Math.max(bw + 3, cols - bw), y: 6 }
  return {
    wide,
    bw,
    iw,
    rows: 15,
    docs: { x: 0, y: 0 },
    server: { x: 0, y: 6 },
    chat,
    remote: { x: chat.x, y: 11 },
    docked: { x: 0, y: 9 },
    tap: bw - 5,
  }
}

const fit = (text: string, n: number) => (text.length > n ? text.slice(0, n - 1) + "…" : text.padEnd(n))

function box(g: Cells, b: Box, l: RouteLayout, title: string, label: string, opts: { joined?: boolean; decoding?: boolean } = {}) {
  const { bw, iw } = l
  const right = b.x + bw - 1
  for (let c = b.x + 1; c < right; c++) {
    if (!opts.joined) g.put(c, b.y, "─", T.line)
    g.put(c, b.y + 3, "─", T.line)
  }
  g.put(b.x, b.y, opts.joined ? "├" : "┌", T.line)
  g.put(right, b.y, opts.joined ? "┤" : "┐", T.line)
  g.put(b.x, b.y + 3, "└", T.line)
  g.put(right, b.y + 3, "┘", T.line)
  for (const r of [1, 2]) {
    g.put(b.x, b.y + r, "│", T.line)
    g.put(right, b.y + r, "│", T.line)
  }
  const t = fit(title, iw)
  for (let i = 0; i < iw; i++) if (t[i] !== " ") g.put(b.x + 2 + i, b.y + 1, t[i], T.title)
  const s = label.padEnd(iw).slice(0, iw)
  for (let i = 0; i < iw; i++) if (s[i] !== " ") g.put(b.x + 2 + i, b.y + 2, s[i], opts.decoding ? T.signal : T.label)
}

// A lane as the cells a packet travels, in order; the last is the arrowhead.
type Lane = { cells: [number, number][]; arrow: string }

function lanes(l: RouteLayout, search: Box) {
  const { docs, server, chat, bw } = l
  const legs: Record<"docs" | "chat" | "search", Lane | null> = { docs: null, chat: null, search: null }
  if (l.wide) {
    const row = 1
    legs.docs = { cells: line(docs.x + bw, row, server.x - 1, row), arrow: "▸" }
  } else {
    const col = docs.x + Math.floor(bw / 2)
    legs.docs = { cells: line(col, docs.y + 4, col, server.y - 1), arrow: "▾" }
  }
  legs.chat = { cells: line(server.x + bw, server.y + 1, chat.x - 1, server.y + 1), arrow: "▸" }
  // Search: down from the server, then along to the box, while it's out on its own.
  const row = search.y + 1
  if (search.x - 1 > l.tap + 1) {
    legs.search = { cells: [...line(l.tap, server.y + 4, l.tap, row), ...line(l.tap + 1, row, search.x - 1, row)], arrow: "▸" }
  }
  return legs
}

function line(c0: number, r0: number, c1: number, r1: number): [number, number][] {
  const out: [number, number][] = []
  const n = Math.max(Math.abs(c1 - c0), Math.abs(r1 - r0))
  for (let i = 0; i <= n; i++) {
    out.push([c0 + Math.sign(c1 - c0) * Math.min(i, Math.abs(c1 - c0)), r0 + Math.sign(r1 - r0) * Math.min(i, Math.abs(r1 - r0))])
  }
  return out
}

function drawLane(g: Cells, lane: Lane) {
  const { cells } = lane
  for (let i = 0; i < cells.length - 1; i++) {
    const [c, r] = cells[i]
    const prev = cells[i - 1]
    const next = cells[i + 1]
    let ch = prev && prev[0] === c && next[0] === c ? "│" : "─"
    if (!prev) ch = next[0] === c ? "│" : "─"
    if (prev && prev[0] === c && next[1] === r) ch = "└" // down, then along
    g.put(c, r, ch, T.line)
  }
  const [c, r] = cells[cells.length - 1]
  g.put(c, r, lane.arrow, T.line)
}

// The packets on a lane at time t: `glyphs` long, one every `every` seconds, moving
// `speed` cells a second; gone at the arrowhead.
function packets(g: Cells, lane: Lane, t: number, { every, speed, glyphs, tones, phase = 0 }: Packet) {
  const n = lane.cells.length - 1
  const travel = (n + glyphs.length) / speed
  const first = Math.floor((t - phase - travel) / every)
  for (let k = Math.max(0, first); k * every + phase <= t; k++) {
    const head = Math.floor((t - phase - k * every) * speed)
    for (let i = 0; i < glyphs.length; i++) {
      const at = head - i
      if (at < 0 || at >= n) continue
      const [c, r] = lane.cells[at]
      g.put(c, r, glyphs[i], tones[i])
    }
  }
}
type Packet = { every: number; speed: number; glyphs: string; tones: number[]; phase?: number }

const DOC: Packet = { every: 1.15, speed: 13, glyphs: "▪░▪▪", tones: [T.doc, T.doc, T.doc, T.doc] }
const ASK: Packet = { every: 1.9, speed: 13, glyphs: "¶¶?", tones: [T.signal, T.signal, T.question], phase: 0.5 }
const SEARCH: Packet = { every: 1.4, speed: 13, glyphs: "▪▪░", tones: [T.doc, T.doc, T.doc], phase: 0.25 }

const GLIDE = 0.5 // the search box moving between its places
const SCRAMBLE = 0.45 // a box's label re-forming
const glide = bezier([0.77, 0, 0.175, 1]) // ease-in-out-strong: on-screen movement

// The diagram at time t, `since` seconds after the mode changed from `from` to `mode`
// (since past every transition, or `from` the same, is the settled diagram).
export function drawRoute(g: Cells, l: RouteLayout, t: number, mode: RouteMode, from: RouteMode, since: number) {
  const tick = Math.floor(t * 20)
  const docked = (m: RouteMode) => (m === "local" ? 1 : 0)
  const k = glide(span(since, 0, GLIDE))
  const d = docked(from) + (docked(mode) - docked(from)) * k
  const search = {
    x: Math.round(l.remote.x + (l.docked.x - l.remote.x) * d),
    y: Math.round(l.remote.y + (l.docked.y - l.remote.y) * d),
  }
  const label = (key: "chat" | "search") => {
    const p = span(since, 0.08, SCRAMBLE)
    const a = LABELS[from][key]
    const b = LABELS[mode][key]
    return { text: a === b || p >= 1 ? b : reveal(b.padEnd(l.iw), p, tick, { from: a.padEnd(l.iw), band: 4, spaces: false }), decoding: a !== b && p < 1 }
  }
  const legs = lanes(l, search)
  for (const lane of [legs.docs, legs.chat, legs.search]) if (lane) drawLane(g, lane)
  box(g, l.docs, l, "your docs", "wikis, drives")
  box(g, l.server, l, "this server", "index")
  const chat = label("chat")
  box(g, l.chat, l, "chat", chat.text, { decoding: chat.decoding })
  const s = label("search")
  box(g, search, l, "search", s.text, { decoding: s.decoding, joined: d === 1 })
  // Where the lanes leave a box, its edge has a junction.
  if (l.wide) g.put(l.docs.x + l.bw - 1, l.docs.y + 1, "├", T.line)
  else g.put(l.docs.x + Math.floor(l.bw / 2), l.docs.y + 3, "┬", T.line)
  g.put(l.server.x + l.bw - 1, l.server.y + 1, "├", T.line)
  if (legs.search) g.put(l.tap, l.server.y + 3, "┬", T.line)
  if (legs.docs) packets(g, legs.docs, t, DOC)
  if (legs.chat) packets(g, legs.chat, t, ASK)
  // No document text leaves while the search box moves, nor once it's on the server.
  if (legs.search && d === 0 && since >= GLIDE) packets(g, legs.search, t, SEARCH)
}
