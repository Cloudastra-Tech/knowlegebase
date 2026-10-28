import AxeBuilder from "@axe-core/playwright"
import { expect, test, type Page } from "@playwright/test"

import { ADMIN, ask, DESKTOP, fitsWidth, MEMBER, overflowingScrollers, PHONE, shot, signIn } from "./helpers.ts"

// Accessibility (axe-core) on the main screens, at a desktop and a phone width: a
// serious or critical violation fails the run. And at 390px nothing is wider than the
// screen: the page itself never scrolls sideways (the sidebar is a sheet,
// the Access tabs in a picker), and no box that scrolls inside it is pushed sideways
// (overflowingScrollers). Runs last (file order): it asks one question.

const SCREENS = [
  ["chat", "/chat"],
  ["knowledge", "/knowledge"],
  ["documents", "/documents"],
  ["integrations", "/integrations"],
  ["insights", "/admin/insights"],
  ["not-found", "/admin/insights/not-found"],
  ["feedback", "/admin/insights/feedback"],
  ["verified", "/admin/verified"],
  ["assistant", "/admin/assistant"],
  ["sign-in", "/admin/sign-in"],
  ["members", "/admin/members"],
  ["groups", "/admin/groups"],
  ["activity", "/admin/activity"],
  ["spaces", "/spaces"],
  ["settings", "/settings"],
  ["settings-general", "/settings/general"],
  ["settings-chats", "/settings/chats"],
  ["settings-connections", "/settings/connections"],
  ["settings-prompts", "/settings/prompts"],
  ["settings-digests", "/settings/digests"],
  ["settings-api-keys", "/settings/api-keys"],
  ["settings-billing", "/settings/billing"],
  ["settings-security", "/settings/security"],
] as const

async function serious(page: Page) {
  const { violations } = await new AxeBuilder({ page }).analyze()
  return violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)
}

// The screen has drawn (its title, or the chat's box), and nothing is loading.
async function settle(page: Page) {
  await expect(
    page.getByRole("heading", { level: 2 }).or(page.getByRole("textbox", { name: "Your question" })).first(),
  ).toBeVisible()
  await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0, { timeout: 10_000 })
}

test("the landing page has no serious accessibility problems", async ({ page }) => {
  for (const size of [DESKTOP, PHONE]) {
    await page.setViewportSize(size)
    await page.goto("/")
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
    // Down the page once, so each demo has drawn its first frame.
    for (const id of ["answer", "access", "privacy", "sources", "start"]) {
      await page.locator(`#${id}`).scrollIntoViewIfNeeded()
    }
    expect(await serious(page)).toEqual([])
    expect(await fitsWidth(page)).toBe(true)
  }
})

test("the sign-in page has no serious accessibility problems", async ({ page }) => {
  for (const size of [DESKTOP, PHONE]) {
    await page.setViewportSize(size)
    await page.goto("/login")
    await expect(page.getByLabel("Username or email")).toBeVisible()
    expect(await serious(page)).toEqual([])
    expect(await fitsWidth(page)).toBe(true)
  }
})

for (const [name, size] of [
  ["desktop", DESKTOP],
  ["phone", PHONE],
] as const) {
  test(`the main screens have no serious accessibility problems (${name})`, async ({ page }) => {
    test.setTimeout(120_000)
    await page.setViewportSize(size)
    await signIn(page, ADMIN)
    const found: string[] = []
    const wide: string[] = []
    for (const [screen, url] of SCREENS) {
      await page.goto(url)
      await settle(page)
      await shot(page, `a11y-${name}-${screen}`)
      found.push(...(await serious(page)).map((v) => `${url}: ${v}`))
      if (size === PHONE) {
        expect(await fitsWidth(page), `${url} is wider than the phone`).toBe(true)
        wide.push(...(await overflowingScrollers(page)).map((s) => `${url}: ${s}`))
      }
    }
    expect(found).toEqual([])
    expect(wide, "scrolling boxes pushed sideways at 390px").toEqual([])
  })
}

test("an answer, and the phone menus, have no serious accessibility problems", async ({ page }) => {
  await page.setViewportSize(PHONE)
  await signIn(page, MEMBER)
  await ask(page, "How many days of annual leave do I get?")
  expect(await serious(page)).toEqual([])
  expect(await fitsWidth(page)).toBe(true)
  expect(await overflowingScrollers(page)).toEqual([])

  await page.context().clearCookies()
  await page.evaluate("localStorage.clear()")
  await signIn(page, ADMIN)
  // On a phone the sidebar is a sheet from the left, opened from the top bar; a screen
  // picked in it closes it.
  await page.getByRole("button", { name: "Open menu" }).click()
  const sheet = page.getByRole("dialog", { name: "Menu" })
  await expect(sheet.getByRole("navigation", { name: "Screens" })).toBeVisible()
  expect(await serious(page)).toEqual([])
  await sheet.getByRole("link", { name: "Access" }).click()
  await expect(page).toHaveURL(/\/admin\/sign-in/)
  await expect(sheet).toHaveCount(0)
  // The Access tabs are a picker.
  await page.getByRole("combobox", { name: "Access section" }).click()
  await page.getByRole("option", { name: "Members" }).click()
  await expect(page).toHaveURL(/\/admin\/members/)
  await settle(page)
  expect(await serious(page)).toEqual([])
  expect(await fitsWidth(page)).toBe(true)
})
