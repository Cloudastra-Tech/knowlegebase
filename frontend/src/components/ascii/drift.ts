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

// Where lines run: the free bands of the empty screen, never across its words. Up
// from the composer to the empty state's last line, one through the composer itself
// (frosted by its glass), and above the heading where there's room. At most `max`,
// the nearest the composer first.
export function lineSlots(
  { top, content, composer }: { top: number; content: Box | null; composer: Box },
  height: number,
  max = 8,
) {
  const gap = clamp(height * 0.07, 40, 58)
  const ys: number[] = [composer.y + composer.h / 2]
  const floor = content ? content.y + content.h + gap * 0.5 : top + gap * 0.5
  for (let y = composer.y - gap * 0.55; y > floor; y -= gap) ys.push(y)
  if (content) for (let y = content.y - gap * 0.6; y > top + 14; y -= gap) ys.push(y)
  const mid = composer.y + composer.h / 2
  return ys.sort((a, b) => Math.abs(a - mid) - Math.abs(b - mid)).slice(0, max)
}

export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

// Frame-rate independent ease towards a target.
export const approach = (cur: number, target: number, dt: number, tau: number) =>
  cur + (target - cur) * (1 - Math.exp(-dt / tau))
