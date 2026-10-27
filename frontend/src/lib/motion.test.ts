import { afterEach, describe, expect, it, vi } from "vitest"

import { markInstant } from "./motion"

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("markInstant", () => {
  it("turns motion off for the next two frames, then back on", () => {
    let frames: FrameRequestCallback[] = []
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb))
    const step = () => {
      const due = frames
      frames = []
      due.forEach((cb) => cb(0))
    }

    markInstant()
    expect(document.documentElement).toHaveAttribute("data-instant")
    step()
    expect(document.documentElement).toHaveAttribute("data-instant")
    step()
    expect(document.documentElement).not.toHaveAttribute("data-instant")
  })
})
