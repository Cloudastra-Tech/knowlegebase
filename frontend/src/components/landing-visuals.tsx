import { useEffect, useRef, useState } from "react"
import { AnimatePresence, motion } from "motion/react"
import { Brain, FileText, Lock, Search, ShieldCheck, Upload } from "lucide-react"
import {
  siConfluence,
  siDropbox,
  siGithub,
  siGoogledocs,
  siGoogledrive,
  siGooglesheets,
  siJira,
  siLinear,
  siNotion,
  siOutline,
  siZendesk,
  type SimpleIcon,
} from "simple-icons"

import { reducedMotion, subscribe } from "@/lib/ascii-loop"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

// The landing page's animated pieces: the knowledge hub, the chat demo and the
// permission gate. Each pauses off screen and shows a still frame with reduced motion.

export function BrandIcon({ icon, className }: { icon: SimpleIcon; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={cn("size-5 fill-current", className)}>
      <path d={icon.path} />
    </svg>
  )
}

// ---- One brain, every source ------------------------------------------------------
type HubSource = { id: string; name: string; icon?: SimpleIcon; live?: boolean }

const HUB: HubSource[] = [
  { id: "outline", name: "Outline", icon: siOutline, live: true },
  { id: "drive", name: "Google Drive", icon: siGoogledrive, live: true },
  { id: "notion", name: "Notion", icon: siNotion, live: true },
  { id: "uploads", name: "Uploads", live: true },
  { id: "docs", name: "Google Docs", icon: siGoogledocs },
  { id: "confluence", name: "Confluence", icon: siConfluence },
  { id: "jira", name: "Jira", icon: siJira },
  { id: "github", name: "GitHub", icon: siGithub },
  { id: "sheets", name: "Google Sheets", icon: siGooglesheets },
  { id: "linear", name: "Linear", icon: siLinear },
  { id: "dropbox", name: "Dropbox", icon: siDropbox },
  { id: "zendesk", name: "Zendesk", icon: siZendesk },
]

const HUB_QUESTIONS = [
  { q: "Who approves expenses over $500?", a: "Your manager, then Finance, within 5 working days.", cites: ["outline", "drive"] },
  { q: "How do I get prod database access?", a: "Ask in #infra-access; on-call approves 8 hours.", cites: ["outline", "uploads"] },
  { q: "What discount can I offer?", a: "Up to 15% on annual plans; more needs deal desk.", cites: ["drive", "notion"] },
]

const CYCLE = 8 // seconds per question
const VB = { w: 1000, h: 700, cx: 500, cy: 350, rx: 410, ry: 225 }

function SourceGlyph({ source, className }: { source: HubSource; className?: string }) {
  return source.icon ? (
    <BrandIcon icon={source.icon} className={className} />
  ) : (
    <Upload aria-hidden className={cn("size-5", className)} />
  )
}

// The company's sources orbit one brain; documents stream in along the lines.
// Every few seconds someone asks a question: the sources it reads light up, and
// the answer comes back out with them cited.
export function KnowledgeHub({ className }: { className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const nodes = useRef<(SVGGElement | null)[]>([])
  const lines = useRef<(SVGLineElement | null)[]>([])
  const dots = useRef<(SVGCircleElement | null)[]>([])
  const [step, setStep] = useState({ i: 0, phase: 0 }) // phase: 0 asking, 1 reading, 2 answered

  useEffect(() => {
    const el = box.current
    if (!el) return
    let shown = "0:0" // the first question, being asked (the initial state)
    const draw = (t: number) => {
      const i = Math.floor(t / CYCLE) % HUB_QUESTIONS.length
      const local = t % CYCLE
      const phase = local < 1.4 ? 0 : local < 3.6 ? 1 : 2
      const key = `${i}:${phase}`
      if (key !== shown) {
        shown = key
        setStep({ i, phase })
      }
      const cites = HUB_QUESTIONS[i].cites
      HUB.forEach((s, n) => {
        const a = (n / HUB.length) * Math.PI * 2 + t * 0.035 - Math.PI / 2
        const x = VB.cx + VB.rx * Math.cos(a)
        const y = VB.cy + VB.ry * Math.sin(a)
        const cited = phase >= 1 && cites.includes(s.id)
        nodes.current[n]?.setAttribute("transform", `translate(${x} ${y})`)
        nodes.current[n]?.setAttribute("data-cited", cited ? "1" : "0")
        const line = lines.current[n]
        if (line) {
          line.setAttribute("x1", `${x}`)
          line.setAttribute("y1", `${y}`)
          line.setAttribute("data-cited", cited ? "1" : "0")
        }
        // Two documents per line travel in to the brain; faster on cited lines.
        for (let k = 0; k < 2; k++) {
          const dot = dots.current[n * 2 + k]
          if (!dot) continue
          const speed = cited && phase === 1 ? 0.9 : 0.18
          const p = (t * speed + n * 0.137 + k * 0.5) % 1
          dot.setAttribute("cx", `${x + (VB.cx - x) * p}`)
          dot.setAttribute("cy", `${y + (VB.cy - y) * p}`)
          dot.setAttribute("opacity", s.live || cited ? `${Math.sin(Math.PI * p) * (cited ? 1 : 0.55)}` : "0")
          dot.setAttribute("data-cited", cited ? "1" : "0")
        }
      })
    }
    // The sources in their places before it first scrolls into view (until then the
    // loop draws nothing, and they'd all sit in a corner); then the shared loop, at
    // the hero's 30 fps, while it's on screen.
    draw(0)
    const loop = subscribe(el, draw, { fps: 30, still: 4.5 })
    return loop.stop
  }, [])

  const item = HUB_QUESTIONS[step.i]
  const answered = step.phase === 2
  return (
    <div ref={box} className={cn("relative mx-auto flex w-full max-w-5xl flex-col gap-3", className)}>
      <div className="relative aspect-[1000/700] w-full">
      <svg viewBox={`0 0 ${VB.w} ${VB.h}`} className="absolute inset-0 size-full" aria-hidden>
        <defs>
          <radialGradient id="hub-glow">
            <stop offset="0%" stopColor="var(--signal)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--signal)" stopOpacity="0" />
          </radialGradient>
        </defs>
        <ellipse cx={VB.cx} cy={VB.cy} rx={VB.rx} ry={VB.ry} fill="none" className="stroke-border" strokeDasharray="2 6" />
        <ellipse cx={VB.cx} cy={VB.cy} rx={VB.rx * 0.55} ry={VB.ry * 0.55} fill="none" className="stroke-border/60" strokeDasharray="2 8" />
        {/* The glow swells as the answer comes out: a scale, not its radius, so the
            transition is the GPU's. 700ms: explanatory marketing motion. */}
        <circle
          cx={VB.cx}
          cy={VB.cy}
          r={190}
          fill="url(#hub-glow)"
          className={cn(
            "origin-[500px_350px] transition-[scale] duration-700 ease-out-strong",
            answered ? "scale-100" : "scale-[0.79]",
          )}
        />
        {HUB.map((s, n) => (
          <line
            key={s.id}
            ref={(e) => {
              lines.current[n] = e
            }}
            x2={VB.cx}
            y2={VB.cy}
            className="stroke-border transition-colors duration-500 data-[cited=1]:stroke-signal"
            strokeWidth={1}
          />
        ))}
        {HUB.map((s, n) =>
          [0, 1].map((k) => (
            <circle
              key={`${s.id}-${k}`}
              ref={(e) => {
                dots.current[n * 2 + k] = e
              }}
              r={3}
              className="fill-foreground/70 data-[cited=1]:fill-signal"
            />
          )),
        )}
        {HUB.map((s, n) => (
          <g
            key={s.id}
            ref={(e) => {
              nodes.current[n] = e
            }}
            className="group"
            data-cited="0"
          >
            <circle
              r={34}
              className={cn(
                "fill-background stroke-border transition-colors duration-500 group-data-[cited=1]:stroke-signal",
                !s.live && "opacity-60",
              )}
              strokeWidth={1.5}
            />
            <g
              transform="translate(-12 -12)"
              className={cn(
                "text-foreground/80 transition-colors duration-500 group-data-[cited=1]:text-signal",
                !s.live && "text-foreground/40",
              )}
            >
              {s.icon ? (
                <path d={s.icon.path} className="fill-current" />
              ) : (
                <Upload x={0} y={0} width={24} height={24} />
              )}
            </g>
          </g>
        ))}
      </svg>

      {/* The brain at the centre. */}
      <div className="pointer-events-none absolute top-1/2 left-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center">
        <div className="relative flex size-20 items-center justify-center rounded-full border border-signal/40 bg-background sm:size-28">
          <span className="absolute inset-0 animate-ping rounded-full border border-signal/30 [animation-duration:3s] motion-reduce:hidden" />
          <Brain className="size-9 text-signal sm:size-12" strokeWidth={1.4} />
        </div>
      </div>

      </div>

      {/* The question going in, the answer coming out: above and below the hub on
          a phone, over its open top and bottom on larger screens. */}
      <div className="pointer-events-none order-first flex justify-center px-4 sm:absolute sm:inset-x-0 sm:top-[2%]">
        <AnimatePresence mode="wait">
          <motion.div
            key={`q${step.i}`}
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="flex max-w-full items-center gap-2 rounded-full border bg-background/90 px-4 py-2 text-xs shadow-[0_8px_24px_-12px_rgb(0_0_0/0.6)] backdrop-blur sm:text-sm"
          >
            <Search className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{item.q}</span>
          </motion.div>
        </AnimatePresence>
      </div>
      <div className="pointer-events-none flex min-h-24 items-start justify-center px-4 sm:absolute sm:inset-x-0 sm:bottom-[1%] sm:min-h-0">
        <AnimatePresence mode="wait">
          {answered && (
            <motion.div
              key={`a${step.i}`}
              initial={{ opacity: 0, y: 10, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 6 }}
              transition={{ type: "spring", duration: 0.35, bounce: 0.15 }}
              className="flex max-w-xl flex-col gap-2 rounded-2xl border border-signal/30 bg-background/95 px-4 py-3 text-xs shadow-[0_12px_32px_-16px_rgb(0_0_0/0.7)] backdrop-blur sm:text-sm"
            >
              <span>{item.a}</span>
              <span className="flex flex-wrap gap-1.5">
                {item.cites.map((id, n) => {
                  const s = HUB.find((h) => h.id === id)!
                  return (
                    <span key={id} className="flex items-center gap-1.5 rounded-md bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                      <span className="font-mono text-signal">{n + 1}</span>
                      <SourceGlyph source={s} className="size-3" />
                      {s.name}
                    </span>
                  )
                })}
              </span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <p className="sr-only">
        Your company's sources, Outline, Google Drive, Notion and uploads among them, feed one
        assistant. Ask it "{item.q}" and it answers "{item.a}", citing the sources it read.
      </p>
    </div>
  )
}

// ---- The chat demo ---------------------------------------------------------------
type DemoDoc = { kind: "outline" | "drive" | "notion" | "upload"; title: string; text: string; cited?: string }
export type DemoScene = { id: string; label: string; question: string; answer: string; docs: DemoDoc[] }

const KIND_ICON: Record<DemoDoc["kind"], SimpleIcon | null> = {
  outline: siOutline,
  drive: siGoogledrive,
  notion: siNotion,
  upload: null,
}

function DocGlyph({ kind, className }: { kind: DemoDoc["kind"]; className?: string }) {
  const icon = KIND_ICON[kind]
  return icon ? <BrandIcon icon={icon} className={className} /> : <FileText aria-hidden className={cn("size-5", className)} />
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
          "rounded-sm bg-transparent text-inherit transition-colors duration-700",
          on && "bg-signal/20 text-foreground",
        )}
      >
        {cited}
      </mark>
      {after}
    </>
  )
}

// A question types in, the assistant reads its sources, the answer streams out
// and its citations light the passages they came from. Then the next team's.
export function ChatDemo({ scenes, className }: { scenes: DemoScene[]; className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const [index, setIndex] = useState(0)
  const [visible, setVisible] = useState(false)
  const scene = scenes[index]

  useEffect(() => {
    const el = box.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { threshold: 0.3 })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  return (
    <div ref={box} className={cn("flex flex-col gap-4", className)}>
      <Tabs value={scene.id} onValueChange={(id) => setIndex(scenes.findIndex((s) => s.id === id))}>
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
        onDone={() => setIndex((i) => (i + 1) % scenes.length)}
      />
    </div>
  )
}

type Phase = "typing" | "reading" | "answering" | "cited"

function ChatRun({ scene, playing, onDone }: { scene: DemoScene; playing: boolean; onDone: () => void }) {
  const still = reducedMotion()
  const answerWords = scene.answer.split(" ")
  const [typed, setTyped] = useState(still ? scene.question.length : 0)
  const [phase, setPhase] = useState<Phase>(still ? "cited" : "typing")
  const [words, setWords] = useState(still ? answerWords.length : 0)
  const done = useRef(onDone)
  useEffect(() => {
    done.current = onDone
  })

  useEffect(() => {
    if (still || !playing) return
    const timers: number[] = []
    const at = (ms: number, f: () => void) => timers.push(window.setTimeout(f, ms))
    const q = scene.question.length
    for (let c = 1; c <= q; c++) at(c * 32, () => setTyped(c))
    const read = q * 32 + 250
    at(read, () => setPhase("reading"))
    const answer = read + 1500
    at(answer, () => setPhase("answering"))
    const n = scene.answer.split(" ").length
    for (let w = 1; w <= n; w++) at(answer + w * 55, () => setWords(w))
    at(answer + n * 55 + 200, () => setPhase("cited"))
    at(answer + n * 55 + 4600, () => done.current())
    return () => timers.forEach(clearTimeout)
  }, [still, playing, scene])

  const cited = phase === "cited"
  const citedDocs = scene.docs.filter((d) => d.cited)
  return (
    <>
      <div className="grid overflow-hidden rounded-2xl border bg-muted/20 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        {/* The conversation. */}
        <div className="flex min-h-[340px] flex-col gap-5 p-5 sm:p-7">
          <div className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
            <span className="size-2 rounded-full bg-signal" /> knowledge assistant
          </div>
          <div className="self-end rounded-2xl rounded-br-md bg-foreground px-4 py-2.5 text-sm text-background">
            {scene.question.slice(0, typed)}
            {phase === "typing" && <span className="ml-0.5 animate-pulse">▌</span>}
          </div>
          <div className="flex gap-3">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-signal/15 text-signal">
              <Brain className="size-4" />
            </div>
            <div className="flex min-w-0 flex-col gap-3 pt-1 text-sm leading-relaxed">
              {phase === "typing" && <span className="text-muted-foreground">…</span>}
              {phase === "reading" && (
                <span className="flex items-center gap-2 text-muted-foreground">
                  <Search className="size-3.5 animate-pulse" /> Reading {scene.docs.length} sources…
                </span>
              )}
              {(phase === "answering" || cited) && (
                <p>
                  {answerWords.slice(0, words).join(" ")}
                  {phase === "answering" && <span className="ml-0.5 animate-pulse">▌</span>}
                </p>
              )}
              <AnimatePresence>
                {cited && (
                  <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex flex-wrap gap-1.5">
                    {citedDocs.map((d, n) => (
                      <motion.span
                        key={d.title}
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ delay: n * 0.12 }}
                        className="flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground"
                      >
                        <span className="font-mono text-signal">{n + 1}</span>
                        <DocGlyph kind={d.kind} className="size-3.5" />
                        {d.title}
                      </motion.span>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </div>

        {/* The sources it read. */}
        <div className="flex flex-col gap-3 border-t bg-background/40 p-5 sm:p-7 lg:border-t-0 lg:border-l">
          <p className="font-mono text-xs text-muted-foreground">sources it read</p>
          {scene.docs.map((d, n) => {
            const isCited = !!d.cited && cited
            return (
              <div
                key={d.title}
                className={cn(
                  "relative overflow-hidden rounded-xl border bg-background p-3.5 transition-colors duration-500",
                  isCited && "border-signal/50",
                )}
              >
                {phase === "reading" && (
                  <motion.span
                    aria-hidden
                    className="absolute inset-y-0 w-16 bg-linear-to-r from-transparent via-signal/15 to-transparent"
                    initial={{ left: "-20%" }}
                    animate={{ left: "110%" }}
                    transition={{ duration: 1.1, delay: n * 0.15, repeat: Infinity, ease: "easeInOut" }}
                  />
                )}
                <div className="flex items-center gap-2 text-sm font-medium">
                  <DocGlyph kind={d.kind} className="size-4 text-muted-foreground" />
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

// ---- The permission gate ---------------------------------------------------------
const GATE_DOCS = [
  { title: "handbook.txt", restricted: false },
  { title: "careers-page.md", restricted: false },
  { title: "salaries-2026.xlsx", restricted: true },
]

const PEOPLE = [
  { name: "Pat", role: "Public", hr: false, answer: "Senior engineers are hired on the band published on the careers page." },
  { name: "Hira", role: "HR", hr: true, answer: "Senior engineers: ₹38–46 L, per salaries-2026.xlsx, row 14." },
]

// The same question from two people. Documents travel to the assistant through a
// permission filter; the restricted one is stopped for the person who may not see
// it, before the search, so the AI never receives it.
export function PermissionGate({ className }: { className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const [who, setWho] = useState(0)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const el = box.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { threshold: 0.3 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  useEffect(() => {
    if (!visible || reducedMotion()) return
    const id = window.setInterval(() => setWho((w) => (w + 1) % PEOPLE.length), 5200)
    return () => window.clearInterval(id)
  }, [visible])

  const person = PEOPLE[who]
  return (
    <div ref={box} className={cn("overflow-hidden rounded-2xl border bg-muted/20 p-5 sm:p-8", className)}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-xs text-muted-foreground">asked by</span>
        <Tabs value={person.name} onValueChange={(name) => setWho(PEOPLE.findIndex((p) => p.name === name))}>
          <TabsList aria-label="Who is asking">
            {PEOPLE.map((p) => (
              <TabsTrigger key={p.name} value={p.name} className="gap-2 px-3">
                <span className="flex size-5 items-center justify-center rounded-full bg-muted text-[10px] font-semibold">
                  {p.name[0]}
                </span>
                {p.name} · {p.role}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      <p className="mt-5 text-lg font-medium sm:text-xl">"What's the salary band for a senior engineer?"</p>

      <div className="relative mt-8 grid grid-cols-1 items-center gap-5 sm:grid-cols-[1fr_auto_1fr] sm:gap-6">
        {/* Documents heading for the filter. */}
        <div className="flex flex-col gap-2.5">
          {GATE_DOCS.map((d, n) => {
            const blocked = d.restricted && !person.hr
            return (
              <motion.div
                key={`${who}-${d.title}`}
                initial={{ opacity: 0, x: -16 }}
                animate={
                  blocked
                    ? { opacity: 1, x: [-16, 0, 10, 4, 8, 6], transition: { delay: 0.15 * n, duration: 1.1 } }
                    : { opacity: 1, x: 0, transition: { delay: 0.15 * n, duration: 0.4 } }
                }
                className={cn(
                  "flex items-center gap-2 rounded-lg border bg-background px-3 py-2 text-xs sm:text-sm",
                  blocked && "border-destructive/50 text-destructive",
                )}
              >
                {blocked ? <Lock className="size-3.5 shrink-0" /> : <FileText className="size-3.5 shrink-0 text-muted-foreground" />}
                <span className="truncate">{d.title}</span>
                {blocked && <span className="ml-auto hidden shrink-0 font-mono text-[10px] sm:inline">filtered</span>}
              </motion.div>
            )
          })}
        </div>

        {/* The filter. */}
        <div className="flex items-center gap-3 sm:flex-col sm:gap-2">
          <div className="flex size-14 items-center justify-center rounded-2xl border border-signal/40 bg-background sm:size-16">
            <ShieldCheck className="size-7 text-signal" strokeWidth={1.5} />
          </div>
          <span className="font-mono text-[10px] text-muted-foreground sm:max-w-20 sm:text-center">permission filter</span>
        </div>

        {/* What reaches the assistant, and its answer. */}
        <div className="flex flex-col gap-2.5">
          <div className="flex items-center gap-2 text-xs text-muted-foreground sm:text-sm">
            <Brain className="size-4 text-signal" /> reaches the assistant
          </div>
          <div className="flex flex-wrap gap-1.5">
            {GATE_DOCS.filter((d) => !d.restricted || person.hr).map((d, n) => (
              <motion.span
                key={`${who}-in-${d.title}`}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0, transition: { delay: 0.6 + 0.15 * n } }}
                className="rounded-md bg-muted px-2 py-1 text-[11px] text-muted-foreground"
              >
                {d.title}
              </motion.span>
            ))}
          </div>
          <AnimatePresence mode="wait">
            <motion.p
              key={who}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0, transition: { delay: 1.1 } }}
              exit={{ opacity: 0 }}
              className="rounded-xl border bg-background px-3 py-2.5 text-xs leading-relaxed sm:text-sm"
            >
              {person.answer}
            </motion.p>
          </AnimatePresence>
        </div>
      </div>
      <p className="mt-6 font-mono text-xs text-muted-foreground">
        Example data. Filtering happens before the search, so the AI never receives what someone can't see.
      </p>
    </div>
  )
}

// ---- Logo marquee ----------------------------------------------------------------
export function Marquee({
  items,
  reverse,
  className,
}: {
  items: { name: string; glyph: React.ReactNode; live?: boolean; tag?: string }[]
  reverse?: boolean
  className?: string
}) {
  const row = (hidden: boolean) => (
    <ul aria-hidden={hidden || undefined} className="flex shrink-0 gap-3 pr-3">
      {items.map((it) => (
        <li
          key={it.name}
          className={cn(
            "flex items-center gap-2.5 rounded-xl border bg-background px-4 py-3 text-sm whitespace-nowrap",
            !it.live && "text-muted-foreground",
          )}
        >
          <span className={cn("flex size-5 items-center justify-center", it.live ? "text-foreground" : "text-muted-foreground/70")}>
            {it.glyph}
          </span>
          {it.name}
          {it.tag && (
            <span className={cn("font-mono text-[10px]", it.live ? "text-signal" : "text-muted-foreground/60")}>
              {it.tag}
            </span>
          )}
        </li>
      ))}
    </ul>
  )
  return (
    <div className={cn("group relative flex overflow-hidden [mask-image:linear-gradient(90deg,transparent,black_8%,black_92%,transparent)]", className)}>
      <div
        className={cn(
          "flex animate-[marquee_45s_linear_infinite] group-hover:[animation-play-state:paused] motion-reduce:animate-none",
          reverse && "[animation-direction:reverse]",
        )}
      >
        {row(false)}
        {row(true)}
      </div>
    </div>
  )
}
