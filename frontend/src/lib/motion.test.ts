import { act, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { markInstant, usePrefersReducedMotion, useStaggerOnce } from "./motion"

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

describe("useStaggerOnce", () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it("staggers the first draw 40ms apart, capped at the sixth, then never again", () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(({ ready }) => useStaggerOnce(ready), {
      initialProps: { ready: false },
    })
    // Nothing yet: the items aren't there.
    expect(result.current(0)).toEqual({})

    rerender({ ready: true })
    expect(result.current(0).className).toContain("motion-safe:animate-in")
    expect(result.current(0).style).toEqual({ animationDelay: "0ms" })
    expect(result.current(2).style).toEqual({ animationDelay: "80ms" })
    expect(result.current(9).style).toEqual({ animationDelay: "200ms" })

    // Played: a refetch, a filter or a new item doesn't animate.
    act(() => vi.advanceTimersByTime(1000))
    expect(result.current(0)).toEqual({})
    expect(result.current(7)).toEqual({})
  })
})
