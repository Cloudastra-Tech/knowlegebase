import { expect, test, type Page } from "@playwright/test"

import { ask, conversation, MEMBER, signIn } from "./helpers.ts"

// The empty new chat's art, "Redacted drift" (components/ascii/redacted-drift.tsx):
// the one field on an empty /chat, moving (still with reduced motion), gone once a
// question is sent (the chat's own field takes over), and never on a saved chat.
// Strings to page.evaluate, so the e2e tsconfig needs no DOM types.

// The art's canvas, drawn: its pixels, as a data URL.
async function driftPixels(page: Page) {
  return page.evaluate(`(() => {
    const canvas = document.querySelector('[data-mode="drift"] canvas')
    return canvas && canvas.width ? canvas.toDataURL() : null
  })()`) as Promise<string | null>
}

// The fields laid out on the page (a hidden one has no box), by mode.
const fieldsShown = (page: Page) =>
  page.evaluate(`[...document.querySelectorAll('[data-slot="ascii-field"]')]
    .filter((f) => f.getBoundingClientRect().width > 0)
    .map((f) => f.dataset.mode)`) as Promise<string[]>

const canvasesShown = (page: Page) =>
  page.evaluate(`[...document.querySelectorAll("canvas")].filter((c) => c.getBoundingClientRect().width > 0).length`) as Promise<number>

test("an empty chat draws the art as its one field, and it moves", async ({ page }) => {
  await signIn(page, MEMBER)
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible()
  await expect.poll(() => fieldsShown(page)).toEqual(["drift"])
  expect(await canvasesShown(page)).toBe(1)
  await expect.poll(() => driftPixels(page)).not.toBeNull()
  const before = await driftPixels(page)
  await expect.poll(() => driftPixels(page), { timeout: 3_000 }).not.toBe(before)
})

test("with reduced motion the art is one still frame", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" })
  await signIn(page, MEMBER)
  await expect.poll(() => fieldsShown(page)).toEqual(["drift"])
  await page.evaluate("document.fonts.ready")
  await page.waitForTimeout(300)
  const still = await driftPixels(page)
  expect(still).not.toBeNull()
  await page.waitForTimeout(1000)
  expect(await driftPixels(page)).toBe(still)

  // Followed live: it drifts again without it.
  await page.emulateMedia({ reducedMotion: "no-preference" })
  await expect.poll(() => driftPixels(page), { timeout: 5_000 }).not.toBe(still)
})

test("once a question is sent the art goes and the chat's field takes over", async ({ page }) => {
  await signIn(page, MEMBER)
  await expect.poll(() => fieldsShown(page)).toEqual(["drift"])
  await ask(page, "How many days of paid annual leave do employees get?")
  await expect.poll(() => fieldsShown(page)).toEqual(["ambient"])
  expect(await page.locator('[data-mode="drift"]').count()).toBe(0)
  // The chat's field, drawn and moving.
  const field = () =>
    page.evaluate(`(() => {
      const c = document.querySelector('[data-slot="chat-backdrop"] [data-mode="ambient"] canvas')
      return c && c.width ? c.toDataURL() : null
    })()`) as Promise<string | null>
  await expect.poll(field).not.toBeNull()
  const before = await field()
  await expect.poll(field, { timeout: 5_000 }).not.toBe(before)
})

test("opening a saved chat from an empty one draws no art and animates nothing", async ({ page }) => {
  await signIn(page, MEMBER)
  await ask(page, "How many days of sick leave do employees get?")
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/)
  const saved = new URL(page.url()).pathname

  await page.goto("/chat")
  await expect.poll(() => fieldsShown(page)).toEqual(["drift"])
  // In the app, as the history list does it.
  await page.evaluate(`window.history.pushState({}, "", ${JSON.stringify(saved)}); dispatchEvent(new PopStateEvent("popstate"))`)
  await expect(conversation(page).getByRole("button", { name: "Copy" })).toHaveCount(1)
  expect(await fieldsShown(page)).toEqual(["ambient"])
  // Nothing in the chat itself. (The history list colours the row now open; that's
  // its own hover and active colour, not the chat's.)
  const running = await page.evaluate(`document.getAnimations()
    .filter((a) => a.playState === "running" || a.playState === "pending")
    .filter((a) => !a.timeline || a.timeline instanceof DocumentTimeline)
    .filter((a) => !(a.effect && a.effect.target && a.effect.target.closest('[data-slot="sidebar"]')))
    .map((a) => (a.animationName || a.transitionProperty || "animation") + " on " +
      (a.effect && a.effect.target ? a.effect.target.tagName + "." + String(a.effect.target.className).slice(0, 60) : "?"))`)
  expect(running).toEqual([])
})
