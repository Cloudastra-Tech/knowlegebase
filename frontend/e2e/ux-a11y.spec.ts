import AxeBuilder from "@axe-core/playwright"
import { expect, test, type Page } from "@playwright/test"

import { ADMIN, ask, DESKTOP, fitsWidth, MEMBER, PHONE, shot, signIn } from "./helpers.ts"

// Accessibility (axe-core) on the main screens, at a desktop and a phone width: a
// serious or critical violation fails the run. And at 390px nothing is wider than the
// screen: the page itself never scrolls sideways (the header's screens go in a menu,
// the Access tabs in a picker). Runs last (file order): it asks one question.

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
  ["digests", "/digests"],
  ["spaces", "/spaces"],
  ["prompts", "/prompts"],
  ["api-keys", "/api-keys"],
  ["connections", "/connections"],
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
    for (const [screen, url] of SCREENS) {
      await page.goto(url)
      await settle(page)
      await shot(page, `a11y-${name}-${screen}`)
      found.push(...(await serious(page)).map((v) => `${url}: ${v}`))
      if (size === PHONE) expect(await fitsWidth(page), `${url} is wider than the phone`).toBe(true)
    }
    expect(found).toEqual([])
  })
}

test("an answer, and the phone menus, have no serious accessibility problems", async ({ page }) => {
  await page.setViewportSize(PHONE)
  await signIn(page, MEMBER)
  await ask(page, "How many days of annual leave do I get?")
  expect(await serious(page)).toEqual([])
  expect(await fitsWidth(page)).toBe(true)

  await page.context().clearCookies()
  await page.evaluate("localStorage.clear()")
  await signIn(page, ADMIN)
  // The header's screens are one menu on a phone, named after the open screen.
  await page.getByRole("button", { name: "Chat, switch screen" }).click()
  await page.getByRole("menuitem", { name: "Access" }).click()
  await expect(page).toHaveURL(/\/admin\/sign-in/)
  // The Access tabs are a picker.
  await page.getByRole("combobox", { name: "Access section" }).click()
  await page.getByRole("option", { name: "Members" }).click()
  await expect(page).toHaveURL(/\/admin\/members/)
  await settle(page)
  expect(await serious(page)).toEqual([])
  expect(await fitsWidth(page)).toBe(true)
})
