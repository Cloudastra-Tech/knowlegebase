import { act, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { AsciiField } from "@/components/ascii/ascii-field"
import { AsciiLoader } from "@/components/ascii/ascii-loader"
import { AsciiProgress } from "@/components/ascii/ascii-progress"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { AsciiThinking } from "@/components/ascii/ascii-thinking"
import { THINKING_EVENT } from "@/components/ascii/events"
import { ScrambleText } from "@/components/ascii/scramble-text"

// What assistive technology gets from `el`: its text with every aria-hidden part taken out.
function spoken(el: Element) {
  const copy = el.cloneNode(true) as Element
  copy.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove())
  return copy.textContent?.replace(/\s+/g, " ").trim()
}

// The ASCII loop, driven by hand: frames on demand, everything on screen at once, and
// reduced motion when a test asks for it.
let frames: FrameRequestCallback[] = []
let clock = 0
let reduce = false

function step(n = 1, ms = 1000 / 60) {
  for (let i = 0; i < n; i++) {
    clock += ms
    const due = frames
    frames = []
    due.forEach((cb) => cb(clock))
  }
}

beforeEach(() => {
  frames = []
  clock = 0
  reduce = false
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb))
  vi.stubGlobal("cancelAnimationFrame", () => {
    frames = []
  })
  vi.spyOn(performance, "now").mockImplementation(() => clock)
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      cb: IntersectionObserverCallback
      constructor(cb: IntersectionObserverCallback) {
        this.cb = cb
      }
      observe(el: Element) {
        this.cb([{ target: el, isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver)
      }
      unobserve() {}
      disconnect() {}
    },
  )
  const realMatchMedia = window.matchMedia
  vi.stubGlobal("matchMedia", (q: string) => ({
    ...realMatchMedia(q),
    matches: q.includes("reduced-motion") && reduce,
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("AsciiSpinner", () => {
  it("is art for the eyes and a label for assistive technology", () => {
    render(<AsciiSpinner label="Loading members" />)
    const status = screen.getByRole("status")
    expect(spoken(status)).toBe("Loading members")
    expect(status.querySelector(".ascii-spin-frames")!.closest("[aria-hidden]")).not.toBeNull()
    // The still "•••" is there for reduced motion; the CSS chooses which shows.
    expect(status.querySelector(".ascii-spin-still")).toHaveTextContent("•••")
  })

  it("says nothing when the words beside it already do", () => {
    const { container } = render(
      <button type="button">
        <AsciiSpinner label="" /> Saving…
      </button>,
    )
    expect(screen.queryByRole("status")).toBeNull()
    expect(screen.getByRole("button")).toHaveAccessibleName("Saving…")
    expect(spoken(container)).toBe("Saving…")
  })
})

describe("AsciiThinking", () => {
  it("exposes only the status, never the decoding characters", () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5)
    const { rerender } = render(<AsciiThinking status="Searching 4 sources" />)
    const status = screen.getByRole("status")
    step(3) // mid-decode: the visible line is scrambled
    expect(status.textContent).not.toContain("Searching 4 sources Searching 4 sources")
    expect(spoken(status)).toBe("Searching 4 sources")

    rerender(<AsciiThinking status="Writing the answer" />)
    step(2)
    expect(spoken(status)).toBe("Writing the answer")
    step(30)
    // Settled: the shown line reads the same, still hidden from assistive technology.
    expect(status.querySelector('[data-slot="scramble-text"] [aria-hidden]')).toHaveTextContent("Writing the answer")
  })
})

describe("ScrambleText", () => {
  it("decodes within 240ms and then leaves the loop", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0)
    const { container } = render(<ScrambleText text="Acme" />)
    const shown = container.querySelector("[aria-hidden]")!
    step(2)
    expect(shown.textContent).not.toBe("Acme")
    step(20) // past 240ms
    expect(shown).toHaveTextContent("Acme")
    await Promise.resolve()
    expect(frames).toHaveLength(0) // the loop is idle again
  })

  it("just shows the text with reduced motion", () => {
    reduce = true
    const { container } = render(<ScrambleText text="Acme" />)
    expect(container.querySelector("[aria-hidden]")).toHaveTextContent("Acme")
    expect(frames).toHaveLength(0)
  })
})

describe("AsciiField", () => {
  // jsdom has no canvas: a 2D context that only counts what's drawn.
  function fakeCanvas() {
    const ctx = { drawImage: vi.fn(), clearRect: vi.fn(), fillText: vi.fn() }
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
    return ctx
  }

  it("is aria-hidden, and the ambient field draws on a canvas at its own pace", () => {
    const ctx = fakeCanvas()
    const { container } = render(<AsciiField mode="ambient" tone="ambient" mask="reading-column" />)
    const field = container.querySelector('[data-slot="ascii-field"]')!
    expect(field).toHaveAttribute("aria-hidden", "true")
    expect(field).toHaveClass("ascii-mask-reading-column")
    expect(field.querySelector("canvas")).not.toBeNull()
    expect(frames).toHaveLength(1)
    step(10)
    expect(ctx.clearRect).toHaveBeenCalled()
  })

  it("draws one still frame and asks for no more with reduced motion", () => {
    reduce = true
    const ctx = fakeCanvas()
    render(<AsciiField mode="ambient" tone="ambient" />)
    const drawn = ctx.clearRect.mock.calls.length
    expect(drawn).toBeGreaterThan(0)
    expect(frames).toHaveLength(0)
    // Not even the thinking ripple moves it.
    act(() => {
      window.dispatchEvent(new CustomEvent(THINKING_EVENT, { detail: { active: true } }))
    })
    step(60)
    expect(ctx.clearRect).toHaveBeenCalledTimes(drawn)
  })

  it("keeps the landing's text field (pre) for strips", () => {
    const { container } = render(<AsciiField className="absolute inset-0 text-foreground/[0.12]" />)
    const field = container.querySelector('[data-slot="ascii-field"]')!
    expect(field.querySelector("pre")).not.toBeNull()
    expect(field).toHaveClass("absolute")
    expect(field).not.toHaveClass("relative")
  })
})

describe("AsciiEmptyState", () => {
  it("puts the words in a heading and hides the art", () => {
    render(<AsciiEmptyState art="inbox" title="No digests yet" description="Subscribe to a space." animate="blink" />)
    const heading = screen.getByRole("heading", { level: 3, name: "No digests yet" })
    const box = heading.closest('[data-slot="ascii-empty-state"]')!
    expect(box.querySelector("pre")!.closest("[aria-hidden]")).not.toBeNull()
    expect(spoken(box)).toBe("No digests yetSubscribe to a space.") // the heading, then the words
    expect(spoken(box)).not.toMatch(/[|_▌]/)
  })

  it("takes its own art and a level-4 heading", () => {
    render(<AsciiEmptyState art={["[ ]", "[x]"]} title="Nothing here" headingLevel={4} animate="scan" />)
    expect(screen.getByRole("heading", { level: 4, name: "Nothing here" })).toBeInTheDocument()
  })
})

describe("AsciiDivider", () => {
  it("is a labelled heading with a hidden rule, or a separator", () => {
    const { container } = render(
      <>
        <AsciiDivider as="h3" label="Providers" />
        <AsciiDivider />
      </>,
    )
    const heading = screen.getByRole("heading", { level: 3 })
    expect(spoken(heading)).toBe("Providers")
    expect(screen.getByRole("separator")).toBeInTheDocument()
    expect(spoken(container)).toBe("Providers")
  })
})

describe("AsciiProgress", () => {
  it("is a named progressbar drawn in whole cells", () => {
    render(<AsciiProgress value={62} label="Indexing" cells={10} />)
    const bar = screen.getByRole("progressbar", { name: "Indexing" })
    expect(bar).toHaveAttribute("aria-valuenow", "62")
    expect(bar).toHaveAttribute("aria-valuetext", "62%")
    expect(bar.querySelector("[aria-hidden]")).toHaveTextContent("[██████░░░░]")
  })

  it("keeps to its range", () => {
    render(<AsciiProgress value={140} label="Upload" showValue={false} />)
    const bar = screen.getByRole("progressbar", { name: "Upload" })
    expect(bar).toHaveAttribute("aria-valuenow", "100")
    expect(spoken(bar)).toBe("")
  })
})

describe("AsciiLoader", () => {
  it("keeps the skeleton slot the e2e specs wait on", () => {
    const { container } = render(<AsciiLoader />)
    expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull()
    expect(spoken(container)).toBe("Loading")
  })
})
