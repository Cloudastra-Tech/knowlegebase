import { expect, test } from "@playwright/test"

import { DESKTOP, fitsWidth, MEMBER, PHONE, signIn } from "./helpers.ts"

// Settings (components/settings/settings-view.tsx): a column of sections beside the
// open one from md up, a list then a page per section on a phone; the old addresses of
// the personal screens land on their section.

const SECTIONS = ["General", "Chats", "Connections", "Prompts", "Digests", "API keys", "Security"]

test("on a desktop, the sections are tabs over the open one, which switches at once and in place", async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  await signIn(page, MEMBER)
  await page.goto("/prompts")
  await expect(page).toHaveURL(/\/settings\/prompts$/)
  const nav = page.getByRole("tablist", { name: "Settings" })
  await expect(nav.getByRole("tab")).toHaveText(SECTIONS)
  await expect(page.getByText("No saved prompts yet")).toBeVisible()
  // To a hundredth of a pixel: the screen's entrance can leave a sub-pixel
  // remainder for a frame; a layout shift is whole pixels.
  const box = async () => {
    const b = (await nav.boundingBox())!
    return { x: Math.round(b.x * 100) / 100, y: Math.round(b.y * 100) / 100, w: Math.round(b.width), h: Math.round(b.height) }
  }
  // Let the screen's entrance finish first, or `before` catches it a fraction of a
  // pixel short: the tabs hold still across two reads 100ms apart.
  let last = await box()
  await expect
    .poll(async () => {
      const prev = last
      last = await box()
      return JSON.stringify(last) === JSON.stringify(prev)
    }, { intervals: [100] })
    .toBe(true)
  const before = await box()
  // Where the title starts (its box is as wide as the crumbs over it, which name the section).
  const at = async () => {
    const box = (await page.locator("[data-screen] h2").boundingBox())!
    return { x: Math.round(box.x * 100) / 100, y: Math.round(box.y * 100) / 100, height: Math.round(box.height) }
  }
  const title = await at()

  for (const name of SECTIONS) {
    await nav.getByRole("tab", { name }).click()
    await expect(nav.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true")
    await expect(page.getByRole("heading", { level: 3, name })).toBeVisible()
    // Nothing moves: the tabs and the title stay where they were.
    expect(await box()).toEqual(before)
    expect(await at()).toEqual(title)
    // Switching sections plays no animation (the empty states' 150ms fade aside, once).
    const running = (await page.evaluate(
      `document.querySelector("[data-settings-section]").getAnimations({ subtree: true })
        .filter((a) => !["settings-empty", "skeleton"].includes(a.effect.target.dataset.slot)).length`,
    )) as number
    expect(running).toBe(0)
  }
})

test("on a phone, Settings is a list, each section a page with its way back", async ({ page }) => {
  await page.setViewportSize(PHONE)
  await signIn(page, MEMBER)
  await page.goto("/settings")
  const list = page.getByRole("main").getByRole("navigation", { name: "Settings" })
  await expect(list.getByRole("link")).toHaveText(SECTIONS)
  expect(await fitsWidth(page)).toBe(true)

  await list.getByRole("link", { name: "API keys" }).click()
  await expect(page).toHaveURL(/\/settings\/api-keys$/)
  await expect(page.getByRole("heading", { level: 2, name: "API keys" })).toBeVisible()
  await expect(page.getByText("No API keys yet")).toBeVisible()
  expect(await fitsWidth(page)).toBe(true)

  await page.getByRole("link", { name: "Back to Settings" }).click()
  await expect(page).toHaveURL(/\/settings$/)
  await expect(list.getByRole("link", { name: "General" })).toBeVisible()
})
