import { useEffect, useRef, useState } from "react"

import type { BrainGrid, BrainOverlay } from "@/components/ascii/brain-overlay"
import { redact, reveal, span, typed } from "@/components/ascii/decode"
import { DRIFT_SNIPPETS, mulberry32 } from "@/components/ascii/drift"
import { subscribe } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// The hero's loop, in ASCII: a question is typed, documents stream into the brain as
// redacted lines, the two that match decode into readable passages, and an answer
// assembles with [1] [2] markers that point back at them. Then it rests (the brain
// keeps breathing) and asks the next question. The card is decoration: the headline
// and the line under it say the same in words, so it's aria-hidden.
//
// One clock for both halves: the card subscribes to the shared loop, and the brain
// (ascii-brain.tsx) asks this scene for the fragments to draw in its own grid
// (BrainOverlay), so they move on the brain's frames at no extra cost. With reduced
// motion or "Reduce effects" the card shows the first answer whole, and no fragments.

export type HeroItem = {
  question: string
  sources: { label: string; text: string }[] // [1], [2]
  answer: { text: string; cite?: number }[] // a marker after a part, 1-based
}

// The timeline of one question, in seconds.
const TYPE_AT = 0.3
const CPS = 26 // typing, characters a second
const STREAM_AT = 1.1 // the first fragment leaves the edge
const FRAGMENTS = 7
const FRAGMENT_EVERY = 0.26
const MATCH = [2, 4] // which fragments are the matching passages
const DECODE = 0.55 // a passage, left to right
const ANSWER_GAP = 0.35 // after the second passage reads
const PART = 0.5 // a part of the answer decodes
const PART_EVERY = 0.42
const PING = 0.7 // a marker lights its passage's marker
const REST = 6.5 // the finished answer holds, the card drawing nothing
const CLEAR = 0.4 // then the passages redact again, and the next question
const FPS = 30

// Each fragment's speed (cells a second) and length.
const SPEED = { wide: 20, narrow: 13 }
const MAX_LEN = 18

type Fragment = { at: number; row: number; text: string; speed: number; side: 1 | -1; match: boolean }

// When each beat of an item happens.
function beats(item: HeroItem) {
  const typedAt = TYPE_AT + item.question.length / CPS
  // A matching fragment reaches the brain about 1.3s after it leaves the edge.
  const arrive = MATCH.map((i) => STREAM_AT + i * FRAGMENT_EVERY + 1.3)
  const decode = arrive.map((a, i) => Math.max(a, typedAt) + 0.1 + i * 0.12)
  const answerAt = decode[decode.length - 1] + DECODE + ANSWER_GAP
  const parts = item.answer.map((_, i) => answerAt + i * PART_EVERY)
  const done = parts[parts.length - 1] + PART + 0.2
  return { typedAt, arrive, decode, parts, done }
}

// The fragments of item `n`'s stream: made-up records, redacted, and the two passages
// it'll quote (in the signal). The same every time for the same item.
function fragmentsOf(item: HeroItem, n: number, wide: boolean): Fragment[] {
  const rand = mulberry32(97 + n * 13)
  const speed = wide ? SPEED.wide : SPEED.narrow
  const out: Fragment[] = []
  for (let i = 0; i < FRAGMENTS; i++) {
    const m = MATCH.indexOf(i)
    const source = m >= 0 ? item.sources[m]?.text : DRIFT_SNIPPETS[Math.floor(rand() * DRIFT_SNIPPETS.length)]
    out.push({
      at: STREAM_AT + i * FRAGMENT_EVERY,
      row: m >= 0 ? (m === 0 ? -0.18 : 0.2) : rand() * 1.1 - 0.55,
      text: redact((source ?? "").slice(0, MAX_LEN).trim()),
      speed: speed * (m >= 0 ? 0.92 : 0.85 + rand() * 0.3),
      side: wide || i % 2 === 0 ? 1 : -1,
      match: m >= 0,
    })
  }
  return out
}

// Draws the fragments in flight at item time `t` into the brain's grid: each comes in
// from the edge on its side, a cell at a time, and is gone where it meets the brain.
function drawFragments(g: BrainGrid, frags: Fragment[], t: number) {
  for (const f of frags) {
    const age = t - f.at
    if (age < 0) continue
    const r = Math.round(g.cy + f.row * g.ry)
    const moved = Math.floor(age * f.speed)
    const len = f.text.length
    const edge = f.side === 1 ? g.cols : -1
    const head = edge - f.side * moved // the cell nearest the brain
    if (f.side === 1 ? head + len < g.cx : head - len > g.cx) continue // swallowed
    for (let i = 0; i < len; i++) {
      const c = head + f.side * i
      // Past the brain's middle (a row it's thin on): gone, as into it.
      if (f.side === 1 ? c <= g.cx : c >= g.cx) continue
      if (g.brain(c, r)) continue
      const ch = f.text[f.side === 1 ? i : len - 1 - i]
      if (ch !== " ") g.put(c, r, ch, f.match)
    }
  }
}

// `stream`: the brain's overlay (one BrainOverlay shared with the hero's AsciiBrain);
// the card keeps it drawing the fragments of its moment.
export function HeroLoop({ items, stream, className }: { items: HeroItem[]; stream: BrainOverlay; className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const q = useRef<HTMLSpanElement>(null)
  const rows = useRef<(HTMLParagraphElement | null)[]>([])
  const texts = useRef<(HTMLSpanElement | null)[]>([])
  const parts = useRef<(HTMLSpanElement | null)[]>([])
  const marks = useRef<(HTMLSpanElement | null)[]>([])
  const [index, setIndex] = useState(0)
  const item = items[index]

  useEffect(() => {
    const el = box.current
    if (!el || !q.current) return
    const b = beats(item)
    const cycle = b.done + CLEAR
    let wide: boolean | null = null
    let frags: Fragment[] = []
    let now = -1 // the card's moment, which the brain's frames read
    let rested = false
    let advanced = false
    const shown: string[] = []
    const write = (k: number, node: HTMLElement | null | undefined, text: string) => {
      if (!node || shown[k] === text) return
      shown[k] = text
      node.textContent = text
    }
    const setOn = (node: HTMLElement | null | undefined, on: boolean) => {
      if (node && (node.dataset.on === "") !== on) {
        if (on) node.dataset.on = ""
        else delete node.dataset.on
      }
    }

    // The whole frame at item time t; t < 0 is the finished answer (the still frame).
    const draw = (time: number) => {
      const t = time < 0 ? b.done : time
      now = time < 0 ? -1 : t
      const tick = Math.floor(t * 20)
      write(0, q.current, typed(item.question, (t - TYPE_AT) * CPS))
      const clearing = span(t, b.done, CLEAR)
      item.sources.forEach((s, i) => {
        const arrived = t >= b.arrive[i]
        const p = span(t, b.decode[i], DECODE) * (1 - clearing)
        setOn(rows.current[i], arrived && clearing < 1)
        write(1 + i, texts.current[i], arrived ? reveal(s.text, p, tick, { seed: i }) : "")
      })
      item.answer.forEach((part, i) => {
        const p = span(t, b.parts[i], PART)
        // Always the part's length (its redaction until it decodes), shown once it starts:
        // the markers after it never move.
        const shownPart = clearing === 0 && p > 0
        write(4 + i, parts.current[i], reveal(part.text, shownPart ? p : 0, tick, { seed: 9 + i }))
        setOn(parts.current[i], shownPart)
        if (part.cite) {
          const shownMark = p >= 1 && clearing === 0
          setOn(marks.current[i], shownMark)
          // The marker lights the passage it points at, once, as it appears.
          const pinging = shownMark && t < b.parts[i] + PART + PING
          setOn(rows.current[part.cite - 1]?.querySelector<HTMLElement>("[data-mark]"), pinging)
        }
      })
      if (time < 0) return
      // Finished: hold it, drawing nothing, then the next question.
      if (!rested && t >= b.done) {
        rested = true
        loop.rest(REST * 1000)
        return
      }
      if (t >= cycle && !advanced) {
        advanced = true
        setIndex((i) => (i + 1) % items.length)
      }
    }

    stream.set((g) => {
      if (now < 0 || now > b.done) return
      if (wide !== g.wide) {
        wide = g.wide
        frags = fragmentsOf(item, index, g.wide)
      }
      drawFragments(g, frags, now)
    })
    // A new item starts its clock from zero: a fresh subscription.
    const loop = subscribe(el, draw, { fps: FPS, still: -1, priority: "hero" })
    return () => {
      loop.stop()
      stream.set(null)
    }
  }, [item, index, items.length, stream])

  return (
    <div
      ref={box}
      aria-hidden
      data-slot="hero-loop"
      className={cn("font-mono text-xs leading-relaxed [contain:content] sm:text-sm", className)}
    >
      <p className="truncate">
        <span className="text-signal">&gt; </span>
        <span ref={q} />
      </p>
      {item.sources.map((s, i) => (
        <p
          key={i}
          ref={(n) => {
            rows.current[i] = n
          }}
          className="flex min-w-0 gap-2 opacity-0 transition-opacity duration-(--dur-swap) ease-out-strong data-on:opacity-100"
        >
          <span
            data-mark
            className="shrink-0 rounded-sm text-signal transition-colors duration-(--dur-reveal) ease-hover data-on:bg-signal/20"
          >
            [{i + 1}]
          </span>
          <span className="shrink-0 text-muted-foreground">{s.label}</span>
          <span
            ref={(n) => {
              texts.current[i] = n
            }}
            className="min-w-0 truncate"
          />
        </p>
      ))}
      <p className="mt-1 min-h-[2lh] text-pretty">
        {item.answer.map((part, i) => (
          <span key={i}>
            <span
              ref={(n) => {
                parts.current[i] = n
              }}
              className="invisible data-on:visible"
            >
              {redact(part.text)}
            </span>
            {part.cite && (
              <span
                ref={(n) => {
                  marks.current[i] = n
                }}
                className="text-signal opacity-0 transition-opacity duration-(--dur-pop) ease-out-strong data-on:opacity-100"
              >
                {` [${part.cite}]`}
              </span>
            )}
          </span>
        ))}
      </p>
    </div>
  )
}
