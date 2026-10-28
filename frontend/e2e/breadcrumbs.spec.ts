import { expect, test, type Page } from "@playwright/test"

import { ADMIN, DESKTOP, fitsWidth, PHONE, signIn } from "./helpers.ts"

// Breadcrumbs over every screen's title (lib/breadcrumbs.ts): the ancestors are links
// that go there through the router; on a phone a detail view has "← Parent" instead,
// to its logical parent (not history), so a deep link has one too.

const crumbs = (page: Page) => page.getByRole("navigation", { name: "Breadcrumb" })
const here = (page: Page) => crumbs(page).locator('[aria-current="page"]')

async function openMySpace(page: Page) {
  await page.goto("/spaces")
  await expect(page.getByRole("heading", { level: 2, name: "Spaces" })).toBeVisible()
  await page.getByRole("main").getByRole("link", { name: /My space/ }).click()
  await expect(page).toHaveURL(/\/spaces\/[^/]+$/)
  await expect(page.getByRole("heading", { level: 2, name: "My space" })).toBeVisible()
}

test("Spaces › a space › its tab, and back to Spaces from the crumb", async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await signIn(page, ADMIN)
  await openMySpace(page)

  await expect(crumbs(page).getByRole("link", { name: "Spaces" })).toHaveAttribute("href", "/spaces")
  await expect(crumbs(page).getByRole("link", { name: "My space" })).toBeVisible()
  await expect(here(page)).toHaveText("Files")
  // The open tab is in the address, and in the crumbs.
  await page.getByRole("tab", { name: "Sources" }).click()
  await expect(page).toHaveURL(/\?tab=sources$/)
  await expect(here(page)).toHaveText("Sources")
  // Esc doesn't go anywhere.
  const url = page.url()
  await page.keyboard.press("Escape")
  expect(page.url()).toBe(url)

  await crumbs(page).getByRole("link", { name: "Spaces" }).click()
  await expect(page).toHaveURL(/\/spaces$/)
  await expect(page.getByRole("heading", { level: 2, name: "Spaces" })).toBeVisible()
  await expect(here(page)).toHaveText("Spaces")
})

test("on a phone, a space has a back button to Spaces, also from a deep link", async ({ page }) => {
  await page.setViewportSize(PHONE)
  await signIn(page, ADMIN)
  await openMySpace(page)
  const space = page.url()
  await expect(crumbs(page)).toBeHidden() // the back button stands for them
  await page.getByRole("link", { name: "Back to Spaces" }).click()
  await expect(page).toHaveURL(/\/spaces$/)

  // Straight to the space: there's no history to go back to, and the button still works.
  await page.goto(space)
  await page.getByRole("link", { name: "Back to Spaces" }).click()
  await expect(page).toHaveURL(/\/spaces$/)
  await expect(page.getByRole("heading", { level: 2, name: "Spaces" })).toBeVisible()
  expect(await fitsWidth(page)).toBe(true)
})

test("Access › Webhooks: the tab is the crumb, and Access goes to its first tab", async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await signIn(page, ADMIN)
  await page.goto("/admin/webhooks")
  await expect(page.getByRole("heading", { level: 2, name: "Access" })).toBeVisible()
  await expect(here(page)).toHaveText("Webhooks")
  // The tabs stay.
  await expect(page.getByRole("tab", { name: "Webhooks" })).toHaveAttribute("aria-selected", "true")
  await crumbs(page).getByRole("link", { name: "Access" }).click()
  await expect(page).toHaveURL(/\/admin\/sign-in$/)
  await expect(here(page)).toHaveText("Sign-in")

  // A personal screen's old address: its Settings section, under Settings.
  await page.goto("/api-keys")
  await expect(page).toHaveURL(/\/settings\/api-keys$/)
  await expect(here(page)).toHaveText("API keys")
  await page.getByRole("navigation", { name: "Settings" }).getByRole("link", { name: "Digests" }).click()
  await expect(page).toHaveURL(/\/settings\/digests$/)
  await expect(here(page)).toHaveText("Digests")
  await crumbs(page).getByRole("link", { name: "Settings" }).click()
  await expect(page).toHaveURL(/\/settings\/general$/)
})

// The crumb row is one height on every screen (the back button too, on a phone), so the
// title doesn't move from one screen to the next.
test("the title sits at the same height on every screen, crumbs or back button", async ({ page }) => {
  test.setTimeout(90_000)
  for (const size of [DESKTOP, PHONE]) {
    await page.setViewportSize(size)
    if (size === DESKTOP) await signIn(page, ADMIN)
    await openMySpace(page)
    const space = new URL(page.url()).pathname
    const tops = new Map<string, number>()
    for (const url of [
      "/knowledge",
      "/spaces",
      space,
      "/admin/webhooks",
      "/admin/insights/not-found",
      "/api-keys",
      "/documents?tab=drafts",
    ]) {
      await page.goto(url)
      const title = page.locator("[data-screen] h2").first()
      await expect(title).toBeVisible()
      await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0, { timeout: 10_000 })
      // The screen's entrance (it rises 6px) is over.
      await expect
        .poll(() =>
          page.evaluate(
            "document.querySelector('[data-screen]').getAnimations().every((a) => a.playState !== 'running')",
          ),
        )
        .toBe(true)
      tops.set(url, Math.round((await title.boundingBox())!.y))
    }
    expect(new Set(tops.values()).size, JSON.stringify([...tops])).toBe(1)
  }
})
