import AxeBuilder from "@axe-core/playwright"
import { expect, test, type Locator, type Page } from "@playwright/test"

import { ADMIN, DESKTOP, FRESH, MEMBER, PHONE, fitsWidth, signIn } from "./helpers.ts"

// The product tour (components/product-tour.tsx, lib/tour.ts): it starts by itself on
// a member's first sign-in (FRESH, the one seeded account that hasn't seen it), goes
// step by step, and the server keeps that it was seen, so it doesn't start again in
// another browser; Skip and Esc close it; the account menu runs it again, and the
// admin tour for admins. While it runs nothing in the app moves. The specs run in
// file order: the first one is FRESH's first sign-in.

const MEMBERS_TOUR = [
  "Ask in plain words",
  "Pick the model", // only with more than one model to pick from
  "Check where it came from",
  "Save the questions you repeat",
  "Find a past chat",
  "Settings, and this tour again",
]

const tourCard = (page: Page) => page.locator("[data-tour-card]")

// A step is shown: the card is named by its title, and not on its way to the next.
async function shown(page: Page, title?: string): Promise<Locator> {
  const card = title ? page.getByRole("dialog", { name: title }) : tourCard(page)
  await expect(card).toBeVisible({ timeout: 10_000 })
  await expect(card.locator("[aria-busy]")).toHaveCount(0, { timeout: 10_000 })
  return card
}

async function serious(page: Page) {
  const { violations } = await new AxeBuilder({ page }).analyze()
  return violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)
}

const composer = (page: Page) => page.locator('[data-tour="composer"]').boundingBox()

async function openAccountMenu(page: Page) {
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByRole("button", { name: "Open menu" }).first().click()
  await page.getByRole("button", { name: /^Account,/ }).click()
}

test("starts on a fresh member's first sign-in, goes step by step, and is kept on the server", async ({
  page,
  browser,
}) => {
  await page.setViewportSize(DESKTOP)
  await signIn(page, FRESH)
  let card = await shown(page, "Ask in plain words")
  await expect(card).toHaveAttribute("aria-modal", "true")
  await expect(card.getByRole("button", { name: "Next" })).toBeFocused()
  expect(await serious(page)).toEqual([])

  // Next through to Done: the steps in order (the model's only with a choice of
  // models), the prompts one on its own page.
  const seen: string[] = []
  for (;;) {
    card = await shown(page)
    const title = (await card.getByRole("heading").textContent()) ?? ""
    seen.push(title)
    if (title === "Save the questions you repeat") await expect(page).toHaveURL(/\/settings\/prompts$/)
    const done = card.getByRole("button", { name: "Done" })
    if (await done.count()) {
      await done.click()
      break
    }
    await card.getByRole("button", { name: "Next" }).click()
    await expect(page.getByRole("dialog", { name: title })).toHaveCount(0)
  }
  expect(seen).toEqual(MEMBERS_TOUR.filter((t) => t !== "Pick the model" || seen.includes(t)))
  await expect(tourCard(page)).toHaveCount(0)
  await expect(page).toHaveURL(/\/chat$/) // back where it started

  // Seen: another browser doesn't start it again.
  const other = await browser.newPage({ viewport: DESKTOP })
  await signIn(other, FRESH)
  await expect(other.getByRole("textbox", { name: "Your question" })).toBeVisible()
  await other.waitForTimeout(2500)
  await expect(tourCard(other)).toHaveCount(0)
  await other.close()
})

test("runs again from the account menu without moving the app; Esc and Skip close it", async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await signIn(page, FRESH)
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible()
  await page.waitForTimeout(1500) // the empty chat has settled
  const before = await composer(page)

  await openAccountMenu(page)
  await page.getByRole("menuitem", { name: "Take the tour" }).click()
  let card = await shown(page, "Ask in plain words")
  // The overlay takes nothing from the page: the composer is where it was.
  expect(await composer(page)).toEqual(before)
  // Its keys: → and ← go, Esc closes, and the focus is back on the menu's button.
  await page.keyboard.press("ArrowRight")
  card = await shown(page)
  await expect(card.getByRole("heading")).not.toHaveText("Ask in plain words")
  await page.keyboard.press("ArrowLeft")
  await shown(page, "Ask in plain words")
  expect(await composer(page)).toEqual(before)
  await page.keyboard.press("Escape")
  await expect(tourCard(page)).toHaveCount(0)
  await expect(page.getByRole("button", { name: /^Account,/ })).toBeFocused()
  expect(await composer(page)).toEqual(before)

  await openAccountMenu(page)
  await page.getByRole("menuitem", { name: "Take the tour" }).click()
  card = await shown(page, "Ask in plain words")
  await card.getByRole("button", { name: "Skip" }).click()
  await expect(tourCard(page)).toHaveCount(0)
  // A member has no admin tour.
  await openAccountMenu(page)
  await expect(page.getByRole("menuitem", { name: "Take the tour" })).toBeVisible()
  await expect(page.getByRole("menuitem", { name: "Admin tour" })).toHaveCount(0)
})

test("the admin tour goes through the admin screens, from the account menu", async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await signIn(page, ADMIN)
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible()
  await openAccountMenu(page)
  await page.getByRole("menuitem", { name: "Admin tour" }).click()
  for (const [title, url] of [
    ["Connect your sources", /\/integrations$/],
    ["Choose the models", /\/admin\/assistant$/],
    ["Decide who sees what", /\/admin\/members$/],
    ["See what people couldn't find", /\/admin\/insights$/],
  ] as const) {
    const card = await shown(page, title)
    await expect(page).toHaveURL(url)
    await card.getByRole("button", { name: /^(Next|Done)$/ }).click()
  }
  await expect(tourCard(page)).toHaveCount(0)
  await expect(page).toHaveURL(/\/chat$/)
})

test("on a phone the card docks to the screen's edge and fits", async ({ page }) => {
  await page.setViewportSize(PHONE)
  await signIn(page, MEMBER)
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible()
  await openAccountMenu(page)
  await page.getByRole("menuitem", { name: "Take the tour" }).click()
  const card = await shown(page, "Ask in plain words")
  const box = (await card.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(15)
  expect(box.x + box.width).toBeLessThanOrEqual(PHONE.width - 15)
  expect(box.y + box.height).toBeGreaterThan(PHONE.height - 40) // at the bottom
  expect(await fitsWidth(page)).toBe(true)
  expect(await serious(page)).toEqual([])
  await page.keyboard.press("Escape")
  await expect(tourCard(page)).toHaveCount(0)
})
