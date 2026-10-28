import { expect, test, type Page } from "@playwright/test"

import { ask, conversation, DESKTOP, fitsWidth, MEMBER, PHONE, signIn } from "./helpers.ts"

// The empty new chat, centred (chat-view.tsx, chat-home.tsx): the heading and the
// composer in the middle of the column, the recent chats, spaces and folders under
// them; sending moves the composer down to its place. And the art answering the
// pointer (redacted-drift.tsx): a reading lens under a mouse, a click decoding a line.
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
    // The fine print stays under it.
    await expect(page.getByText(/Searching as/)).toBeVisible()
    expect(await fitsWidth(page)).toBe(true)
  })
}

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
  // Spaces to ask in, as chips.
  await expect(page.getByRole("group", { name: "Spaces" }).getByRole("button", { name: "All company" })).toHaveAttribute(
    "aria-pressed",
    "true",
  )
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
