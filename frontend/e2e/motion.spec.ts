import { expect, test, type Page } from "@playwright/test"

import { ADMIN, ask, conversation, MEMBER, signIn } from "./helpers.ts"

// The motion and theme contracts (DESIGN.md, "Theme" and "Motion"): the app field
// holds still with reduced motion, dark is the default and a Light choice is kept,
// the sign-in pages stay dark (overlays too) whatever the choice, and a chat loaded
// from history doesn't animate. Strings to page.evaluate, so the e2e tsconfig needs no
// DOM types.

// index.css, .dark --popover: oklch(0.205 0.005 165), as the built CSS writes it or not.
const DARK_POPOVER = /^oklch\((0\.205|20\.5%) /

// The app's background field (AppBackdrop), drawn: its pixels, as a data URL.
async function fieldPixels(page: Page) {
  return page.evaluate(`(() => {
    const canvas = document.querySelector('[data-slot="ascii-field"] canvas')
    return canvas && canvas.width ? canvas.toDataURL() : null
  })()`) as Promise<string | null>
}

async function pickTheme(page: Page, name: "Dark" | "Light" | "System") {
  await page.getByRole("button", { name: /^Account, / }).click()
  await page.getByRole("menuitemradio", { name }).click()
  await page.keyboard.press("Escape")
}

const htmlClass = (page: Page) => page.evaluate("document.documentElement.className") as Promise<string>

test("with reduced motion the app's field holds still, and moves again when it's off", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" })
  await signIn(page, ADMIN)
  await page.goto("/knowledge")
  await expect(page.getByRole("heading", { level: 2 })).toBeVisible()
  await expect.poll(() => fieldPixels(page)).not.toBeNull()
  // Drawn after the fonts arrive; then it's one frame.
  await page.evaluate("document.fonts.ready")
  await page.waitForTimeout(300)
  const still = await fieldPixels(page)
  await page.waitForTimeout(1000)
  expect(await fieldPixels(page)).toBe(still)

  // The setting is followed live: without it the field moves (12 fps).
  await page.emulateMedia({ reducedMotion: "no-preference" })
  await expect.poll(() => fieldPixels(page), { timeout: 5_000 }).not.toBe(still)
})

test("dark is the default, and a Light choice is kept after a reload", async ({ page }) => {
  await signIn(page, ADMIN)
  expect((await htmlClass(page)).split(" ")).toContain("dark")

  await pickTheme(page, "Light")
  await expect.poll(() => htmlClass(page)).toContain("light")
  await page.reload()
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible()
  const after = (await htmlClass(page)).split(" ")
  expect(after).toContain("light")
  expect(after).not.toContain("dark")
})

test("the sign-in page and an overlay on it are dark with a Light choice", async ({ page }) => {
  await signIn(page, MEMBER)
  await pickTheme(page, "Light")
  await expect.poll(() => htmlClass(page)).toContain("light")

  // Signing out everywhere lands on the landing page with a toast (an overlay, in
  // <body> outside the page). The pointer rests on it, which holds it up, while the
  // keyboard opens the sign-in page.
  await page.getByRole("button", { name: /^Account, / }).click()
  await page.getByRole("menuitem", { name: "Sign out everywhere" }).click()
  await page.getByRole("alertdialog").getByRole("button", { name: "Sign out everywhere" }).click()
  const toast = page.locator("[data-sonner-toast]").filter({ hasText: "Signed out on every device." })
  await toast.hover()
  await page.getByRole("button", { name: "Sign in" }).first().press("Enter")
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByLabel("Username or email")).toBeVisible()
  await expect(toast).toBeVisible()

  expect((await htmlClass(page)).split(" ")).toContain("dark")
  const popover = await page.evaluate(`(() => {
    const el = [...document.querySelectorAll("[data-sonner-toast]")].find((t) => t.textContent.includes("Signed out on every device."))
    return getComputedStyle(el).getPropertyValue("--popover").trim()
  })()`)
  expect(popover).toMatch(DARK_POPOVER)
  await expect(page.locator("[data-sonner-toaster]")).toHaveAttribute("data-sonner-theme", "dark")
  // The choice itself is kept for the app.
  expect(await page.evaluate("localStorage.getItem('theme')")).toBe("light")
})

test("a chat opened from history doesn't animate", async ({ page }) => {
  await signIn(page, MEMBER)
  await ask(page, "How many days of paid annual leave do employees get?")
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/)

  await page.reload()
  const log = conversation(page)
  await expect(log.getByRole("button", { name: "Copy" })).toHaveCount(1)
  // At once, while an entrance would still be running (they're 150-280ms). Animations
  // on a scroll timeline (the scroller's edge fades) follow the scroll, not the clock.
  const running = await page.evaluate(`document.getAnimations()
    .filter((a) => a.playState === "running" || a.playState === "pending")
    .filter((a) => !a.timeline || a.timeline instanceof DocumentTimeline)
    .map((a) => {
      const el = a.effect && a.effect.target
      return (a.animationName || a.transitionProperty || "animation") + " on " +
        (el ? el.tagName.toLowerCase() + (el.dataset && el.dataset.slot ? "[" + el.dataset.slot + "]" : "") + "." + String(el.className).slice(0, 60) : "?")
    })`)
  expect(running).toEqual([])
})
