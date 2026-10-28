import "@testing-library/jest-dom/vitest"
import { cleanup, configure } from "@testing-library/react"
import { afterEach, vi } from "vitest"

import { session } from "@/lib/api"

// The pages load lazily (React.lazy); the first test to open one also waits for it
// to be compiled, which can take longer than the default second on a slow machine.
configure({ asyncUtilTimeout: 5000 })

// What jsdom doesn't have and the pages use: media queries (reduced motion), the
// observers behind the ASCII art, scrolling.
class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}
globalThis.ResizeObserver = NoopObserver as unknown as typeof ResizeObserver
globalThis.IntersectionObserver = NoopObserver as unknown as typeof IntersectionObserver
window.matchMedia = (query: string) =>
  ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  }) as MediaQueryList
window.scrollTo = () => {}
Element.prototype.scrollIntoView = () => {}
Element.prototype.scrollTo = () => {}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  // Signed out again: the session lives in memory as well as in localStorage.
  session.setToken(null)
  session.setRole("hr")
  localStorage.clear()
})
