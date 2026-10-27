import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// The shared loop, driven by hand: a fake requestAnimationFrame (one 60 Hz frame per
// step()), a fake clock for the frame budget, and the observers and media query the
// loop listens to. Each test loads a fresh copy of the module.
type Loop = typeof import("./ascii-loop")

let clock = 0
let frames = new Map<number, FrameRequestCallback>()
let nextId = 0
let observer: { cb: IntersectionObserverCallback; els: Set<Element> }
let reduceListeners: ((e: MediaQueryListEvent) => void)[] = []
let reduce = false
const realMatchMedia = window.matchMedia

function step(n = 1, ms = 1000 / 60) {
  for (let i = 0; i < n; i++) {
    clock += ms
    const due = [...frames.values()]
    frames.clear()
    due.forEach((cb) => cb(clock))
  }
}

function show(el: Element, visible = true) {
  const entry = { target: el, isIntersecting: visible } as IntersectionObserverEntry
  observer.cb([entry], {} as IntersectionObserver)
}

function setReduced(matches: boolean) {
  reduce = matches
  reduceListeners.forEach((l) => l({ matches } as MediaQueryListEvent))
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden })
  document.dispatchEvent(new Event("visibilitychange"))
}

async function load(): Promise<Loop> {
  vi.resetModules()
  return import("./ascii-loop")
}

const box = () => document.body.appendChild(document.createElement("div"))

beforeEach(() => {
  clock = 0
  frames = new Map()
  reduceListeners = []
  reduce = false
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    frames.set(++nextId, cb)
    return nextId
  })
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id))
  vi.spyOn(performance, "now").mockImplementation(() => clock)
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(cb: IntersectionObserverCallback) {
        observer = { cb, els: new Set() }
      }
      observe(el: Element) {
        observer.els.add(el)
      }
      unobserve(el: Element) {
        observer.els.delete(el)
      }
      disconnect() {
        observer.els.clear()
      }
    },
  )
  window.matchMedia = (query: string) =>
    ({
      media: query,
      get matches() {
        return reduce
      },
      addEventListener: (_: string, l: (e: MediaQueryListEvent) => void) => reduceListeners.push(l),
      removeEventListener: (_: string, l: (e: MediaQueryListEvent) => void) => {
        reduceListeners = reduceListeners.filter((x) => x !== l)
      },
    }) as unknown as MediaQueryList
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  window.matchMedia = realMatchMedia
  setHidden(false)
  document.body.removeAttribute("data-scroll-locked")
  delete document.documentElement.dataset.effects
  document.body.innerHTML = ""
})

describe("the shared ASCII loop", () => {
  it("runs every subscriber on one requestAnimationFrame", async () => {
    const { subscribe } = await load()
    const draws = [vi.fn(), vi.fn(), vi.fn()]
    const els = draws.map(() => box())
    const handles = els.map((el, i) => subscribe(el, draws[i], { still: 1 }))
    els.forEach((el) => show(el))

    expect(frames.size).toBe(1)
    step(4)
    expect(frames.size).toBe(1)
    draws.forEach((d) => expect(d).toHaveBeenCalled())

    handles.forEach((h) => h.stop())
    expect(frames.size).toBe(0) // idle with nobody left
  })

  it("caps each subscriber at its own frame rate", async () => {
    const { subscribe } = await load()
    const fast = vi.fn()
    const slow = vi.fn()
    const a = box()
    const b = box()
    subscribe(a, fast, { still: 0, fps: 30 })
    subscribe(b, slow, { still: 0, fps: 12, priority: "ambient" })
    show(a)
    show(b)

    step(60) // one second
    expect(fast).toHaveBeenCalledTimes(30)
    expect(slow).toHaveBeenCalledTimes(12)
  })

  it("rests while offscreen or the tab is hidden, and the clock waits", async () => {
    const { subscribe } = await load()
    const draw = vi.fn()
    const el = box()
    subscribe(el, draw, { still: 0 })
    expect(frames.size).toBe(0) // not on screen yet

    show(el)
    step(2)
    const seen = draw.mock.calls.length
    const t = draw.mock.lastCall![0]

    setHidden(true)
    step(30)
    expect(draw).toHaveBeenCalledTimes(seen)
    expect(frames.size).toBe(0)

    setHidden(false)
    step(1)
    expect(draw.mock.lastCall![0]).toBeLessThan(t + 0.1) // not half a second on

    show(el, false)
    step(30)
    expect(frames.size).toBe(0)
  })

  it("draws one still frame under reduced motion", async () => {
    reduce = true
    const { subscribe } = await load()
    const draw = vi.fn()
    const el = box()
    const loop = subscribe(el, draw, { still: 2.4 })
    show(el)
    step(10)

    expect(draw).toHaveBeenCalledTimes(1)
    expect(draw).toHaveBeenCalledWith(2.4)
    expect(frames.size).toBe(0)
    loop.redraw()
    expect(draw).toHaveBeenLastCalledWith(2.4)
  })

  it("follows a change to reduced motion while running", async () => {
    const { subscribe } = await load()
    const draw = vi.fn()
    const el = box()
    subscribe(el, draw, { still: 3 })
    show(el)
    step(4)

    setReduced(true)
    expect(draw).toHaveBeenLastCalledWith(3)
    expect(frames.size).toBe(0)
    const seen = draw.mock.calls.length
    step(10)
    expect(draw).toHaveBeenCalledTimes(seen)

    setReduced(false)
    step(4)
    expect(draw.mock.calls.length).toBeGreaterThan(seen)
  })

  it('goes still for "Reduce effects", and marks the page for the glass', async () => {
    const { subscribe, setEffects } = await load()
    const draw = vi.fn()
    const el = box()
    subscribe(el, draw, { still: 5 })
    show(el)
    step(2)

    setEffects("reduced")
    expect(draw).toHaveBeenLastCalledWith(5)
    expect(document.documentElement.dataset.effects).toBe("reduced")
    expect(frames.size).toBe(0)

    setEffects("full")
    expect(document.documentElement.dataset.effects).toBeUndefined()
    expect(frames.size).toBe(1)
  })

  it("rests the background field while scrolling and under a dialog, not the hero", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const { subscribe } = await load()
    const ambient = vi.fn()
    const hero = vi.fn()
    const a = box()
    const h = box()
    subscribe(a, ambient, { still: 0, fps: 12, priority: "ambient" })
    subscribe(h, hero, { still: 0 })
    show(a)
    show(h)
    step(10)
    const before = ambient.mock.calls.length

    document.dispatchEvent(new Event("scroll"))
    step(6) // 100ms of scrolling
    expect(ambient).toHaveBeenCalledTimes(before)
    vi.advanceTimersByTime(150)
    step(12)
    expect(ambient.mock.calls.length).toBeGreaterThan(before)

    document.body.setAttribute("data-scroll-locked", "1")
    await Promise.resolve() // the MutationObserver
    const open = ambient.mock.calls.length
    const heroOpen = hero.mock.calls.length
    step(30)
    expect(ambient).toHaveBeenCalledTimes(open)
    expect(hero.mock.calls.length).toBeGreaterThan(heroOpen)

    document.body.removeAttribute("data-scroll-locked")
    await Promise.resolve()
    step(12)
    expect(ambient.mock.calls.length).toBeGreaterThan(open)
  })

  it("steps the background field down when it keeps taking more than 4ms, and back up", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const { subscribe } = await load()
    let cost = 5
    const field = vi.fn(() => {
      clock += cost // a slow frame
    })
    const el = box()
    subscribe(el, field, { still: 0, fps: 12, priority: "ambient" })
    show(el)

    step(300) // warm-up, then 30 slow frames at 12 fps: down to 6
    field.mockClear()
    step(120) // two seconds
    expect(field.mock.calls.length).toBeLessThanOrEqual(13)
    expect(field.mock.calls.length).toBeGreaterThan(8)

    step(300) // 30 slow frames at 6 fps: still
    expect(frames.size).toBe(0)

    cost = 0.5 // cheap again: after a wait it moves at 6 fps, then back at 12
    vi.advanceTimersByTime(10_000)
    expect(frames.size).toBe(1)
    step(330)
    field.mockClear()
    step(120)
    expect(field.mock.calls.length).toBeGreaterThan(20)
  })

  it("waits longer each time the field has to stop again", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
    const { subscribe } = await load()
    const field = vi.fn(() => {
      clock += 5
    })
    const el = box()
    subscribe(el, field, { still: 0, fps: 12, priority: "ambient" })
    show(el)
    step(600) // down to still
    expect(frames.size).toBe(0)

    vi.advanceTimersByTime(10_000) // tries again at 6 fps, still too slow
    step(330)
    expect(frames.size).toBe(0)
    vi.advanceTimersByTime(10_000)
    expect(frames.size).toBe(0) // the second wait is twice as long
    vi.advanceTimersByTime(10_000)
    expect(frames.size).toBe(1)
  })

  it("lets the landing's hero spend up to 8ms a frame at 30 fps", async () => {
    const { subscribe } = await load()
    const hero = vi.fn(() => {
      clock += 6 // over the app's 4ms, inside the hero's 8
    })
    const el = box()
    subscribe(el, hero, { still: 0 })
    show(el)

    step(240)
    hero.mockClear()
    step(60)
    expect(hero.mock.calls.length).toBeGreaterThanOrEqual(25)
  })

  it("drops the hero to 20 fps past 8ms, and brings it back once frames are cheap", async () => {
    const { subscribe } = await load()
    let cost = 10
    const hero = vi.fn(() => {
      clock += cost
    })
    const el = box()
    subscribe(el, hero, { still: 0 })
    show(el)

    step(100) // warm-up, then 30 slow frames
    hero.mockClear()
    step(60)
    expect(hero.mock.calls.length).toBeLessThanOrEqual(20)
    expect(hero.mock.calls.length).toBeGreaterThan(12)

    cost = 2
    step(120) // 30 cheap frames at 20 fps
    hero.mockClear()
    step(60)
    expect(hero.mock.calls.length).toBeGreaterThanOrEqual(28)
  })

  it("doesn't count a piece's first frames against the budget", async () => {
    const { subscribe } = await load()
    let n = 0
    const hero = vi.fn(() => {
      if (++n <= 10) clock += 40 // cold code, a font arriving
    })
    const el = box()
    subscribe(el, hero, { still: 0 })
    show(el)

    step(120)
    hero.mockClear()
    step(60)
    expect(hero.mock.calls.length).toBeGreaterThanOrEqual(28)
  })

  it('starts still when the page already says "Reduce effects", and keeps the choice', async () => {
    document.documentElement.dataset.effects = "reduced"
    const { subscribe, setEffects, getEffects } = await load()
    const draw = vi.fn()
    const el = box()
    subscribe(el, draw, { still: 7 })
    show(el)
    step(10)
    expect(draw).toHaveBeenCalledTimes(1)
    expect(draw).toHaveBeenCalledWith(7)
    expect(getEffects()).toBe("reduced")

    setEffects("full")
    expect(localStorage.getItem("effects")).toBeNull()
    setEffects("reduced")
    expect(localStorage.getItem("effects")).toBe("reduced")
  })

  it("keeps runLoop as the landing calls it: 30 frames a second", async () => {
    const { runLoop } = await load()
    const draw = vi.fn()
    const el = box()
    const loop = runLoop(el, draw, 1)
    show(el)
    step(60)
    expect(draw).toHaveBeenCalledTimes(30)
    loop.stop()
    expect(frames.size).toBe(0)
  })
})
