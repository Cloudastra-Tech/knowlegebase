import { expect, test, type Page } from "@playwright/test"

import { ask, conversation, DESKTOP, fitsWidth, MEMBER, PHONE, signIn } from "./helpers.ts"

// The empty new chat, centred (chat-view.tsx, chat-home.tsx): the heading and the
// composer in the middle of the column, the recent chats under them in one glass card
// on the composer's width and left edge, who you search as at the foot; sending moves
// the composer down to its place. And the art (redacted-drift.tsx): over the whole
// page, frosted under the glass, answering the pointer with a reading lens under a
// mouse and a click decoding a line.
// Strings to page.evaluate, so the e2e tsconfig needs no DOM types.

// Headless Chromium hides scrollbars (Playwright passes --hide-scrollbars); shown, as a
// desktop browser shows them, a sideways shift from a gutter would be measurable.
test.use({ launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] } })

type Box = { x: number; y: number; width: number; height: number }

const composerBox = (page: Page) => page.locator('[data-drift="composer"]').boundingBox() as Promise<Box>
const drift = (page: Page) => page.locator('[data-mode="drift"]')

// The art's lines, on the page: the ones over the heading (blank space to point at).
async function linesAbove(page: Page) {
  const art = drift(page)
  await expect(art).toHaveAttribute("data-lines", /\d/)
  const rect = (await art.boundingBox()) as Box
  const heading = (await page.locator('[data-drift="content"]').boundingBox()) as Box
  const ys = ((await art.getAttribute("data-lines")) ?? "").split(",").map((y) => rect.y + Number(y))
  return { rect, ys: ys.filter((y) => y < heading.y - 24).sort((a, b) => b - a) }
}

// The art's pixels round a point (page coordinates), as a data URL.
const pixelsAround = (page: Page, x: number, y: number) =>
  page.evaluate(`(() => {
    const c = document.querySelector('[data-mode="drift"] canvas')
    const r = c.getBoundingClientRect()
    const k = c.width / r.width
    const out = document.createElement("canvas")
    out.width = 200; out.height = 60
    out.getContext("2d").drawImage(c, (${x} - r.left - 100) * k, (${y} - r.top - 30) * k, 200 * k, 60 * k, 0, 0, 200, 60)
    return out.toDataURL()
  })()`) as Promise<string>

for (const size of [DESKTOP, PHONE]) {
  test(`at ${size.width}px the empty chat's composer sits in the middle`, async ({ page }) => {
    await page.setViewportSize(size)
    await signIn(page, MEMBER)
    await expect(page.getByRole("heading", { level: 2 })).toBeVisible()
    const box = await composerBox(page)
    const centre = box.y + box.height / 2
    expect(Math.abs(centre - size.height / 2)).toBeLessThan(size.height * 0.12)
    // Who you search as, and the logging notice: one line at the foot of the page,
    // on the composer's left edge (the hint's), in view without scrolling.
    const notice = page.getByText(/Searching as/)
    await expect(notice).toBeVisible()
    const line = (await notice.boundingBox()) as Box
    expect(line.y).toBeGreaterThan(box.y + box.height)
    expect(line.y + line.height).toBeLessThanOrEqual(size.height)
    expect(line.y + line.height).toBeGreaterThan(size.height - 90)
    expect(Math.abs(line.x - (box.x - 16))).toBeLessThanOrEqual(1)
    expect(await page.evaluate(`getComputedStyle(document.querySelector('[data-slot="chat-fine-print"]')).paddingLeft`)).toBe("36px")
    expect(await fitsWidth(page)).toBe(true)
  })
}

test("the keys are said under the composer only while it's focused and empty", async ({ page }) => {
  await signIn(page, MEMBER)
  const hint = page.locator('[data-slot="composer-hint"]')
  const input = page.getByRole("textbox", { name: "Your question" })
  await input.blur()
  await expect(hint).toHaveText("")
  const row = (await hint.locator("..").boundingBox()) as Box
  await input.focus()
  await expect(hint).toHaveText(/^Enter to send/)
  await input.fill("Leave")
  await expect(hint).toHaveText("")
  // Its row keeps its height: nothing moves as it comes and goes.
  expect(await hint.locator("..").boundingBox()).toEqual(row)
})

test("the art covers the whole page, and the composer is frosted glass over it", async ({ page }) => {
  await signIn(page, MEMBER)
  const art = drift(page)
  await expect(art).toHaveAttribute("data-lines", /\d/)
  const column = (await page.locator('[data-slot="chat-backdrop"]').boundingBox()) as Box
  const canvas = (await art.locator("canvas:not([data-frost])").boundingBox()) as Box
  // The canvas is the whole window: the chat column top to bottom, and the sidebar's
  // pane beside it, which frosts it.
  expect(canvas.y).toBeLessThanOrEqual(column.y + 1)
  expect(canvas.y + canvas.height).toBeGreaterThanOrEqual(column.y + column.height - 1)
  expect(canvas.x).toBeLessThanOrEqual(1)
  expect(canvas.x + canvas.width).toBeGreaterThanOrEqual(column.x + column.width - 1)
  // Its lines run from near the top to near the bottom, under the home's sections too.
  const ys = ((await art.getAttribute("data-lines")) ?? "").split(",").map((y) => canvas.y + Number(y))
  expect(Math.min(...ys)).toBeLessThan(column.y + column.height * 0.15)
  expect(Math.max(...ys)).toBeGreaterThan(column.y + column.height * 0.85)
  const composer = await composerBox(page)
  expect(ys.some((y) => y > composer.y + composer.height + 60)).toBe(true)

  // Glass you can see through: the composer blurs what's behind it. Over the art, the
  // art does that blur itself (redacted-drift.tsx): the glass is marked and has no
  // backdrop-filter, and a frost layer sits under it, its exact box.
  const blur = `getComputedStyle(document.querySelector('[data-drift="composer"]')).backdropFilter`
  const glass = page.locator('[data-drift="composer"]')
  await expect(glass).toHaveAttribute("data-drift-frost", "")
  expect(await page.evaluate(blur)).toBe("none")
  expect(await page.evaluate(`getComputedStyle(document.querySelector('[data-drift="composer"]')).getPropertyValue("--glass-blur")`)).toMatch(/^[1-9]/)
  const box = (await glass.boundingBox()) as Box
  const layers = await art.locator("canvas[data-frost]").evaluateAll((cs) =>
    cs.map((c) => {
      const r = c.getBoundingClientRect()
      return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)].join(",")
    }),
  )
  expect(layers).toContain([box.x, box.y, box.width, box.height].map(Math.round).join(","))

  // "Reduce effects": solid, no blur.
  await page.evaluate(`localStorage.setItem("effects", "reduced")`)
  await page.reload()
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible()
  expect(await page.evaluate(`document.documentElement.dataset.effects`)).toBe("reduced")
  expect(await page.evaluate(blur)).toBe("none")
  await expect(glass).not.toHaveAttribute("data-drift-frost")
  await expect(art.locator("canvas[data-frost]")).toHaveCount(0)
  await page.evaluate(`localStorage.removeItem("effects")`)
})

test("recent chats are under the composer, and one opens from there", async ({ page }) => {
  await signIn(page, MEMBER)
  await ask(page, "How do I claim travel expenses?")
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/)
  const saved = new URL(page.url()).pathname

  await page.goto("/chat")
  const recent = page.getByRole("region", { name: "Recent chats" })
  await expect(recent).toBeVisible()
  const row = recent.getByRole("button", { name: /How do I claim travel expenses\?/ }).first()
  await expect(row).toBeVisible()
  // One glass card on the composer's width and left edge.
  await expect(recent).toHaveAttribute("data-drift", "glass")
  const card = (await recent.boundingBox()) as Box
  const composer = await composerBox(page)
  expect(Math.abs(card.x - composer.x)).toBeLessThanOrEqual(1)
  expect(Math.abs(card.width - composer.width)).toBeLessThanOrEqual(1)
  expect(card.y).toBeGreaterThan(composer.y + composer.height)
  // No Spaces or Folders rows (the composer's space chip and the sidebar have them),
  // and no starters for someone with chats.
  await expect(page.getByRole("group", { name: "Spaces" })).toHaveCount(0)
  await expect(page.getByRole("group", { name: "Folders" })).toHaveCount(0)
  await expect(page.getByRole("group", { name: "Suggested questions" })).toHaveCount(0)
  await row.click()
  await expect(page).toHaveURL(new RegExp(`${saved}$`))
  await expect(conversation(page).getByRole("button", { name: "Copy" })).toHaveCount(1)
  await expect(page.getByRole("region", { name: "Recent chats" })).toHaveCount(0)

  // View all: Search chats.
  await page.goto("/chat")
  await page.getByRole("button", { name: "View all" }).click()
  await expect(page.getByRole("dialog", { name: "Search chats" })).toBeVisible()
})

test("sending moves the composer down in one short move, and the question pins to the top", async ({ page }) => {
  await signIn(page, MEMBER)
  const before = await composerBox(page)
  const box = page.getByRole("textbox", { name: "Your question" })
  await box.fill("How many days of paid annual leave do employees get?")
  await box.press("Enter")
  // The move: one transform animation on the composer's row, 240ms at most.
  const moves = (await page.evaluate(`document.getAnimations()
    .filter((a) => a.effect && a.effect.target && a.effect.target.contains(document.querySelector('[data-drift="composer"]')))
    .map((a) => a.effect.getTiming().duration)`)) as number[]
  expect(moves.length).toBeLessThanOrEqual(1)
  for (const d of moves) expect(d).toBeLessThanOrEqual(250)
  await expect(conversation(page).getByRole("button", { name: "Copy" })).toHaveCount(1, { timeout: 20_000 })
  const after = await composerBox(page)
  // Down only: nothing sideways.
  expect(after.x).toBe(before.x)
  expect(after.width).toBe(before.width)
  expect(after.y).toBeGreaterThan(before.y + 100)
  expect(after.y + after.height).toBeGreaterThan(DESKTOP.height - 140)
  // The question at the top of the transcript, as before.
  const viewport = (await page.locator('[data-slot="message-scroller-viewport"]').boundingBox()) as Box
  const question = (await conversation(page).getByText("How many days of paid annual leave do employees get?").first().boundingBox()) as Box
  expect(question.y - viewport.y).toBeLessThan(120)

  // A second question is pinned to the top, the composer staying where it is.
  await ask(page, "How many days of sick leave do employees get?")
  const again = await composerBox(page)
  expect(Math.abs(again.y - after.y)).toBeLessThan(2)
  const second = (await conversation(page).getByText("How many days of sick leave do employees get?").first().boundingBox()) as Box
  expect(second.y - viewport.y).toBeLessThan(120)
})

test.describe("with scrollbars shown", () => {
  test("the home's sections stay centred under the composer, and don't move when they scroll", async ({ page }) => {
    await signIn(page, MEMBER)
    const recent = page.getByRole("region", { name: "Recent chats" })
    await expect(recent).toBeVisible()
    const centre = (b: Box) => b.x + b.width / 2
    const composer = await composerBox(page)
    const home = page.locator('[data-drift="below"]')
    const before = (await home.boundingBox()) as Box
    expect(Math.abs(centre(before) - centre(composer))).toBeLessThanOrEqual(1)
    // Grown until it scrolls: same place, same width.
    await page.evaluate(`document.querySelector('[data-drift="below"]').insertAdjacentHTML("beforeend", '<div style="height:2000px"></div>')`)
    const grown = (await home.boundingBox()) as Box
    expect(grown.x).toBe(before.x)
    expect(grown.width).toBe(before.width)
    expect((await composerBox(page)).x).toBe(composer.x)
  })
})

test("with reduced motion the composer moves at once", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" })
  await signIn(page, MEMBER)
  const box = page.getByRole("textbox", { name: "Your question" })
  await box.fill("When is the office closed for holidays?")
  await box.press("Enter")
  const moving = await page.evaluate(`document.getAnimations()
    .filter((a) => a.effect && a.effect.target && a.effect.target.contains(document.querySelector('[data-drift="composer"]'))).length`)
  expect(moving).toBe(0)
})

test("a mouse over the art reads the lines near it; a click decodes one and keeps the focus", async ({ page }) => {
  await signIn(page, MEMBER)
  const { rect, ys } = await linesAbove(page)
  expect(ys.length).toBeGreaterThan(0)
  const x = rect.x + rect.width * 0.55
  const y = ys[0]
  const away = await pixelsAround(page, x, y)
  await page.mouse.move(x - 40, y)
  await page.mouse.move(x, y, { steps: 6 })
  await expect(drift(page)).toHaveAttribute("data-lens", "")
  await expect.poll(() => pixelsAround(page, x, y)).not.toBe(away)
  // Gone again once the pointer leaves the column.
  await page.mouse.move(2, 2)
  await expect(drift(page)).not.toHaveAttribute("data-lens")

  // A click on a line: it reads, and the composer keeps the focus.
  const input = page.getByRole("textbox", { name: "Your question" })
  await input.focus()
  await page.mouse.click(rect.x + rect.width * 0.3, ys[ys.length > 1 ? 1 : 0])
  await expect(drift(page)).toHaveAttribute("data-decoding", "")
  await expect(input).toBeFocused()
})

test("the lens and a click work in the bottom half too, beside the recent chats", async ({ page }) => {
  await signIn(page, MEMBER)
  await ask(page, "How do I claim travel expenses?")
  await page.goto("/chat")
  await expect(page.getByRole("region", { name: "Recent chats" })).toBeVisible()
  const art = drift(page)
  await expect(art).toHaveAttribute("data-lines", /\d/)
  const rect = (await art.boundingBox()) as Box
  const composer = await composerBox(page)
  const ys = ((await art.getAttribute("data-lines")) ?? "")
    .split(",")
    .map((y) => rect.y + Number(y))
    .filter((y) => y > composer.y + composer.height + 60)
  expect(ys.length).toBeGreaterThan(0)
  // Open space: left of the column's card.
  const column = (await page.locator('[data-slot="chat-backdrop"]').boundingBox()) as Box
  const x = (column.x + composer.x) / 2
  const y = ys[0]
  const away = await pixelsAround(page, x, y)
  await page.mouse.move(x - 40, y)
  await page.mouse.move(x, y, { steps: 6 })
  await expect(art).toHaveAttribute("data-lens", "")
  await expect.poll(() => pixelsAround(page, x, y)).not.toBe(away)
  await page.mouse.move(2, 2)
  const input = page.getByRole("textbox", { name: "Your question" })
  await input.focus()
  await page.mouse.click(x, y)
  await expect(art).toHaveAttribute("data-decoding", "")
  await expect(input).toBeFocused()
})

test("with reduced motion a mouse over the art changes nothing", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" })
  await signIn(page, MEMBER)
  await page.evaluate("document.fonts.ready")
  const { rect, ys } = await linesAbove(page)
  await page.waitForTimeout(300)
  const x = rect.x + rect.width * 0.55
  const y = ys[0]
  const still = await pixelsAround(page, x, y)
  await page.mouse.move(x - 40, y)
  await page.mouse.move(x, y, { steps: 6 })
  await page.mouse.click(x, y)
  await page.waitForTimeout(600)
  expect(await pixelsAround(page, x, y)).toBe(still)
  await expect(drift(page)).not.toHaveAttribute("data-lens")
})
