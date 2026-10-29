import { Profiler } from "react"
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
let drawn = 0
let rings = 0
let fine = true
let advance = 7 // the font's "0", as measureText gives it
let tiles: number[] = [] // where line sprites are drawn (x), drawImage at the art's scale

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
    clearRect: (_x: number, _y: number, w: number) => {
      clears++
      if (w >= 1000) drawn++ // the whole art: a frame
    },
    measureText: () => ({ width: advance }),
    fillRect() {},
    drawImage(...args: unknown[]) {
      if (args.length === 5) tiles.push(args[1] as number) // a line's sprite (the frost's take 9)
    },
    createImageData: (w: number, h: number) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
    putImageData() {},
    globalCompositeOperation: "source-over",
    roundRect() {},
    save() {},
    restore() {},
    beginPath() {},
    rect() {},
    moveTo() {},
    ellipse() {},
    clip() {},
    strokeStyle: "",
    lineWidth: 1,
    arc() {},
    stroke: () => {
      rings++
    },
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
  drawn = 0
  rings = 0
  fine = true
  advance = 7
  tiles = []
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
    matches: q.includes("reduced-motion") ? reduce : q.includes("pointer: fine") ? fine : false,
  }))
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
    // 60s of drifting, at the loop's 20 fps: a decode's line and record each time one
    // starts.
    const decodes: { y: number; text: string }[] = []
    let decoding = false
    for (let i = 0; i < 60 * 60; i++) {
      // Someone at the page (it rests after 20s without input).
      if (i % 600 === 0) window.dispatchEvent(new Event("pointermove"))
      glyphs = []
      const before = clears
      step()
      if (clears === before) continue // a 60 Hz frame the 20 fps loop skipped
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
    if (decoding) decodes.pop() // one still reading when the minute ran out
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

// The pointer, as the chat column hears it (jsdom has no PointerEvent: a MouseEvent
// of the same name carries what the scene reads).
function point(target: Element, type: string, x: number, y: number, pointerType = "mouse") {
  const e = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, detail: 1 })
  Object.defineProperty(e, "pointerType", { value: pointerType })
  target.dispatchEvent(e)
}
const linesOf = (container: HTMLElement) =>
  (container.querySelector<HTMLElement>('[data-mode="drift"]')!.dataset.lines ?? "").split(",").map(Number)
// One drawn frame's glyphs (the art is 20 fps, 30 with a mouse over it: some 60 Hz
// steps draw nothing).
function frame() {
  for (let i = 0; i < 4; i++) {
    glyphs = []
    const before = clears
    step()
    if (clears !== before) return glyphs
  }
  return glyphs
}
// The lens's glyphs: readable, but quieter than a decode (and not scrambling).
const lensGlyphs = (gs: Glyph[]) => gs.filter((g) => g.alpha > 0.25 && g.alpha <= 0.5)

describe("RedactedDrift on whole pixels", () => {
  // A font's advance is seldom whole (7.8px for a 13px mono "0"). Drawn at a fraction
  // a tile is resampled and a glyph lands between pixels: on a 1x screen every mark
  // and letter smears into the next pixel.
  it.each([1, 2])("draws every tile and every letter it reads on device pixels at DPR %i", (dpr) => {
    vi.stubGlobal("devicePixelRatio", dpr)
    advance = 7.8
    const { container } = render(<Column />)
    const root = container.firstElementChild as HTMLElement
    step(10)
    const y = linesOf(container).find((ly) => ly > 300 && ly < 640)!
    point(root, "pointerdown", 300, y + 10)
    point(root, "click", 300, y + 10)
    step(40) // past the reveal
    const read = readable(frame())
    expect(read.length).toBeGreaterThan(20)
    const whole = (x: number) => Math.abs(x * dpr - Math.round(x * dpr)) < 1e-6
    expect(read.filter((g) => !whole(g.x))).toEqual([])
    // A minute of drifting: lines' next tiles come on screen.
    tiles = []
    for (let i = 0; i < 60 * 60; i++) {
      if (i % 600 === 0) window.dispatchEvent(new Event("pointermove"))
      step()
    }
    expect(tiles.some((x) => x > 0)).toBe(true)
    expect(tiles.filter((x) => !whole(x))).toEqual([])
  })
})

describe("RedactedDrift and the pointer", () => {
  it("reads the line under a mouse, within the lens's radius only, and closes it again", () => {
    const { container } = render(<Column />)
    const root = container.firstElementChild!
    step(10)
    const y = linesOf(container).find((ly) => ly > 300 && ly < 640)!
    const x = 500
    point(root, "pointermove", x, y)
    step(18) // 300ms: past the scramble in
    const shown = lensGlyphs(frame())
    expect(shown.length).toBeGreaterThan(8)
    // Only the made-up records' own characters, and only inside the radius.
    const theirs = new Set(DRIFT_SNIPPETS.join(""))
    for (const g of shown) {
      expect(Math.hypot(g.x + 3.5 - x, g.y - 0.5 - y)).toBeLessThan(104)
      expect(theirs.has(g.ch)).toBe(true)
    }
    // Lines above and below read too, where the circle reaches them.
    expect(new Set(shown.map((g) => g.y)).size).toBeGreaterThan(1)

    // The pointer leaves: the cells scramble out, then the line is redacted again.
    point(root, "pointerleave", x, y)
    step(30)
    expect(lensGlyphs(frame())).toHaveLength(0)
  })

  it("drifts at 20 frames a second, and at 30 while a mouse is over it for the lens", () => {
    const { container } = render(<Column />)
    const root = container.firstElementChild!
    step(10)
    let before = drawn
    step(60)
    expect(drawn - before).toBe(20)
    point(root, "pointermove", 500, 400)
    before = drawn
    step(60)
    expect(drawn - before).toBe(30)
    point(root, "pointerleave", 500, 400)
    before = drawn
    step(60)
    expect(drawn - before).toBe(20)
  })

  it("draws no lens for a touch", () => {
    const { container } = render(<Column />)
    const root = container.firstElementChild!
    step(10)
    const y = linesOf(container).find((ly) => ly > 300 && ly < 640)!
    point(root, "pointermove", 500, y, "touch")
    step(18)
    expect(lensGlyphs(frame())).toHaveLength(0)
  })

  it("draws no lens on a screen without a fine pointer that hovers", () => {
    fine = false
    const { container } = render(<Column />)
    const root = container.firstElementChild!
    step(10)
    const y = linesOf(container).find((ly) => ly > 300 && ly < 640)!
    point(root, "pointermove", 500, y)
    step(18)
    expect(lensGlyphs(frame())).toHaveLength(0)
  })

  it("decodes the whole line a click lands on, with one faint ring", () => {
    const { container } = render(<Column />)
    const root = container.firstElementChild as HTMLElement
    step(10)
    const y = linesOf(container).find((ly) => ly > 300 && ly < 640)!
    point(root, "pointerdown", 300, y + 10)
    point(root, "click", 300, y + 10)
    step(4) // the next frame, at 20 fps
    expect(rings).toBeGreaterThan(0)
    step(40) // past the reveal
    const read = readable(frame()).filter((g) => Math.abs(g.y - 0.5 - y) < 1)
    const xs = read.map((g) => g.x)
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(700) // across the line, not one record
    expect(container.querySelector('[data-mode="drift"]')).toHaveAttribute("data-decoding")
    // Still readable after 5s, when a zone decode would have gone back.
    step(6 * 60)
    expect(readable(frame()).filter((g) => Math.abs(g.y - 0.5 - y) < 1).length).toBeGreaterThan(20)
  })

  it("ignores a click far from any line, on a control, or a drag", () => {
    const { container } = render(
      <div>
        <Column />
        <button type="button">A control</button>
      </div>,
    )
    const root = container.querySelector('[data-slot="chat-backdrop"]')!.parentElement!
    step(10)
    const ys = linesOf(container)
    const far = ys.reduce((a, b) => Math.max(a, b)) + 200
    point(root, "pointerdown", 300, far)
    point(root, "click", 300, far)
    const y = ys.find((ly) => ly > 300 && ly < 640)!
    point(root.querySelector('[data-drift="composer"]')!, "click", 300, y)
    point(root, "pointerdown", 100, y)
    point(root, "click", 300, y)
    step(30)
    expect(rings).toBe(0)
    expect(container.querySelector('[data-mode="drift"]')).not.toHaveAttribute("data-decoding")
  })

  it("never draws React again for the pointer", () => {
    let renders = 0
    const { container } = render(
      <Profiler id="drift" onRender={() => renders++}>
        <Column />
      </Profiler>,
    )
    const root = container.firstElementChild!
    step(5)
    const before = renders
    const y = linesOf(container).find((ly) => ly > 300 && ly < 640)!
    for (let i = 0; i < 60; i++) {
      point(root, "pointermove", 200 + i * 8, y)
      step()
    }
    point(root, "pointerdown", 400, y)
    point(root, "click", 400, y)
    step(10)
    expect(renders).toBe(before)
  })

  it("with reduced motion the still frame answers neither the lens nor a click", () => {
    reduce = true
    const { container } = render(<Column />)
    const root = container.firstElementChild!
    const drawn = clears
    const y = linesOf(container).find((ly) => ly > 300 && ly < 640)!
    point(root, "pointermove", 500, y)
    point(root, "pointerdown", 500, y)
    point(root, "click", 500, y)
    step(60)
    expect(clears).toBe(drawn)
    expect(rings).toBe(0)
  })

  it("on a phone decodes nothing by itself, only a line that's tapped", () => {
    // A 390px column, the composer at its foot.
    RECTS.composer = [16, 680, 358, 84]
    RECTS.content = [16, 80, 358, 200]
    RECTS[""] = [0, 0, 390, 800]
    try {
      const { container } = render(<Column />)
      const root = container.firstElementChild!
      const art = container.querySelector('[data-mode="drift"]')
      // A minute of drifting: no line reads.
      let read = 0
      for (let i = 0; i < 60 * 60; i++) {
        glyphs = []
        step()
        read += readable(glyphs).length
      }
      expect(read).toBe(0)
      expect(art).not.toHaveAttribute("data-decoding")

      const y = linesOf(container).find((ly) => ly > 300 && ly < 640)!
      point(root, "pointerdown", 200, y, "touch")
      point(root, "click", 200, y)
      step(40)
      expect(art).toHaveAttribute("data-decoding")
      expect(readable(frame()).length).toBeGreaterThan(0)
    } finally {
      RECTS.composer = [150, 680, 700, 84]
      RECTS.content = [150, 80, 700, 200]
      delete RECTS[""]
    }
  })
})

describe("RedactedDrift under glass", () => {
  // The composer as glass with a 16px blur (index.css, --glass-blur).
  function Glass({ leaving = false }: { leaving?: boolean }) {
    return (
      <div>
        <div data-slot="sidebar-gap" />
        <div data-slot="chat-backdrop">
          <RedactedDrift leaving={leaving} />
        </div>
        <div data-drift="content" />
        <div data-drift="composer" style={{ ["--glass-blur" as string]: "16px" }} />
      </div>
    )
  }
  const layer = (c: HTMLElement) => c.querySelector<HTMLCanvasElement>("canvas[data-frost]")
  const composer = (c: HTMLElement) => c.querySelector('[data-drift="composer"]')!

  it("frosts the glass over it itself, and hands the blur back to the glass as it leaves", () => {
    const { container, rerender, unmount } = render(<Glass />)
    step(4)
    // Marked: the glass's backdrop-filter is off (ascii.css) while the art blurs for it.
    expect(composer(container)).toHaveAttribute("data-drift-frost")
    rerender(<Glass leaving />)
    step(4)
    expect(composer(container)).not.toHaveAttribute("data-drift-frost")
    rerender(<Glass />)
    step(4)
    expect(composer(container)).toHaveAttribute("data-drift-frost")
    const el = composer(container)
    unmount()
    expect(el).not.toHaveAttribute("data-drift-frost")
  })

  it("draws the frost on a canvas of its own over the glass, and follows the glass when the sidebar folds", () => {
    const { container } = render(<Glass />)
    step(4)
    expect(layer(container)).not.toBeNull()
    expect(layer(container)!.style.left).toBe("150px")
    expect(layer(container)!.style.width).toBe("700px")
    // The sidebar folds: the composer moves sideways, the same size.
    RECTS.composer = [300, 680, 700, 84]
    try {
      container.querySelector('[data-slot="sidebar-gap"]')!.dispatchEvent(new Event("transitionend", { bubbles: true }))
      step(4)
      expect(layer(container)!.style.left).toBe("300px")
    } finally {
      RECTS.composer = [150, 680, 700, 84]
    }
  })

  it("leaves glass without a blur to CSS", () => {
    const { container } = render(<Column />)
    step(4)
    expect(composer(container)).not.toHaveAttribute("data-drift-frost")
    expect(container.querySelector("canvas[data-frost]")).toBeNull()
  })
})

describe("RedactedDrift with nobody at the page", () => {
  it("holds a frame after 20s without input, never mid-decode, and drifts on at the next", () => {
    const { container } = render(<Column />)
    const art = container.querySelector('[data-mode="drift"]')!
    // 20s without input, then up to a decode's length more to reach a quiet moment.
    step(60 * 30)
    expect(art).not.toHaveAttribute("data-decoding")
    const held = clears
    step(60 * 10)
    expect(clears).toBe(held)
    expect(frames).toHaveLength(0) // nothing scheduled: the loop has stopped

    window.dispatchEvent(new Event("pointermove"))
    step(4)
    expect(clears).toBeGreaterThan(held)
  })
})
