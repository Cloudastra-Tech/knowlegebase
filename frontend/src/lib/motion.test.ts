import { act, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { markInstant, usePrefersReducedMotion } from "./motion"

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

describe("usePrefersReducedMotion", () => {
  it("follows the setting live", () => {
    let matches = false
    const listeners = new Set<() => void>()
    vi.stubGlobal("matchMedia", (query: string) => ({
      get matches() {
        return query === "(prefers-reduced-motion: reduce)" && matches
      },
      addEventListener: (_: string, cb: () => void) => listeners.add(cb),
      removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
    }))
    const { result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(false)

    matches = true
    act(() => listeners.forEach((cb) => cb()))
    expect(result.current).toBe(true)
  })
})
