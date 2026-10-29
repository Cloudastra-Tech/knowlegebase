// Text that decodes, typed text and timelines: the pure parts of the landing's ASCII
// scenes (landing-*.tsx), so they're tested as they are. A scene is a function of its
// clock (lib/ascii-loop.ts gives it seconds that only run while it's on screen); these
// turn a moment into the string to show.

import { hash01, redact } from "@/components/ascii/drift"

export { redact }

// Characters a cell flickers through while it decodes: lower case and digits, which
// are about a letter's width in the sans and exactly it in the mono.
export const SCRAMBLE = "abcdefghijklmnopqrstuvwxyz0123456789"

export const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x)

// How far `t` is through the stretch that starts at `at` and lasts `d` seconds, 0 to 1.
export const span = (t: number, at: number, d: number) => (d <= 0 ? (t >= at ? 1 : 0) : clamp01((t - at) / d))

// The cell's flicker at a moment: the same for the same cell and tick (a tick is
// about 1/20 s), so a frame drawn twice is drawn the same.
export function scrambleChar(i: number, tick: number, seed = 0) {
  return SCRAMBLE[Math.floor(hash01(i * 7919 + tick * 104729 + seed * 31) * SCRAMBLE.length)]
}

// `text` part of the way from `from` (its redaction, by default) to itself: left to
// right, the cells before the edge read, the `band` after it flicker, the rest are
// still `from`. Spaces stay spaces, so the words keep their places (`spaces: false`
// for text re-forming from other text, whose spaces are elsewhere). `p` 0 is all
// `from`, 1 is all `text`.
export function reveal(
  text: string,
  p: number,
  tick: number,
  { band = 3, from = redact(text), seed = 0, spaces = true } = {},
) {
  if (p <= 0) return from
  if (p >= 1) return text
  const edge = Math.floor(p * (text.length + band)) - band
  let out = ""
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (i < edge || (spaces && ch === " ")) out += ch
    else if (i < edge + band) out += ch === " " && (from[i] ?? " ") === " " ? " " : scrambleChar(i, tick, seed)
    else out += from[i] ?? " "
  }
  return out
}

// The first `n` characters of `text`, with a block cursor while it's still typing.
export function typed(text: string, n: number, cursor = "▌") {
  const k = Math.max(0, Math.min(text.length, Math.floor(n)))
  return k < text.length ? text.slice(0, k) + cursor : text
}

// Cubic-bezier easing (the motion tokens: lib/motion.ts `ease`), for a scene that
// steps a position by whole cells along a curve.
export function bezier([x1, y1, x2, y2]: readonly number[]) {
  const at = (a: number, b: number, s: number) => 3 * a * s * (1 - s) ** 2 + 3 * b * s * s * (1 - s) + s ** 3
  return (x: number) => {
    if (x <= 0) return 0
    if (x >= 1) return 1
    let lo = 0
    let hi = 1
    for (let i = 0; i < 24; i++) {
      const mid = (lo + hi) / 2
      if (at(x1, x2, mid) < x) lo = mid
      else hi = mid
    }
    return at(y1, y2, (lo + hi) / 2)
  }
}
