import { useEffect, useRef } from "react"

import { runLoop } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// One line of a terminal, asking each question in turn and typing the answer (the
// landing's hero). The typing is decoration; the questions are real text elsewhere
// on the page. The accent is the landing's --ascii-accent, else the signal.
export function AsciiTicker({
  items,
  className,
}: {
  items: { question: string; answer: string; source: string }[]
  className?: string
}) {
  const box = useRef<HTMLDivElement>(null)
  const q = useRef<HTMLSpanElement>(null)
  const a = useRef<HTMLSpanElement>(null)
  const src = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const el = box.current
    if (!el || !q.current || !a.current || !src.current) return
    const [qEl, aEl, sEl] = [q.current, a.current, src.current]
    const each = 7
    const draw = (time: number) => {
      const item = items[Math.floor(time / each) % items.length]
      const t = time % each
      const cursor = Math.floor(time * 3) % 2 === 0 ? "▌" : " "
      const qn = Math.min(item.question.length, Math.floor(t * 28))
      const an = Math.max(0, Math.min(item.answer.length, Math.floor((t - 1.8) * 45)))
      qEl.textContent = item.question.slice(0, qn) + (qn < item.question.length ? cursor : "")
      aEl.textContent = t > 1.6 ? item.answer.slice(0, an) + (an < item.answer.length ? cursor : "") : ""
      sEl.textContent = an >= item.answer.length ? `[${item.source}]` : ""
    }
    const loop = runLoop(el, draw, 4.5)
    return loop.stop
  }, [items])

  return (
    <div ref={box} aria-hidden className={cn("font-mono text-xs leading-relaxed sm:text-sm", className)}>
      <p className="truncate">
        <span className="text-[var(--ascii-accent,var(--ascii-signal))]">&gt; </span>
        <span ref={q} />
      </p>
      <p className="min-h-[2lh] text-muted-foreground sm:min-h-[1lh]">
        <span ref={a} /> <span ref={src} className="text-[var(--ascii-accent,var(--ascii-signal))]" />
      </p>
    </div>
  )
}
