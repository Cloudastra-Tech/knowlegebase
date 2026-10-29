import { expect, test, type Page } from "@playwright/test"

import { ask, conversation, DESKTOP, MEMBER, PHONE, shot, signIn, waitForAnswer } from "./helpers.ts"

// The chat's scrolling (components/chat-view.tsx, the message scroller): a question just
// asked is pinned to the top of the view and its answer grows below it, with no
// scrolling while it streams; "Jump to latest" goes to the end; a saved chat opens at
// its last question and nothing in it animates. "in full detail" makes the fake OpenAI
// write a long answer slowly (tests/e2e/fake_openai.py).

const viewport = (page: Page) => page.getByRole("region", { name: "Chat messages" })
const jump = (page: Page) => page.locator('[data-slot="message-scroller-button"]')

// Strings, so the e2e tsconfig needs no DOM types.
// Also, from the question asked next: where it and its answer's row are on screen in
// every frame (the spread of each, px), and the layout shifts that moved anything in
// the question.
const WATCH = `(() => {
  const v = document.querySelector('[data-slot="message-scroller-viewport"]')
  window.__scrolls = 0
  v.addEventListener("scroll", () => window.__scrolls++, { passive: true })
  window.__smooth = 0
  const scrollTo = Element.prototype.scrollTo
  Element.prototype.scrollTo = function (...args) {
    if (args[0] && args[0].behavior === "smooth") window.__smooth++
    return scrollTo.apply(this, args)
  }
  const anchors = () => [...v.querySelectorAll('[data-scroll-anchor="true"]')]
  const before = new Set(anchors())
  const spread = { q: [Infinity, -Infinity], a: [Infinity, -Infinity] }
  const add = (k, y) => { spread[k][0] = Math.min(spread[k][0], y); spread[k][1] = Math.max(spread[k][1], y) }
  window.__frames = 0
  window.__spread = () => ({ question: spread.q[1] - spread.q[0], answer: spread.a[1] - spread.a[0], frames: window.__frames })
  const frame = () => {
    const question = anchors().find((r) => !before.has(r))
    if (question) {
      const top = v.getBoundingClientRect().top
      add("q", question.getBoundingClientRect().top - top)
      const answer = question.nextElementSibling
      if (answer) add("a", answer.getBoundingClientRect().top - top)
      window.__frames++
    }
    requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
  window.__shifted = 0
  new PerformanceObserver((list) => {
    const question = anchors().find((r) => !before.has(r))
    for (const e of list.getEntries())
      if (question && e.sources.some((s) => s.node && question.contains(s.node))) window.__shifted++
  }).observe({ type: "layout-shift" })
})()`
const spread = (page: Page) =>
  page.evaluate("window.__spread()") as Promise<{ question: number; answer: number; frames: number }>

// The turn before has settled: nothing in the transcript is still moving. The fake
// OpenAI writes a short answer in a few milliseconds, so it's done while its steps are
// still folding away (250ms from its first word); a real answer takes far longer than
// that. Asked during the fold, the question is pinned while the turn above it is still
// getting shorter, which the scroller makes up for a frame later: not what this is about.
async function settled(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(
        `document.querySelector('[data-slot="message-scroller-content"]').getAnimations({ subtree: true }).filter((a) => a.playState === "running").length`,
      ),
    )
    .toBe(0)
}

interface View {
  top: number // the viewport's scrollTop
  scrolls: number // scroll events since WATCH
  smooth: number // smooth scrollTo calls since WATCH
  pinned: number // the last question's top, below the viewport's top (px)
  answer: number // the last answer's height (px)
  below: number // how far the last answer's end is below the viewport's (px)
}
const VIEW = `(() => {
  const v = document.querySelector('[data-slot="message-scroller-viewport"]')
  const rows = [...v.querySelectorAll('[data-slot="message-scroller-item"]')]
  const question = rows.filter((r) => r.dataset.scrollAnchor === "true").at(-1)
  const answers = v.querySelectorAll('[aria-label="Answer"]')
  const answer = answers[answers.length - 1]
  const box = v.getBoundingClientRect()
  return {
    top: v.scrollTop,
    scrolls: window.__scrolls ?? 0,
    smooth: window.__smooth ?? 0,
    pinned: question ? question.getBoundingClientRect().top - box.top : NaN,
    answer: answer ? answer.getBoundingClientRect().height : 0,
    below: answer ? answer.getBoundingClientRect().bottom - box.bottom : 0,
  }
})()`
const view = (page: Page) => page.evaluate(VIEW) as Promise<View>

async function send(page: Page, question: string) {
  const box = page.getByRole("textbox", { name: "Your question" })
  await box.fill(question)
  await box.press("Enter")
  await expect(page.getByRole("button", { name: "Stop answering" })).toBeVisible()
}

test("a new question is pinned to the top, its answer grows below it without scrolling, and Jump to latest goes to the end", async ({
  page,
}) => {
  test.setTimeout(60_000)
  await page.setViewportSize(DESKTOP)
  await signIn(page, MEMBER)
  const log = conversation(page)
  await ask(page, "How many paid annual leave days do staff get?")
  await settled(page)

  await page.evaluate(WATCH)
  await send(page, "What does the leave policy say about sick leave, in full detail?")

  // Pinned: the question's row sits near the top of the view (the scroller keeps 64px
  // of the turn before it in sight, under the transcript's 24px padding).
  await expect.poll(async () => (await view(page)).pinned).toBeGreaterThan(40)
  await expect.poll(async () => (await view(page)).pinned).toBeLessThan(120)
  // The answer has started (the earlier turn lost its follow-ups as the question went,
  // before it was pinned).
  await expect(log.locator('[aria-label="Answer"]').last().locator("p").first()).toBeVisible()
  const pinned = await view(page)
  expect(pinned.top).toBeGreaterThan(0) // the first turn went up out of the way

  // The answer grows by several lines, past the bottom of the view, while nothing
  // scrolls: no scroll event, no smooth scroll, the same scrollTop, the question in the
  // same place.
  await expect
    .poll(async () => (await view(page)).answer, { timeout: 15_000 })
    .toBeGreaterThan(pinned.answer + 300)
  await expect.poll(async () => (await view(page)).below, { timeout: 15_000 }).toBeGreaterThan(0)
  const grown = await view(page)
  expect(grown.top).toBe(pinned.top)
  expect(grown.scrolls).toBe(pinned.scrolls)
  expect(grown.smooth).toBe(0)
  expect(grown.pinned).toBeCloseTo(pinned.pinned, 0)
  // Nor for a single frame, from the moment it was asked: not the question (the answer
  // getting shorter as its steps fold away left the view past the end of the scroll
  // range, and the browser pulled it back, dropping the question, until the scroller
  // put it back) and not its answer's row (the question's Edit button arriving pushed
  // it down). No layout shift moved anything in the question.
  const frames = await spread(page)
  expect(frames.frames).toBeGreaterThan(10)
  expect(frames.question).toBeLessThan(0.5)
  expect(frames.answer).toBeLessThan(0.5)
  expect(await page.evaluate("window.__shifted")).toBe(0)
  await expect(jump(page)).toHaveAttribute("data-active", "true")
  await shot(page, "chat-scroll-desktop-streaming")

  // Moved some other way than the wheel (here, the focus going to the first answer,
  // above, as a skip or Shift+Tab would), the view stays where that put it while the
  // answer goes on growing.
  await log.getByLabel("Answer", { exact: true }).first().focus()
  await expect.poll(async () => (await view(page)).top).toBeLessThan(pinned.top)
  const moved = await view(page)
  await expect.poll(async () => (await view(page)).answer).toBeGreaterThan(moved.answer + 100)
  expect((await view(page)).top).toBe(moved.top)

  // Stop: the view stays where it is, the end is said once, the focus goes to the answer.
  await page.getByRole("button", { name: "Stop answering" }).click()
  await expect(log).toContainText("Stopped before the end")
  await expect(page.getByRole("status")).toHaveText("Answer stopped.")
  expect((await view(page)).top).toBe(moved.top)
  await expect(log.locator('[aria-label="Answer"]').last()).toBeFocused()

  // The wheel moves it (the reader is up in the earlier turn), and Jump to latest comes back.
  await viewport(page).hover()
  await page.mouse.wheel(0, 200)
  await expect.poll(async () => (await view(page)).top).toBeGreaterThan(moved.top)
  await page.getByRole("button", { name: "Jump to latest" }).click()
  await expect.poll(async () => (await view(page)).below).toBeLessThan(40)
  await expect(viewport(page)).toBeFocused()
  await expect(jump(page)).toHaveAttribute("data-active", "false")
  await shot(page, "chat-scroll-desktop-jumped")

  // Reloaded, the chat opens at its last question, and nothing animates in.
  await page.reload()
  await expect(log).toContainText("in full detail")
  await expect(log.getByRole("button", { name: "Copy" })).toHaveCount(2)
  await expect.poll(async () => (await view(page)).pinned).toBeLessThan(120)
  await expect(log.locator(".animate-in")).toHaveCount(0)
  await shot(page, "chat-scroll-desktop-reloaded")
})

test("at 390px with reduced motion, Jump to latest moves at once, and the answer finishes in place", async ({
  page,
}) => {
  test.setTimeout(60_000)
  await page.emulateMedia({ reducedMotion: "reduce" })
  await page.setViewportSize(PHONE)
  await signIn(page, MEMBER)
  await ask(page, "How many sick leave days do staff get?")
  await settled(page)

  await page.evaluate(WATCH)
  await send(page, "What does the leave policy say about annual leave, in full detail?")
  await expect.poll(async () => (await view(page)).pinned).toBeLessThan(120)
  const pinned = await view(page)
  await expect(jump(page)).toHaveAttribute("data-active", "true", { timeout: 15_000 })
  expect((await view(page)).top).toBe(pinned.top)
  const frames = await spread(page)
  expect(frames.question).toBeLessThan(0.5)
  expect(frames.answer).toBeLessThan(0.5)

  // Instant: at the end straight after the click (smooth would still be on its way).
  await page.getByRole("button", { name: "Jump to latest" }).click()
  expect((await view(page)).below).toBeLessThan(60)
  // No following after that: the answer keeps growing below the view.
  const jumped = await view(page)
  await waitForAnswer(page, 1)
  expect((await view(page)).top).toBe(jumped.top)
  await expect(page.getByRole("status")).toHaveText(/^Answer ready/)
  await shot(page, "chat-scroll-phone-done")
})
