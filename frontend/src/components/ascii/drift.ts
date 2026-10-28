// "Redacted drift", the empty chat's art (redacted-drift.tsx): the parts that need no
// canvas. Lines of made-up records drift sideways, redacted; near the composer one
// line at a time becomes readable. Everything here is pure, so it's tested as is.

// What a decoded line can say: neutral, made up, never a real document's title (an
// empty screen shows nothing private). Short enough to fit the readable zone on a phone.
export const DRIFT_SNIPPETS = [
  "leave policy · section 3",
  "expense approvals · finance",
  "onboarding checklist · week 1",
  "security policy · laptops",
  "holiday calendar · 2027",
  "travel policy · economy",
  "parental leave · 26 weeks",
  "password reset · self-serve",
  "vendor contracts · notice",
  "remote work · core hours",
  "brand guidelines · logo",
  "incident runbook · on-call",
] as const

// A record as it shows outside the zone: letters a solid block, digits a light shade,
// anything else a dot, spaces kept. Same length, same word lengths.
export function redact(text: string) {
  let out = ""
  for (const ch of text) {
    if (ch === " ") out += " "
    else if (/\p{L}/u.test(ch)) out += "▪"
    else if (/\p{N}/u.test(ch)) out += "░"
    else out += "·"
  }
  return out
}

// The line to decode next: the first eligible one after the last decoded, wrapping
// round, so decodes go round the lines instead of landing on the same one.
export function nextLine(eligible: readonly number[], last: number | null) {
  if (!eligible.length) return null
  const sorted = [...eligible].sort((a, b) => a - b)
  if (last === null) return sorted[0]
  return sorted.find((i) => i > last) ?? sorted[0]
}

// Each line's records, in its own order: the list turned by `rank` (so neighbouring
// lines start on different records) and the rest lightly shuffled with a fixed seed.
export function lineSnippets(rank: number, seed = 7) {
  const list = [...DRIFT_SNIPPETS]
  const k = (rank * 5) % list.length
  const order = list.slice(k).concat(list.slice(0, k))
  const rand = mulberry32(seed + rank)
  for (let i = order.length - 1; i > 1; i--) {
    if (rand() < 0.35) {
      const j = 1 + Math.floor(rand() * i)
      ;[order[i], order[j]] = [order[j], order[i]]
    }
  }
  return order
}

// A small seeded PRNG and an integer hash, so the layout is the same on every visit.
export function mulberry32(a: number) {
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
export function hash01(n: number) {
  n = (n ^ 61) ^ (n >>> 16)
  n = Math.imul(n, 9)
  n ^= n >>> 4
  n = Math.imul(n, 0x27d4eb2d)
  n ^= n >>> 15
  return (n >>> 0) / 4294967296
}

export type Box = { x: number; y: number; w: number; h: number }

// The readable zone: an ellipse round the composer, scaled to the screen, so on a
// phone it's the width of the screen and not wider (decodes need a record to fit in).
// There it reaches a little higher instead, so the lines above the composer have
// room for a whole record.
export function zoneOf(composer: Box, width: number, height: number) {
  const padX = clamp(width * 0.1, 16, 130)
  const padY = clamp(height * 0.15, 70, 130) + (width < 640 ? 40 : 0)
  return {
    cx: composer.x + composer.w / 2,
    cy: composer.y + composer.h / 2,
    a: Math.min(composer.w / 2 + padX, width / 2),
    b: composer.h / 2 + padY,
  }
}
export type Zone = ReturnType<typeof zoneOf>

export const inZone = (z: Zone, x: number, y: number, k = 1) => {
  const dx = (x - z.cx) / (z.a * k)
  const dy = (y - z.cy) / (z.b * k)
  return dx * dx + dy * dy < 1
}

// Half the zone's width at height y (0 outside it).
export const zoneHalfWidth = (z: Zone, y: number, k = 1) => {
  const t = (y - z.cy) / (z.b * k)
  return t * t >= 1 ? 0 : z.a * k * Math.sqrt(1 - t * t)
}

// The box round two boxes.
export function union(a: Box, b: Box): Box {
  const x = Math.min(a.x, b.x)
  const y = Math.min(a.y, b.y)
  return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y }
}

// Where lines run: the whole page, top to bottom, evenly spaced, one through the
// composer's middle and the rest a step (`gap`) apart up and down from it. Words and
// glass over them don't move them: they're drawn fainter there (lineSpans). Sorted
// by distance from the composer, so the nearest come first (depths go by rank).
export function pageRows({ top, bottom, anchor }: { top: number; bottom: number; anchor: number }, height: number) {
  const gap = clamp(height * 0.075, 42, 60)
  const ys: number[] = []
  for (let y = anchor; y > top + 12; y -= gap) ys.push(y)
  for (let y = anchor + gap; y < bottom - 10; y += gap) ys.push(y)
  return ys.sort((a, b) => Math.abs(a - anchor) - Math.abs(b - anchor))
}

// Something over the art: a box (in the art's coordinates) and how strongly the lines
// under it are drawn, as a factor of their alpha (faint under words, full or more
// under glass, whose blur and fill soften them).
export type Cover = { box: Box; k: number }

// Whether the band of a line at height y (half its height `half`) meets a box.
const meets = (b: Box, y: number, half: number) => y + half > b.y && y - half < b.y + b.h

// Along a line at height y, the stretches it's drawn in and at what strength: under a
// cover, the cover's own; in the readable zone (its half-width at y, about its centre
// cx), `zoneK`; elsewhere full. Neighbouring stretches of one strength are one.
// Returns [x0, x1, k] triples covering 0..width.
export function lineSpans(
  width: number,
  y: number,
  half: number,
  zone: { cx: number; hx: number } | null,
  zoneK: number,
  covers: readonly Cover[],
) {
  const hits = covers.filter((c) => meets(c.box, y, half))
  const cuts = new Set([0, width])
  const add = (x: number) => x > 0 && x < width && cuts.add(x)
  if (zone && zone.hx > 0) {
    add(zone.cx - zone.hx)
    add(zone.cx + zone.hx)
  }
  for (const c of hits) {
    add(c.box.x)
    add(c.box.x + c.box.w)
  }
  const xs = [...cuts].sort((a, b) => a - b)
  const out: [number, number, number][] = []
  for (let i = 0; i < xs.length - 1; i++) {
    const mid = (xs[i] + xs[i + 1]) / 2
    const cover = hits.find((c) => mid > c.box.x && mid < c.box.x + c.box.w)
    const k = cover ? cover.k : zone && Math.abs(mid - zone.cx) < zone.hx ? zoneK : 1
    const last = out[out.length - 1]
    if (last && last[2] === k) last[1] = xs[i + 1]
    else out.push([xs[i], xs[i + 1], k])
  }
  return out
}

// Whether a point is under any cover: no cell reads there (a lens or a decode).
export const covered = (covers: readonly Cover[], x: number, y: number) =>
  covers.some((c) => x > c.box.x && x < c.box.x + c.box.w && y > c.box.y && y < c.box.y + c.box.h)

// The reading lens (the pointer, on a fine pointer): a circle in which the lines read.
export type Lens = { x: number; y: number; r: number }

// The cells of a line at height `y` whose centres are inside the lens, as the first
// and last cell index (cell n's left edge is at originX + n * cw), or null for none.
// A circle's slice is one run, so a range says it all.
export function lensSpan(lens: Lens, y: number, cw: number, originX: number): [number, number] | null {
  const dy = y - lens.y
  const h2 = lens.r * lens.r - dy * dy
  if (h2 <= 0 || cw <= 0) return null
  const h = Math.sqrt(h2)
  const first = Math.ceil((lens.x - h - originX) / cw - 0.5)
  const last = Math.floor((lens.x + h - originX) / cw - 0.5)
  return first <= last ? [first, last] : null
}

// How strongly a cell `d` px from the lens's centre reads: fully in the middle,
// about a third at the edge, so the lens has a soft rim and no line.
export const lensFalloff = (d: number, r: number) => 0.35 + 0.65 * clamp(1 - (d * d) / (r * r), 0, 1)

// The line nearest height `y` (its index in `ys`), if one is within `max` px; -1 if not.
export function nearestLine(ys: readonly number[], y: number, max = Infinity) {
  let best = -1
  let bestD = max
  ys.forEach((ly, i) => {
    const d = Math.abs(ly - y)
    if (d <= bestD) {
      best = i
      bestD = d
    }
  })
  return best
}

export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

// Frame-rate independent ease towards a target.
export const approach = (cur: number, target: number, dt: number, tau: number) =>
  cur + (target - cur) * (1 - Math.exp(-dt / tau))
