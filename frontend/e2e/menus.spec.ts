import { expect, test, type Page } from "@playwright/test"

import { ADMIN, ask, DESKTOP, MEMBER, PHONE, signIn } from "./helpers.ts"

// The item is the topmost element at its own centre, so it can really be seen and clicked.
async function onTop(page: Page, role: string, box: { x: number; y: number; width: number; height: number }) {
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2]
  return page.evaluate(
    `document.elementFromPoint(${x}, ${y})?.closest('[role="${role}"]')?.textContent ?? ""`,
  ) as Promise<string>
}

// A submenu opens beside its glass menu, on top of the page: not clipped inside the
// parent (whose backdrop-filter would contain a fixed-position child).
test("a chat's Move to folder submenu is visible and usable", async ({ page }) => {
  await signIn(page, MEMBER)
  await ask(page, "How many days of paid annual leave do employees get?")

  const row = page.getByRole("button", { name: /^Options for / }).first()
  await row.click({ force: true })
  await page.getByRole("menuitem", { name: "Move to folder" }).hover()
  const newFolder = page.getByRole("menuitem", { name: "New folder" })
  await expect(newFolder).toBeVisible()
  expect(await onTop(page, "menuitem", (await newFolder.boundingBox())!)).toContain("New folder")
})

// A 13" laptop's window.
const SHORT = { width: 1366, height: 728 }

test("on a short window the Admin group starts folded, opens from its label, and stays so", async ({ page }) => {
  await page.setViewportSize(SHORT)
  await signIn(page, ADMIN)
  const nav = page.getByRole("navigation", { name: "Screens" })
  const label = nav.getByRole("button", { name: "Admin" })
  await expect(label).toHaveAttribute("aria-expanded", "false")
  await expect(nav.getByRole("link", { name: "Insights" })).toHaveCount(0)
  // Room left for the chats, and the account at the foot, inside the window.
  const history = (await page.getByRole("region", { name: "Chat history" }).boundingBox())!
  expect(history.height).toBeGreaterThan(200)
  const account = page.getByRole("button", { name: /^Account, / })
  const foot = (await account.boundingBox())!
  expect(foot.y + foot.height).toBeLessThanOrEqual(SHORT.height)

  await label.click()
  await expect(nav.getByRole("link", { name: "Insights" })).toBeVisible()
  await page.reload()
  await expect(nav.getByRole("link", { name: "Insights" })).toBeVisible()
  const open = (await account.boundingBox())!
  expect(open.y + open.height).toBeLessThanOrEqual(SHORT.height)
})

test("on a tall window the Admin group is open", async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await signIn(page, ADMIN)
  const nav = page.getByRole("navigation", { name: "Screens" })
  await expect(nav.getByRole("button", { name: "Admin" })).toHaveAttribute("aria-expanded", "true")
  await expect(nav.getByRole("link", { name: "Verified" })).toBeVisible()
})

test("an admin's account menu fits a short window, with View as and Appearance beside it", async ({ page }) => {
  await page.setViewportSize(SHORT)
  await signIn(page, ADMIN)
  await page.getByRole("button", { name: /^Account, / }).click()
  const menu = page.getByRole("menu")
  await expect(menu.getByRole("menuitem", { name: "Sign out" })).toBeVisible()
  // Whole, without scrolling inside it.
  const box = (await menu.boundingBox())!
  expect(box.y).toBeGreaterThanOrEqual(0)
  expect(box.y + box.height).toBeLessThanOrEqual(SHORT.height)
  const scrolls = `(() => { const m = document.querySelector('[role="menu"]'); return m.scrollHeight > m.clientHeight })()`
  expect(await page.evaluate(scrolls)).toBe(false)

  // Each submenu opens on top of the page, not clipped inside the glass menu.
  for (const [trigger, item] of [
    ["Appearance", "Light"],
    [/^View as/, "Public user"],
  ] as const) {
    await page.getByRole("menuitem", { name: trigger }).hover()
    const radio = page.getByRole("menuitemradio", { name: item })
    await expect(radio).toBeVisible()
    expect(await onTop(page, "menuitemradio", (await radio.boundingBox())!)).toContain(item)
  }
})

// Short, as ChatGPT's and Claude.ai's: everything personal is in Settings.
test("the account menu is short: who you are, Settings, View as for an admin, Appearance, Sign out", async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP)
  for (const [account, expected] of [
    [MEMBER, ["Settings", "Appearance", "Keyboard shortcuts", "Sign out"]],
    [ADMIN, ["Settings", "View as", "Appearance", "Keyboard shortcuts", "Sign out"]],
  ] as const) {
    await page.context().clearCookies()
    await page.goto("/login")
    await page.evaluate("localStorage.clear()")
    await signIn(page, account)
    await page.getByRole("button", { name: /^Account, / }).click()
    const menu = page.getByRole("menu")
    await expect(menu.getByText(account.email)).toBeVisible()
    const items = (await menu.getByRole("menuitem").allTextContents()).map((t) =>
      t.replace(/(Ctrl|⌘)\/$/, "").replace(/(HR \/ Admin|Public)$/, "").trim(),
    )
    expect(items).toEqual(expected)
    // Keyboard shortcuts opens the list of them; Settings opens Settings.
    await menu.getByRole("menuitem", { name: "Keyboard shortcuts" }).click()
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible()
    await page.keyboard.press("Escape")
    await page.getByRole("button", { name: /^Account, / }).click()
    await page.getByRole("menuitem", { name: "Settings" }).click()
    await expect(page).toHaveURL(/\/settings\/general$/)
    await expect(page.getByRole("heading", { level: 2, name: "Settings" })).toBeVisible()
  }
})

test("on a phone the account menu has its View as and theme in place, inside the screen", async ({ page }) => {
  await page.setViewportSize(PHONE)
  await signIn(page, ADMIN)
  await page.getByRole("button", { name: "Open menu" }).click()
  await page.getByRole("button", { name: /^Account, / }).click()
  const menu = page.getByRole("menu")
  // No submenu beside it (no room on a phone): the choices are in the menu.
  await expect(menu.getByRole("menuitemradio", { name: "Public user" })).toBeVisible()
  await expect(menu.getByRole("menuitemradio", { name: "Light" })).toBeVisible()
  const box = (await menu.boundingBox())!
  expect(box.y).toBeGreaterThanOrEqual(0)
  expect(box.y + box.height).toBeLessThanOrEqual(PHONE.height)
  expect(box.x + box.width).toBeLessThanOrEqual(PHONE.width)
})
