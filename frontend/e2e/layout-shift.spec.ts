import { expect, test, type Page } from "@playwright/test"

import { ADMIN, DESKTOP, PHONE, signIn } from "./helpers.ts"

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

// The landing's scenes type, decode, stream, re-route and mask while they're on
// screen; none of it may move what's around them, at either width, while each plays.
test("the landing page's demos don't shift the page", async ({ page }) => {
  test.setTimeout(120_000)
  for (const size of [DESKTOP, PHONE]) {
    await page.setViewportSize(size)
    await page.goto("/")
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
    await page.evaluate(`(() => {
      window.__shift = 0
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) if (!e.hadRecentInput) window.__shift += e.value
      }).observe({ type: "layout-shift" })
    })()`)
    // The hero's card: typing, the passages arriving and decoding, the answer citing.
    await page.waitForTimeout(6000)
    for (const [target, wait] of [
      ["#answer [role=tablist]", 6000], // the scan, the passages locking in, the answer
      ["#access [role=tablist]", 4000], // the lines decoding, the answer re-forming
      ["[data-slot=model-route]", 7500], // past its first re-route (every 6.5s)
      ["#privacy ol", 2500],
      ["#sources [data-source]", 1500],
      ["#start h2", 1500], // the heading resolving
    ] as const) {
      await page.locator(target).first().scrollIntoViewIfNeeded()
      await page.waitForTimeout(wait)
    }
    // Picked by hand: the other person, another route, a connector's sync line.
    await page.locator("#access").getByRole("tab", { name: /Hira/ }).click()
    await page.waitForTimeout(2500)
    await page.locator("#privacy").getByRole("tab", { name: /This server/ }).click()
    await page.waitForTimeout(1000)
    await page.locator("#sources [data-source]").first().hover()
    await page.waitForTimeout(500)
    expect(await page.evaluate("window.__shift"), `layout shift at ${size.width}px`).toBeLessThan(0.01)
  }
})
