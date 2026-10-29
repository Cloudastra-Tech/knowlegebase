import { useEffect, useMemo, useRef, useState } from "react"
import { FileText, Lock } from "lucide-react"

import { redact, reveal, span } from "@/components/ascii/decode"
import { ScrambleText } from "@/components/ascii/scramble-text"
import { BrandMark } from "@/components/brand-mark"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { subscribe } from "@/lib/ascii-loop"
import { SEEN, still, usePickedBy, writer } from "@/components/landing-scene"
import { cn } from "@/lib/utils"

// The landing page's explanatory pieces: the chat demo, the access demo and the
// question log. Each is a scene on the shared ASCII loop (lib/ascii-loop.ts): a
// function of a clock that runs only while the piece is on screen (from about a
// third of it in view) and the tab is in front, writing the few characters and
// attributes that change straight into the page, never React state per frame. Each
// shows its finished state with reduced motion or "Reduce effects". The tabs stop
// the cycling; picked from the keyboard, the new state is there at once.


// ---- The chat demo ---------------------------------------------------------------
type DemoDoc = { brand: string | null; title: string; text: string; cited?: string }
export type DemoScene = {
  id: string
  label: string
  question: string
  answer: string
  docs: DemoDoc[]
  others: { brand: string | null; title: string; text: string }[] // read, not cited
}

function DocGlyph({ brand, className }: { brand: string | null; className?: string }) {
  return brand ? <BrandMark slug={brand} className={className} /> : <FileText aria-hidden className={cn("size-5", className)} />
}

// A question is sent, and the search reads down a column of passages: a cursor
// steps from one to the next, a band of flicker passes over each, and the ones it
// cites lock in (they decode, the phrase it quotes is marked, and they get their
// [n]) while the rest stay redacted. Then the answer streams in, its chips pointing
// at the passages they came from. Then the next team's, until someone picks a team.
export function ChatDemo({ scenes, className }: { scenes: DemoScene[]; className?: string }) {
  const [index, setIndex] = useState(0)
  const [picked, setPicked] = useState<false | "pointer" | "keyboard">(false)
  const { by, listeners } = usePickedBy()
  const scene = scenes[index]

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <Tabs
        panels={false}
        value={scene.id}
        onValueChange={(id) => {
          setPicked(by.current)
          setIndex(scenes.findIndex((s) => s.id === id))
        }}
      >
        <TabsList aria-label="Example questions" {...listeners}>
          {scenes.map((s) => (
            <TabsTrigger key={s.id} value={s.id} className="px-3.5">
              {s.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <ChatRun
        key={scene.id}
        scene={scene}
        instant={picked === "keyboard"}
        last={!!picked}
        onDone={() => setIndex((i) => (i + 1) % scenes.length)}
      />
    </div>
  )
}

// The chat run's timeline, in seconds.
const ASKED = 0.05
const SCAN_AT = 0.55 // the cursor reaches the first passage
const STEP = 0.42 // to the next
const READ = 0.5 // a band passes over a passage
const WORD = 0.055 // the answer streams a word at a time
const CHAT_HOLD = 5 // the finished answer holds before the next team's

// The passages the search reads, in the order it reads them: the cited ones and the
// others between them.
function passagesOf(scene: DemoScene) {
  const docs: (DemoDoc & { cite: number })[] = []
  let n = 0
  scene.docs.forEach((d, i) => {
    docs.push({ ...d, cite: d.cited ? ++n : 0 })
    const other = scene.others[i]
    if (other) docs.push({ ...other, cite: 0 })
  })
  return docs
}

function ChatRun({ scene, instant, last, onDone }: { scene: DemoScene; instant: boolean; last: boolean; onDone: () => void }) {
  const box = useRef<HTMLDivElement>(null)
  const bubble = useRef<HTMLDivElement>(null)
  const status = useRef<HTMLSpanElement>(null)
  const answer = useRef<HTMLParagraphElement>(null)
  const cursor = useRef<HTMLSpanElement>(null)
  const rows = useRef<(HTMLDivElement | null)[]>([])
  const chips = useRef<(HTMLSpanElement | null)[]>([])
  const done = useRef(onDone)
  useEffect(() => {
    done.current = onDone
  })
  const passages = useMemo(() => passagesOf(scene), [scene])
  const cited = passages.filter((p) => p.cite)
  const words = useMemo(() => scene.answer.split(" "), [scene])

  useEffect(() => {
    const el = box.current
    if (!el) return
    const w = writer()
    const scanEnd = SCAN_AT + (passages.length - 1) * STEP + READ
    const answerAt = scanEnd + 0.15
    const answerEnd = answerAt + words.length * WORD
    const end = answerEnd + 0.3
    let rested = false
    let finished = false
    let on = -1 // the passage the cursor is on

    const draw = (time: number) => {
      const t = time < 0 ? end : time
      const tick = Math.floor(t * 20)
      w.on(bubble.current, t >= ASKED)
      w.on(status.current, t >= SCAN_AT - 0.2 && t < answerAt)
      // The cursor sits on the passage being read, and leaves when the reading's done.
      const at = Math.min(passages.length - 1, Math.max(0, Math.floor((t - SCAN_AT) / STEP)))
      const row = rows.current[at]
      if (cursor.current && row && at !== on) {
        on = at
        cursor.current.style.transform = `translateY(${row.offsetTop + 11}px)`
      }
      w.on(cursor.current, t >= SCAN_AT - 0.1 && t < scanEnd)
      passages.forEach((p, i) => {
        const node = rows.current[i]
        if (!node) return
        const from = SCAN_AT + i * STEP
        const k = span(t, from, READ)
        const text = node.querySelectorAll<HTMLElement>("[data-part]")
        // Cited: the band leaves the passage readable. The rest: redacted again.
        const target = p.cite ? p.text : redact(p.text)
        const shown = reveal(target, k, tick, { from: redact(p.text), seed: i })
        const [a, b] = split(p.text, p.cite ? (p as DemoDoc).cited : undefined)
        w.text(text[0], shown.slice(0, a))
        w.text(text[1], shown.slice(a, b))
        w.text(text[2], shown.slice(b))
        w.state(node, k <= 0 ? "waiting" : k < 1 ? "reading" : p.cite ? "cited" : "read")
      })
      w.text(answer.current, t < answerAt ? "" : words.slice(0, Math.ceil((t - answerAt) / WORD)).join(" "))
      chips.current.forEach((c) => w.on(c, t >= answerEnd))
      if (time < 0) return
      if (t >= end && !rested) {
        rested = true
        if (last) queueMicrotask(() => loop.stop())
        else loop.rest(CHAT_HOLD * 1000)
        return
      }
      if (rested && !finished) {
        finished = true
        done.current()
      }
    }
    if (instant) {
      draw(-1)
      return
    }
    const loop = subscribe(el, draw, { fps: 30, still: -1, priority: "hero", visibleAt: SEEN })
    return () => loop.stop()
  }, [instant, last, passages, words])

  return (
    <div ref={box} className="grid overflow-hidden rounded-2xl border bg-card/40 [contain:content] lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      {/* The conversation. */}
      <div className="flex min-h-80 flex-col gap-5 p-5 sm:p-7">
        <div
          ref={bubble}
          className="max-w-[85%] self-end rounded-2xl rounded-br-md bg-foreground px-4 py-2.5 text-sm text-background opacity-0 transition-opacity duration-(--dur-pop) ease-out-strong data-on:opacity-100"
        >
          {scene.question}
        </div>
        <div className="flex min-w-0 flex-col gap-3 text-reading">
          {/* The whole answer holds its room from the start (invisible); the streamed
              words and the reading line sit over it: nothing below moves. */}
          <div aria-hidden className="grid">
            <p className="invisible text-pretty [grid-area:1/1]">{scene.answer}</p>
            <span
              ref={status}
              className="self-start font-mono text-xs text-muted-foreground opacity-0 transition-opacity duration-(--dur-swap) ease-out-strong [grid-area:1/1] data-on:opacity-100"
            >
              reading {passages.length} passages…
            </span>
            <p ref={answer} className="text-pretty [grid-area:1/1]" />
          </div>
          <div aria-hidden className="flex min-h-7 flex-wrap gap-1.5">
            {cited.map((d, n) => (
              <span
                key={d.title}
                ref={(node) => {
                  chips.current[n] = node
                }}
                style={{ transitionDelay: `${n * 30}ms` }}
                className="flex translate-y-1 items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground opacity-0 transition-[opacity,transform] duration-(--dur-pop) ease-out-strong data-on:translate-y-0 data-on:opacity-100"
              >
                <span className="font-mono text-signal">{n + 1}</span>
                <DocGlyph brand={d.brand} className="size-3.5" />
                {d.title}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* The passages it read, as the search reads them. */}
      <div aria-hidden className="relative flex flex-col gap-2 border-t bg-background/40 p-5 pl-7 sm:p-7 sm:pl-9 lg:border-t-0 lg:border-l">
        <p className="font-mono text-xs text-muted-foreground">sources it read</p>
        <span
          ref={cursor}
          className="absolute top-0 left-2.5 font-mono text-xs text-muted-foreground opacity-0 transition-[opacity,transform] duration-(--dur-step) ease-out-strong data-on:opacity-100 sm:left-4"
        >
          ▸
        </span>
        {passages.map((p, i) => (
          <div
            key={p.title}
            ref={(node) => {
              rows.current[i] = node
            }}
            data-state="waiting"
            className="group/p rounded-xl border bg-background px-3 py-2.5 transition-colors duration-(--dur-reveal) ease-hover data-[state=cited]:border-signal/50 data-[state=reading]:border-foreground/25"
          >
            <div className="flex items-center gap-2 text-sm">
              <DocGlyph brand={p.brand} className="size-4 shrink-0 text-muted-foreground" />
              <span className="truncate text-muted-foreground transition-colors duration-(--dur-reveal) ease-hover group-data-[state=cited]/p:text-foreground">
                {p.title}
              </span>
              {p.cite > 0 && (
                <span className="ml-auto font-mono text-xs text-signal opacity-0 transition-opacity duration-(--dur-pop) ease-out-strong group-data-[state=cited]/p:opacity-100">
                  [{p.cite}]
                </span>
              )}
            </div>
            <p className="mt-1 font-mono text-xs leading-relaxed text-muted-foreground">
              <span data-part>{redact(p.text)}</span>
              <mark
                data-part
                className="rounded-sm bg-transparent text-inherit transition-colors duration-(--dur-reveal) ease-hover group-data-[state=cited]/p:bg-signal/20 group-data-[state=cited]/p:text-foreground"
              />
              <span data-part />
            </p>
          </div>
        ))}
      </div>
      <p className="sr-only">{`Example. Question: ${scene.question} Answer: ${scene.answer} Cited: ${cited.map((d) => d.title).join(", ")}.`}</p>
    </div>
  )
}

// Where the quoted phrase starts and ends in a passage (an empty stretch for none).
function split(text: string, cited?: string): [number, number] {
  const at = cited ? text.indexOf(cited) : -1
  return at < 0 ? [text.length, text.length] : [at, at + (cited?.length ?? 0)]
}

// ---- Access: one question, two people ----------------------------------------------
const FILES = [
  { title: "Engineering ladder", brand: "notion", access: "Everyone", hrOnly: false, lines: ["L4 · senior engineer · leads a team"] },
  { title: "Careers page", brand: null, access: "Everyone", hrOnly: false, lines: ["pay: “competitive, with equity”"] },
  {
    title: "Salaries 2026.xlsx",
    brand: "googledrive",
    access: "HR",
    hrOnly: true,
    lines: ["L3 · ₹24 to 30 lakh", "L4 · ₹38 to 46 lakh", "L5 · ₹52 to 60 lakh"],
  },
]

const PEOPLE = [
  {
    name: "Pat",
    team: "Engineering",
    hr: false,
    answer: "Pay for senior engineers isn't in the documents you can see. The careers page lists the role as “competitive, with equity”.",
    cites: ["Careers page"],
  },
  {
    name: "Hira",
    team: "HR",
    hr: true,
    answer: "Senior engineers are paid ₹38 to 46 lakh a year, by the 2026 salary sheet, at level L4 on the ladder.",
    cites: ["Salaries 2026.xlsx", "Engineering ladder"],
  },
]

// The access run's timeline, in seconds.
const LINE_AT = 0.3 // the first line starts to decode
const LINE_EVERY = 0.14
const CHAR = 0.028 // character by character
const REDACT = 0.3 // a line closes again, right to left
const REFORM = 0.7 // the answer re-forms
const ACCESS_HOLD = 5.5

// The same question from two people. The sheet shared with HR alone is dropped
// before the search for the person outside HR, so the model never reads it: its rows
// stay redacted for Pat, and decode character by character for Hira. The answer
// re-forms from one person's into the other's through a scramble.
export function AccessDemo({ className }: { className?: string }) {
  // Who's asking, and who asked before (null at first: every line starts redacted).
  const [{ who, from }, setView] = useState<{ who: number; from: number | null }>({ who: 0, from: null })
  const [picked, setPicked] = useState<false | "pointer" | "keyboard">(false)
  const { by, listeners } = usePickedBy()
  const person = PEOPLE[who]

  return (
    <div className={cn("flex flex-col gap-5 rounded-2xl border bg-card/40 p-5 sm:p-7", className)}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm text-muted-foreground">Asked by</span>
        <Tabs
          panels={false}
          value={person.name}
          onValueChange={(name) => {
            setPicked(by.current)
            setView({ who: PEOPLE.findIndex((p) => p.name === name), from: who })
          }}
        >
          <TabsList aria-label="Who is asking" {...listeners}>
            {PEOPLE.map((p) => (
              <TabsTrigger key={p.name} value={p.name} className="px-3">
                {p.name}, {p.team}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      <p className="text-lg font-medium text-balance">What do we pay senior engineers?</p>
      <AccessRun
        key={who}
        who={who}
        from={from}
        instant={picked === "keyboard"}
        last={!!picked}
        onDone={() => setView((v) => ({ who: (v.who + 1) % PEOPLE.length, from: v.who }))}
      />
    </div>
  )
}

function AccessRun({
  who,
  from,
  instant,
  last,
  onDone,
}: {
  who: number
  from: number | null
  instant: boolean
  last: boolean
  onDone: () => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const lines = useRef<(HTMLSpanElement | null)[]>([])
  const answer = useRef<HTMLParagraphElement>(null)
  const chips = useRef<HTMLSpanElement>(null)
  const done = useRef(onDone)
  useEffect(() => {
    done.current = onDone
  })
  const person = PEOPLE[who]
  const before = from === null ? null : PEOPLE[from]

  useEffect(() => {
    const el = box.current
    if (!el) return
    const w = writer()
    // Each line: when it starts to change, and from what to what. The lines a person
    // can open decode; a line that was open and isn't now closes again; a line that
    // stays as it was doesn't move.
    const all = FILES.flatMap((f) => f.lines.map((text) => ({ text, hrOnly: f.hrOnly })))
    const open = (p: (typeof PEOPLE)[number] | null, hrOnly: boolean) => !!p && (!hrOnly || p.hr)
    let next = LINE_AT
    const plan = all.map((line) => {
      const was = open(before, line.hrOnly)
      const now = open(person, line.hrOnly)
      const at = was === now ? 0 : next
      if (was !== now) next += LINE_EVERY
      return { ...line, was, now, at, d: now ? line.text.length * CHAR : REDACT }
    })
    const settled = Math.max(LINE_AT, ...plan.map((l) => (l.was === l.now ? 0 : l.at + l.d)))
    const answerAt = settled + 0.15
    const old = before?.answer ?? ""
    const size = Math.max(old.length, person.answer.length)
    const end = answerAt + REFORM + 0.2
    let rested = false
    let finished = false

    const draw = (time: number) => {
      const t = time < 0 ? end : time
      const tick = Math.floor(t * 20)
      plan.forEach((l, i) => {
        let shown: string
        if (l.was === l.now) shown = l.now ? l.text : redact(l.text)
        else if (l.now) shown = reveal(l.text, span(t, l.at, l.d), tick, { band: 1, seed: i })
        else shown = reveal(l.text, 1 - span(t, l.at, l.d), tick, { band: 2, seed: i })
        w.text(lines.current[i], shown)
      })
      // The answer: the last person's, re-forming left to right into this one's.
      const k = span(t, answerAt, REFORM)
      const text =
        k >= 1 ? person.answer : reveal(person.answer.padEnd(size), k, tick, { band: 6, from: old.padEnd(size), seed: 3, spaces: false }).trimEnd()
      w.text(answer.current, text)
      w.on(chips.current, k >= 1)
      if (time < 0) return
      if (t >= end && !rested) {
        rested = true
        if (last) queueMicrotask(() => loop.stop())
        else loop.rest(ACCESS_HOLD * 1000)
        return
      }
      if (rested && !finished) {
        finished = true
        done.current()
      }
    }
    if (instant) {
      draw(-1)
      return
    }
    const loop = subscribe(el, draw, { fps: 30, still: -1, priority: "hero", visibleAt: SEEN })
    return () => loop.stop()
  }, [person, before, instant, last])

  let line = 0
  return (
    <div ref={box} className="flex flex-col gap-5 [contain:content]">
      <ul className="flex flex-col gap-2" aria-label="Documents in the workspace">
        {FILES.map((f) => {
          const hidden = f.hrOnly && !person.hr
          return (
            <li
              key={f.title}
              className={cn(
                "rounded-xl border bg-background px-3.5 py-2.5 text-sm transition-[color,border-color] duration-(--dur-popover) ease-hover",
                hidden && "border-dashed text-muted-foreground",
              )}
            >
              <div className="flex items-center gap-3">
                {hidden ? (
                  <Lock aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                ) : (
                  <DocGlyph brand={f.brand} className="size-4 shrink-0 text-muted-foreground" />
                )}
                <span className="min-w-0 truncate">{f.title}</span>
                <span className="ml-auto flex shrink-0 items-center gap-2">
                  {hidden && <span className="hidden text-xs text-muted-foreground sm:inline">Left out for {person.name}</span>}
                  <Badge variant="mono">{f.access}</Badge>
                </span>
              </div>
              <div aria-hidden className="mt-1.5 flex flex-col pl-7 font-mono text-xs leading-relaxed text-muted-foreground">
                {f.lines.map((text) => {
                  const i = line++
                  return (
                    <span
                      key={text}
                      ref={(node) => {
                        lines.current[i] = node
                      }}
                      className="truncate whitespace-pre"
                    >
                      {redact(text)}
                    </span>
                  )
                })}
              </div>
            </li>
          )
        })}
      </ul>

      {/* Every answer sits invisibly in the same cell, so the box is the tallest's
          size from the start and a swap never moves what's below. */}
      <div className="grid rounded-xl border border-signal/30 bg-background p-4">
        {PEOPLE.map((p) => (
          <GateAnswer key={p.name} person={p} className="invisible [grid-area:1/1]" hidden />
        ))}
        <div aria-hidden className="flex flex-col gap-3 [grid-area:1/1]">
          <p ref={answer} className="text-sm leading-relaxed text-pretty">
            {before?.answer ?? ""}
          </p>
          <span
            ref={chips}
            className="flex translate-y-1 flex-wrap gap-1.5 opacity-0 transition-[opacity,transform] duration-(--dur-pop) ease-out-strong data-on:translate-y-0 data-on:opacity-100"
          >
            <Cites person={person} />
          </span>
        </div>
      </div>
      <p className="sr-only">{`${person.name} from ${person.team} gets: ${person.answer}`}</p>
    </div>
  )
}

function Cites({ person }: { person: (typeof PEOPLE)[number] }) {
  return (
    <>
      {person.cites.map((title, n) => {
        const f = FILES.find((x) => x.title === title)!
        return (
          <span key={title} className="flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs text-muted-foreground">
            <span className="font-mono text-signal">{n + 1}</span>
            <DocGlyph brand={f.brand} className="size-3" />
            {title}
          </span>
        )
      })}
    </>
  )
}

function GateAnswer({ person, className, hidden }: { person: (typeof PEOPLE)[number]; className?: string; hidden?: boolean }) {
  return (
    <div aria-hidden={hidden || undefined} className={cn("flex flex-col gap-3", className)}>
      <p className="text-sm leading-relaxed text-pretty">{person.answer}</p>
      <span className="flex flex-wrap gap-1.5">
        <Cites person={person} />
      </span>
    </div>
  )
}

// ---- The question log, masked ------------------------------------------------------
// The real mask (pii.py writes [REDACTED_EMAIL], [REDACTED_PHONE] ...).
type LogRow = { time: string; via: string; before: string; pii?: string; mask?: string; after?: string }

const LOG: LogRow[] = [
  { time: "09:14", via: "Slack", before: "Can ", pii: "m.lopez@acme.com", mask: "[REDACTED_EMAIL]", after: " approve my March claim?" },
  { time: "09:02", via: "Teams", before: "Who's on call for payments? Text me on ", pii: "+44 20 7946 0412", mask: "[REDACTED_PHONE]" },
  { time: "08:47", via: "Web", before: "What discount can I offer on an annual plan?" },
]

// A few questions as the log keeps them. The personal data in them is shown as it
// was asked, then scrambles into the mask the log stores: that's what happens
// before a row is written. Still: masked from the start.
export function QuestionLog({ className }: { className?: string }) {
  const box = useRef<HTMLOListElement>(null)
  const [masked, setMasked] = useState(still)

  // Once it has been on screen for 0.9s (the loop's clock), it masks.
  useEffect(() => {
    const el = box.current
    if (!el || masked) return
    const loop = subscribe(
      el,
      (t) => {
        if (t < 0.9) return
        queueMicrotask(() => loop.stop())
        setMasked(true)
      },
      { fps: 10, still: -1, priority: "hero", visibleAt: SEEN },
    )
    return () => loop.stop()
  }, [masked])

  return (
    <ol ref={box} aria-label="The question log" className={cn("flex flex-col divide-y rounded-2xl border bg-card font-mono text-xs", className)}>
      {LOG.map((r) => (
        <li key={r.time} className="grid grid-cols-[3rem_3.5rem_minmax(0,1fr)] gap-x-3 px-4 py-3 sm:grid-cols-[3.5rem_4rem_minmax(0,1fr)] sm:px-5">
          <span className="text-muted-foreground tabular-nums">{r.time}</span>
          <span className="text-muted-foreground">{r.via}</span>
          <span className="leading-relaxed text-foreground">
            {r.before}
            {r.pii && r.mask && (
              // Both values share one cell (and, in mono, the same 16 characters), so
              // the row never changes size.
              <span className="inline-grid align-bottom whitespace-nowrap">
                <span aria-hidden={masked} className={cn("[grid-area:1/1]", masked && "invisible")}>
                  {r.pii}
                </span>
                <span aria-hidden={!masked} className={cn("[grid-area:1/1] text-signal", !masked && "invisible")}>
                  {masked ? <ScrambleText text={r.mask} /> : r.mask}
                </span>
              </span>
            )}
            {r.after}
          </span>
        </li>
      ))}
    </ol>
  )
}
