import { act, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { LandingPage } from "@/components/landing-page"
import { BAND } from "@/components/landing-scene"

// The landing's step sections (landing-steps.tsx): the answer demo, the access demo and
// where the model runs used to be tabs; now the step crossing the middle of the window
// picks what the visual beside it shows. The observer is faked, so a test says which
// step has entered the band.

type Fake = { cb: IntersectionObserverCallback; options?: IntersectionObserverInit; nodes: Set<Element> }
let observers: Fake[] = []
let media: Record<string, boolean> = {}
const real = { io: globalThis.IntersectionObserver, mm: window.matchMedia }

beforeEach(() => {
  observers = []
  media = { "(min-width: 1024px)": true }
  globalThis.IntersectionObserver = class {
    fake: Fake
    constructor(cb: IntersectionObserverCallback, options?: IntersectionObserverInit) {
      this.fake = { cb, options, nodes: new Set() }
      observers.push(this.fake)
    }
    observe(el: Element) {
      this.fake.nodes.add(el)
    }
    unobserve(el: Element) {
      this.fake.nodes.delete(el)
    }
    disconnect() {
      this.fake.nodes.clear()
    }
    takeRecords() {
      return []
    }
  } as unknown as typeof IntersectionObserver
  window.matchMedia = (query: string) =>
    ({
      matches: !!media[query],
      media: query,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }) as unknown as MediaQueryList
})

afterEach(() => {
  globalThis.IntersectionObserver = real.io
  window.matchMedia = real.mm
})

// The step `step` of section `id` enters the middle band.
function enter(id: string, step: number) {
  const li = document.querySelectorAll(`#${id} li[data-step]`)[step]
  const fake = observers.find((o) => o.options?.rootMargin === BAND && o.nodes.has(li))!
  const rect = { top: 380, height: 40 } as DOMRectReadOnly
  act(() =>
    fake.cb(
      [{ target: li, isIntersecting: true, boundingClientRect: rect, rootBounds: { top: 0, height: 800 } } as unknown as IntersectionObserverEntry],
      {} as IntersectionObserver,
    ),
  )
}

const section = (id: string) => document.getElementById(id)!
const activeStep = (id: string) => section(id).querySelector("li[data-active]")?.getAttribute("data-step")
const live = (id: string) => section(id).querySelector("[aria-live=polite]")?.textContent

describe("the landing's step sections", () => {
  it("has no tabs left", () => {
    render(<LandingPage onSignIn={() => {}} />)
    expect(screen.queryByRole("tablist")).toBeNull()
    expect(screen.queryByRole("tab")).toBeNull()
  })

  it("shows the state of the step in the middle of the window", () => {
    render(<LandingPage onSignIn={() => {}} />)
    expect(activeStep("answer")).toBe("hr")
    expect(live("answer")).toBe("1 of 3: HR example")

    enter("answer", 1)
    expect(activeStep("answer")).toBe("eng")
    expect(live("answer")).toBe("2 of 3: Engineering example")
    expect(within(section("answer")).getByText("2 / 3", { exact: false })).toBeInTheDocument()
    // Only the active example is shown and in the accessibility tree.
    const layers = [...section("answer").querySelectorAll("[data-current], [aria-hidden=true][inert]")]
    const shown = layers.filter((l) => l.hasAttribute("data-current"))
    expect(shown).toHaveLength(1)
    expect(shown[0]).toHaveTextContent("How do I get production database access?")

    enter("access", 1)
    expect(activeStep("access")).toBe("Hira")
    expect(live("access")).toBe("2 of 2: asked by Hira, HR")

    enter("privacy", 2)
    expect(activeStep("privacy")).toBe("local")
    expect(live("privacy")).toBe("3 of 3: This server")
    // A step leaving the band changes nothing: the last one entered stays.
    const li = section("privacy").querySelectorAll("li[data-step]")[2]
    const fake = observers.find((o) => o.nodes.has(li))!
    act(() => fake.cb([{ target: li, isIntersecting: false } as unknown as IntersectionObserverEntry], {} as IntersectionObserver))
    expect(activeStep("privacy")).toBe("local")
  })

  it("keeps every step's words in the page, in order", () => {
    render(<LandingPage onSignIn={() => {}} />)
    const titles = [...section("privacy").querySelectorAll("li[data-step] h3")].map((h) => h.textContent)
    expect(titles).toEqual(["Your AWS account", "Your own key", "This server"])
  })

  it("on a phone, puts each step's own visual under it, with nothing sticky", () => {
    media = {}
    render(<LandingPage onSignIn={() => {}} />)
    for (const id of ["answer", "access", "privacy"]) {
      expect(section(id).querySelector(".sticky")).toBeNull()
      expect(section(id).querySelector("[aria-live]")).toBeNull()
    }
    expect(section("privacy").querySelectorAll("[data-slot=model-route]")).toHaveLength(3)
    const steps = section("answer").querySelectorAll("li[data-step]")
    expect(steps).toHaveLength(3)
    expect(steps[2]).toHaveTextContent("Example. Question: What discount can I offer?")
  })

  it("with reduced motion, shows a step's finished state at once", () => {
    media["(prefers-reduced-motion: reduce)"] = true
    render(<LandingPage onSignIn={() => {}} />)
    enter("answer", 2)
    const shown = section("answer").querySelector("[data-current]")!
    // The scan is over: the cited passages are marked, the answer written out.
    expect(shown.querySelectorAll("[data-state=cited]")).toHaveLength(2)
    expect(shown).toHaveTextContent("Up to 15% on annual plans and none on monthly.")
  })
})
