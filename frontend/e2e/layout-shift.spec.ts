import { expect, test, type Page } from "@playwright/test"

import { ADMIN, centreStep, DESKTOP, PHONE, signIn } from "./helpers.ts"

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
    // Each section's steps in turn (landing-steps.tsx): the scan, the passages locking
    // in and the answer, the next team's; the lines decoding and the answer
    // re-forming for the other person; the route re-routing for each choice.
    for (const [section, step, wait] of [
      ["answer", "hr", 5000],
      ["answer", "eng", 5000],
      ["answer", "sales", 2500],
      ["access", "Pat", 3000],
      ["access", "Hira", 3000],
      ["privacy", "aws", 1500],
      ["privacy", "key", 1500],
      ["privacy", "local", 1500],
    ] as const) {
      await centreStep(page, section, step)
      await page.waitForTimeout(wait)
    }
    for (const [target, wait] of [
      ["#privacy ol[aria-label]", 2500], // the log masking
      ["#sources [data-source]", 1500],
      ["#start h2", 1500], // the heading resolving
    ] as const) {
      await page.locator(target).first().scrollIntoViewIfNeeded()
      await page.waitForTimeout(wait)
    }
    // Hovered: a connector's sync line.
    await page.locator("#sources [data-source]").first().hover()
    await page.waitForTimeout(500)
    expect(await page.evaluate("window.__shift"), `layout shift at ${size.width}px`).toBeLessThan(0.01)
  }
})
