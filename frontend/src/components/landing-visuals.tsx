import { useEffect, useRef, useState } from "react"
import { AnimatePresence, motion } from "motion/react"
import { FileText, Lock } from "lucide-react"

import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { ScrambleText } from "@/components/ascii/scramble-text"
import { BrandMark } from "@/components/brand-mark"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { getEffects, reducedMotion } from "@/lib/ascii-loop"
import { dur, ease, stagger } from "@/lib/motion"
import { cn } from "@/lib/utils"

// The landing page's explanatory pieces: the chat demo, the access demo and the
// question log. Each runs only on screen, stops cycling once someone picks a tab,
// and shows its finished state with reduced motion or "Reduce effects".

const still = () => reducedMotion() || getEffects() === "reduced"

// True while the element is at least 30% on screen.
function useOnScreen<T extends Element>() {
  const ref = useRef<T>(null)
  const [on, setOn] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => setOn(e.isIntersecting), { threshold: 0.3 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return [ref, on] as const
}

// ---- The chat demo ---------------------------------------------------------------
type DemoDoc = { brand: string | null; title: string; text: string; cited?: string }
export type DemoScene = { id: string; label: string; question: string; answer: string; docs: DemoDoc[] }

function DocGlyph({ brand, className }: { brand: string | null; className?: string }) {
  return brand ? <BrandMark slug={brand} className={className} /> : <FileText aria-hidden className={cn("size-5", className)} />
}

// Highlights `cited` inside `text` once `on`.
function Passage({ text, cited, on }: { text: string; cited?: string; on: boolean }) {
  if (!cited || !text.includes(cited)) return <>{text}</>
  const [before, after] = text.split(cited)
  return (
    <>
      {before}
      <mark
        className={cn(
          "rounded-sm bg-transparent text-inherit transition-colors duration-(--dur-reveal) ease-hover",
          on && "bg-signal/20 text-foreground",
        )}
      >
        {cited}
      </mark>
      {after}
    </>
  )
}

// A question is sent, the assistant reads its sources, the answer streams out and
// its citations light the passages they came from. Then the next team's, until
// someone picks a team: from then on it plays the one they picked.
export function ChatDemo({ scenes, className }: { scenes: DemoScene[]; className?: string }) {
  const [box, visible] = useOnScreen<HTMLDivElement>()
  const [index, setIndex] = useState(0)
  const [picked, setPicked] = useState(false)
  const scene = scenes[index]

  return (
    <div ref={box} className={cn("flex flex-col gap-4", className)}>
      <Tabs
        panels={false}
        value={scene.id}
        onValueChange={(id) => {
          setPicked(true)
          setIndex(scenes.findIndex((s) => s.id === id))
        }}
      >
        <TabsList aria-label="Example questions">
          {scenes.map((s) => (
            <TabsTrigger key={s.id} value={s.id} className="px-3.5">
              {s.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {/* A fresh run for each scene, and again when it comes back into view. */}
      <ChatRun
        key={`${index}-${visible}`}
        scene={scene}
        playing={visible}
        onDone={() => {
          if (!picked) setIndex((i) => (i + 1) % scenes.length)
        }}
      />
    </div>
  )
}

type Phase = "asked" | "reading" | "answering" | "cited"

function ChatRun({ scene, playing, onDone }: { scene: DemoScene; playing: boolean; onDone: () => void }) {
  const [calm] = useState(still)
  const answerWords = scene.answer.split(" ")
  const [phase, setPhase] = useState<Phase>(calm ? "cited" : "asked")
  const [words, setWords] = useState(calm ? answerWords.length : 0)
  const done = useRef(onDone)
  useEffect(() => {
    done.current = onDone
  })

  useEffect(() => {
    if (calm || !playing) return
    const timers: number[] = []
    const at = (ms: number, f: () => void) => timers.push(window.setTimeout(f, ms))
    const read = 450
    at(read, () => setPhase("reading"))
    const answer = read + 1300
    at(answer, () => setPhase("answering"))
    const n = scene.answer.split(" ").length
    for (let w = 1; w <= n; w++) at(answer + w * 55, () => setWords(w))
    at(answer + n * 55 + 200, () => setPhase("cited"))
    at(answer + n * 55 + 5200, () => done.current())
    return () => timers.forEach(clearTimeout)
  }, [calm, playing, scene])

  const cited = phase === "cited"
  const citedDocs = scene.docs.filter((d) => d.cited)
  return (
    <>
      <div className="grid overflow-hidden rounded-2xl border bg-card/40 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        {/* The conversation. */}
        <div className="flex min-h-80 flex-col gap-5 p-5 sm:p-7">
          {/* Sent whole, as a real message is. */}
          <motion.div
            initial={calm ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: dur.pop, ease: ease.out }}
            className="max-w-[85%] self-end rounded-2xl rounded-br-md bg-foreground px-4 py-2.5 text-sm text-background"
          >
            {scene.question}
          </motion.div>
          <div className="flex min-w-0 flex-col gap-3 text-reading">
            {/* The whole answer holds its room from the start (invisible), and the
                streamed words, the reading line, sit over it: nothing below moves. */}
            <div className="grid">
              <p aria-hidden className="invisible text-pretty [grid-area:1/1]">
                {scene.answer}
              </p>
              {phase === "reading" && (
                <span className="flex items-center gap-2 self-start text-sm text-muted-foreground [grid-area:1/1]">
                  <AsciiSpinner size="xs" label="" /> Reading {scene.docs.length} sources…
                </span>
              )}
              {(phase === "answering" || cited) && (
                <p className="text-pretty [grid-area:1/1]">{answerWords.slice(0, words).join(" ")}</p>
              )}
            </div>
            <div className="flex min-h-7 flex-wrap gap-1.5">
              {cited &&
                citedDocs.map((d, n) => (
                  <motion.span
                    key={d.title}
                    initial={calm ? false : { opacity: 0, transform: "translateY(4px)" }}
                    animate={{ opacity: 1, transform: "translateY(0px)" }}
                    transition={{ duration: dur.pop, ease: ease.out, delay: Math.min(n, stagger.sourcesMax - 1) * stagger.sources }}
                    className="flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground"
                  >
                    <span className="font-mono text-signal">{n + 1}</span>
                    <DocGlyph brand={d.brand} className="size-3.5" />
                    {d.title}
                  </motion.span>
                ))}
            </div>
          </div>
        </div>

        {/* The sources it read. */}
        <div className="flex flex-col gap-3 border-t bg-background/40 p-5 sm:p-7 lg:border-t-0 lg:border-l">
          <p className="font-mono text-xs text-muted-foreground">sources it read</p>
          {scene.docs.map((d) => {
            const isCited = !!d.cited && cited
            return (
              <div
                key={d.title}
                className={cn(
                  "rounded-xl border bg-background p-3.5 transition-colors duration-(--dur-reveal) ease-hover",
                  isCited && "border-signal/50",
                )}
              >
                <div className="flex items-center gap-2 text-sm font-medium">
                  <DocGlyph brand={d.brand} className="size-4 text-muted-foreground" />
                  <span className="truncate">{d.title}</span>
                  {isCited && (
                    <span className="ml-auto font-mono text-xs text-signal">[{citedDocs.indexOf(d) + 1}]</span>
                  )}
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                  <Passage text={d.text} cited={d.cited} on={isCited} />
                </p>
              </div>
            )
          })}
        </div>
      </div>
      <p className="sr-only" aria-live="polite">
        {cited && `Question: ${scene.question} Answer: ${scene.answer}`}
      </p>
    </>
  )
}

// ---- Access: one question, two people ----------------------------------------------
const FILES = [
  { title: "Engineering ladder", brand: "notion", access: "Everyone", hrOnly: false },
  { title: "Careers page", brand: null, access: "Everyone", hrOnly: false },
  { title: "Salaries 2026.xlsx", brand: "googledrive", access: "HR", hrOnly: true },
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

// The same question from two people. The sheet shared with HR alone is dropped
// before the search for the person outside HR, so the model never reads it.
export function AccessDemo({ className }: { className?: string }) {
  const [box, visible] = useOnScreen<HTMLDivElement>()
  const [who, setWho] = useState(0)
  const [picked, setPicked] = useState(false)

  useEffect(() => {
    if (!visible || picked || still()) return
    const id = window.setInterval(() => setWho((w) => (w + 1) % PEOPLE.length), 6000)
    return () => window.clearInterval(id)
  }, [visible, picked])

  const person = PEOPLE[who]
  return (
    <div ref={box} className={cn("flex flex-col gap-5 rounded-2xl border bg-card/40 p-5 sm:p-7", className)}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm text-muted-foreground">Asked by</span>
        <Tabs
          panels={false}
          value={person.name}
          onValueChange={(name) => {
            setPicked(true)
            setWho(PEOPLE.findIndex((p) => p.name === name))
          }}
        >
          <TabsList aria-label="Who is asking">
            {PEOPLE.map((p) => (
              <TabsTrigger key={p.name} value={p.name} className="px-3">
                {p.name}, {p.team}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      <p className="text-lg font-medium text-balance">What do we pay senior engineers?</p>

      <ul className="flex flex-col gap-2" aria-label="Documents in the workspace">
        {FILES.map((f) => {
          const hidden = f.hrOnly && !person.hr
          return (
            <li
              key={f.title}
              className={cn(
                "flex items-center gap-3 rounded-xl border bg-background px-3.5 py-2.5 text-sm transition-[color,border-color] duration-(--dur-popover) ease-hover",
                hidden && "border-dashed text-muted-foreground",
              )}
            >
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
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={who}
            initial={{ opacity: 0, transform: "translateY(6px)" }}
            animate={{ opacity: 1, transform: "translateY(0px)", transition: { duration: dur.reveal, ease: ease.out } }}
            exit={{ opacity: 0, transition: { duration: dur.swap, ease: ease.out } }}
            className="[grid-area:1/1]"
          >
            <GateAnswer person={person} />
          </motion.div>
        </AnimatePresence>
      </div>
      <p className="sr-only" aria-live="polite">
        {`${person.name} from ${person.team} gets: ${person.answer}`}
      </p>
    </div>
  )
}

function GateAnswer({ person, className, hidden }: { person: (typeof PEOPLE)[number]; className?: string; hidden?: boolean }) {
  return (
    <div aria-hidden={hidden || undefined} className={cn("flex flex-col gap-3", className)}>
      <p className="text-sm leading-relaxed text-pretty">{person.answer}</p>
      <span className="flex flex-wrap gap-1.5">
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
  const [box, visible] = useOnScreen<HTMLOListElement>()
  const [masked, setMasked] = useState(still)

  useEffect(() => {
    if (!visible || masked) return
    const id = window.setTimeout(() => setMasked(true), 900)
    return () => window.clearTimeout(id)
  }, [visible, masked])

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
