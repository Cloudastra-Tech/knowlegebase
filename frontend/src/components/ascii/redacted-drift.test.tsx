import { render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { DRIFT_SNIPPETS } from "@/components/ascii/drift"
import { RedactedDrift } from "@/components/ascii/redacted-drift"

// The ASCII loop driven by hand (as ascii-kit.test.tsx), and a 2D context that records
// the letters drawn: the art's only text is what it decodes.
let frames: FrameRequestCallback[] = []
let clock = 0
let reduce = false
type Glyph = { ch: string; x: number; y: number; alpha: number }
let glyphs: Glyph[] = []
let clears = 0

function step(n = 1, ms = 1000 / 60) {
  for (let i = 0; i < n; i++) {
    clock += ms
    const due = frames
    frames = []
    due.forEach((cb) => cb(clock))
  }
}

function fakeContext() {
  const ctx = {
    globalAlpha: 1,
    font: "",
    fillStyle: "",
    textBaseline: "",
    setTransform() {},
    clearRect: () => {
      clears++
    },
    measureText: () => ({ width: 7 }),
    fillRect() {},
    drawImage() {},
    save() {},
    restore() {},
    beginPath() {},
    rect() {},
    moveTo() {},
    ellipse() {},
    clip() {},
    fillText(ch: string, x: number, y: number) {
      glyphs.push({ ch, x, y, alpha: ctx.globalAlpha })
    },
  }
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
}

// A 1000×800 chat column with the composer at its foot and the empty state's words
// at its top.
const RECTS: Record<string, [number, number, number, number]> = {
  composer: [150, 680, 700, 84],
  content: [150, 80, 700, 200],
}
function layout() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const [x, y, w, h] = RECTS[this.dataset.drift ?? ""] ?? [0, 0, 1000, 800]
    return { x, y, left: x, top: y, width: w, height: h, right: x + w, bottom: y + h, toJSON() {} } as DOMRect
  })
}

function Column() {
  return (
    <div>
      <div data-slot="chat-backdrop">
        <RedactedDrift />
      </div>
      <div data-drift="content" />
      <div data-drift="composer" />
    </div>
  )
}

// The readable text drawn in a frame, a decode's letters by the line they're on.
const readable = (gs: Glyph[]) => gs.filter((g) => g.alpha > 0.5)

beforeEach(() => {
  frames = []
  clock = 0
  reduce = false
  glyphs = []
  clears = 0
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
  vi.stubGlobal("matchMedia", (q: string) => ({ ...realMatchMedia(q), matches: q.includes("reduced-motion") && reduce }))
  fakeContext()
  layout()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("RedactedDrift", () => {
  it("is decoration: aria-hidden, the chat's one field", () => {
    const { container } = render(<Column />)
    const art = container.querySelector('[data-mode="drift"]')!
    expect(art).toHaveAttribute("aria-hidden", "true")
    expect(art).toHaveAttribute("data-slot", "ascii-field")
    expect(art.querySelector("canvas")).not.toBeNull()
  })

  it("with reduced motion is one still frame with one line readable", () => {
    reduce = true
    render(<Column />)
    expect(frames).toHaveLength(0) // never joins the loop
    const shown = readable(glyphs)
    expect(new Set(shown.map((g) => g.y)).size).toBe(1)
    const text = shown.map((g) => g.ch).join("")
    expect(DRIFT_SNIPPETS.map((s) => s.replace(/ /g, ""))).toContain(text)
    const drawn = clears
    step(120)
    expect(clears).toBe(drawn)
  })

  it("decodes about every 8s, going round the lines, and never shows a record twice running", () => {
    render(<Column />)
    expect(frames).toHaveLength(1)
    // 60s of drifting, at the loop's 30 fps: a decode's line and record each time one
    // starts.
    const decodes: { y: number; text: string }[] = []
    let decoding = false
    for (let i = 0; i < 60 * 60; i++) {
      glyphs = []
      const before = clears
      step()
      if (clears === before) continue // a 60 Hz frame the 30 fps loop skipped
      const shown = readable(glyphs)
      if (shown.length && !decoding) {
        const y = shown[0].y
        decodes.push({ y, text: "" })
      }
      if (shown.length) {
        // The record as fully read (its longest readable run).
        const text = shown.map((g) => g.ch).join("")
        const last = decodes[decodes.length - 1]
        if (text.length > last.text.length) last.text = text
      }
      decoding = shown.length > 0
    }
    expect(decodes.length).toBeGreaterThanOrEqual(5)
    expect(decodes.length).toBeLessThanOrEqual(8)
    for (let i = 1; i < decodes.length; i++) {
      expect(decodes[i].y).not.toBe(decodes[i - 1].y)
      expect(decodes[i].text).not.toBe(decodes[i - 1].text)
    }
    const snippets = DRIFT_SNIPPETS.map((s) => s.replace(/ /g, ""))
    for (const d of decodes) expect(snippets).toContain(d.text)
  })
})
