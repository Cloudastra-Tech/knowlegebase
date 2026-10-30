import type { ReactNode } from "react"

import { SWAP, SWAP_OFF, useActiveStep, useWide } from "@/components/landing-scene"
import { cn } from "@/lib/utils"

// A landing section told in steps as the page is scrolled (it used to be tabs, which
// nobody stops to pick). From lg up: the steps down one column, and beside them a
// visual in a sticky frame, held in the middle of the window, showing the state of
// the step crossing the middle of the window (useActiveStep). Below lg nothing sticks:
// each step is followed by its own visual, each playing as it comes into view. Every
// step's words are in the page, in order, at any width; the visual's state is also a
// line of text, announced politely when it changes. Nothing to click or tab to.

export type Step = { id: string; label: ReactNode; title: string; body: ReactNode }

// One of several states stacked in one cell: the cell is the largest's size, so a
// swap never moves anything. Off, it's hidden from assistive tech and can't be focused.
export function Layer({ on, className, children }: { on: boolean; className?: string; children: ReactNode }) {
  return (
    <div
      data-current={on || undefined}
      aria-hidden={!on || undefined}
      inert={!on}
      className={cn("[grid-area:1/1]", SWAP, !on && SWAP_OFF, className)}
    >
      {children}
    </div>
  )
}

const FADE = "transition-opacity duration-(--dur-reveal) ease-out-strong motion-reduce:duration-(--dur-swap)"

// Dimmed, a step still reads at 4.5:1: the title at 52%, the muted words at 75%.
function StepText({ step, dim }: { step: Step; dim: boolean }) {
  return (
    <>
      <div className={cn("flex items-center gap-2 font-mono text-xs text-muted-foreground", FADE, dim && "opacity-75")}>
        {step.label}
      </div>
      <h3 className={cn("mt-3 text-xl font-medium tracking-tight text-balance", FADE, dim && "opacity-52")}>{step.title}</h3>
      <p className={cn("mt-2 max-w-[48ch] text-pretty text-muted-foreground", FADE, dim && "opacity-75")}>{step.body}</p>
    </>
  )
}

export function ScrollSteps({
  head,
  steps,
  visual,
  stepVisual,
  caption,
  columns = "lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]",
  className,
}: {
  head: ReactNode // the section's title, over the steps
  steps: Step[]
  visual: (active: number) => ReactNode // the sticky visual, from lg
  stepVisual: (i: number) => ReactNode // below lg, under each step
  caption: (i: number) => string // a state in words: over the visual, and announced
  columns?: string
  className?: string
}) {
  const wide = useWide()
  const { active, ref } = useActiveStep(wide)

  if (!wide) {
    return (
      <div className={className}>
        {head}
        <ol className="mt-10 flex flex-col gap-16">
          {steps.map((s, i) => (
            <li key={s.id} data-step={s.id}>
              <StepText step={s} dim={false} />
              <div className="mt-6">{stepVisual(i)}</div>
            </li>
          ))}
        </ol>
      </div>
    )
  }

  return (
    <div className={cn("grid gap-16", columns, className)}>
      <div>
        {head}
        {/* Room under the last step, so the visual is still held in the middle while
            the last step is. */}
        <ol className="pb-[20svh]">
          {steps.map((s, i) => (
            <li
              key={s.id}
              ref={ref(i)}
              data-step={s.id}
              data-active={i === active || undefined}
              className="flex min-h-[60svh] flex-col justify-center py-8"
            >
              <StepText step={s} dim={i !== active} />
            </li>
          ))}
        </ol>
      </div>
      {/* The frame sticks under the 56px header, the visual centred in the rest. */}
      <div>
        <div className="sticky top-14 flex h-[calc(100svh-3.5rem)] flex-col justify-center gap-3">
          <div aria-hidden className="flex items-baseline justify-between gap-4 font-mono text-xs text-muted-foreground">
            <span className="grid">
              {steps.map((s, i) => (
                <span key={s.id} className={cn("[grid-area:1/1]", SWAP, i !== active && SWAP_OFF)}>
                  {caption(i)}
                </span>
              ))}
            </span>
            <span data-slot="step-count" className="tabular-nums">
              {active + 1} / {steps.length}
            </span>
          </div>
          {visual(active)}
          <p className="sr-only" aria-live="polite">
            {`${active + 1} of ${steps.length}: ${caption(active)}`}
          </p>
        </div>
      </div>
    </div>
  )
}
