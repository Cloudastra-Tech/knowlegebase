import { expect, test, type Page } from "@playwright/test"

import { ADMIN, DESKTOP, signIn } from "./helpers.ts"

// The room a screen's content gets: where its scroller starts and how wide its inside
// is. Neither may change when the screen gains a scrollbar or a modal locks the scroll.
async function contentBox(page: Page) {
  await expect(page.locator("[data-screen] h2").first()).toBeVisible()
  return page.evaluate(`(() => {
    const s = document.querySelector("[data-screen]").closest(".overflow-y-auto")
    return { left: Math.round(s.getBoundingClientRect().left), inner: s.clientWidth }
  })()`)
}

// Headless Chromium hides scrollbars (Playwright passes --hide-scrollbars); show them,
// as a desktop browser does, so a shift would be measurable.
test.use({ launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] } })

test("scrollbars and modals don't shift the layout", async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await signIn(page, ADMIN)

  // A screen that doesn't scroll, then the same screen grown until it does: the
  // scroller kept its gutter all along, so its content keeps the same width.
  await page.goto("/spaces")
  const short = await contentBox(page)
  await page.evaluate(
    `document.querySelector("[data-screen]").closest(".overflow-y-auto")
      .insertAdjacentHTML("beforeend", '<div style="height:3000px"></div>')`,
  )
  expect(
    await page.evaluate(
      `(() => { const s = document.querySelector("[data-screen]").closest(".overflow-y-auto"); return s.scrollHeight > s.clientHeight })()`,
    ),
  ).toBe(true)
  expect(await contentBox(page)).toEqual(short)

  // Opening a modal locks the scroll without nudging the page sideways.
  await page.goto("/admin/sign-in")
  const before = await contentBox(page)
  await page.getByRole("button", { name: /Add provider/ }).first().click()
  await expect(page.getByRole("dialog")).toBeVisible()
  expect(await contentBox(page)).toEqual(before)
})
