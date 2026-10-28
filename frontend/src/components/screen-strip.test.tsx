import { act, render } from "@testing-library/react"
import { useRef } from "react"
import { MemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ScreenHeader } from "@/components/screen-header"
import { ScreenStrip } from "@/components/screen-strip"

// The strip watches the screen's h2 with an IntersectionObserver; this one hands its
// callback to the test, which says where the title is.
let report: ((entry: Partial<IntersectionObserverEntry>) => void) | null = null
class TitleObserver {
  constructor(cb: IntersectionObserverCallback) {
    report = (entry) => cb([entry as IntersectionObserverEntry], this as unknown as IntersectionObserver)
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}

function Screen() {
  const box = useRef<HTMLDivElement>(null)
  return (
    <div ref={box}>
      <ScreenStrip scroller={box} />
      <ScreenHeader title="Knowledge base" />
    </div>
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  report = null
})

describe("ScreenStrip", () => {
  it("shows the title once it has scrolled under the strip, and hides again", () => {
    vi.stubGlobal("IntersectionObserver", TitleObserver)
    const { container } = render(
      <MemoryRouter initialEntries={["/knowledge"]}>
        <Screen />
      </MemoryRouter>,
    )
    const strip = container.querySelector('[data-slot="screen-strip"]')!
    expect(strip).toHaveAttribute("aria-hidden", "true")
    expect(strip).not.toHaveAttribute("data-shown")

    const rootBounds = { top: 0 } as DOMRectReadOnly
    act(() => report!({ isIntersecting: false, boundingClientRect: { top: -30 } as DOMRectReadOnly, rootBounds }))
    expect(strip).toHaveAttribute("data-shown")
    expect(strip).toHaveTextContent("Knowledge base")

    act(() => report!({ isIntersecting: true, boundingClientRect: { top: 60 } as DOMRectReadOnly, rootBounds }))
    expect(strip).not.toHaveAttribute("data-shown")
  })
})
