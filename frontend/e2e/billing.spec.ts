import { expect, test } from "@playwright/test"

import {
  ADMIN,
  conversation,
  DESKTOP,
  LAPSED_OWNER,
  PHONE,
  shot,
  signIn,
  TRIAL_OWNER,
} from "./helpers.ts"

// BILLING=on (tests/e2e/server.py). Stripe is never called: Checkout is answered here,
// in the browser, with a URL back to the billing screen as Stripe would send it.
for (const [size, viewport] of [
  ["1280", DESKTOP],
  ["390", PHONE],
] as const) {
  test.describe(`at ${size}px`, () => {
    test.use({ viewport })

    test("the trial banner counts down, and Plan & billing sends the owner to Checkout", async ({
      page,
    }) => {
      const checkouts: unknown[] = []
      await page.route("**/api/billing/checkout", async (route) => {
        checkouts.push(route.request().postDataJSON())
        await route.fulfill({ json: { url: "/billing?checkout=done" } })
      })
      await signIn(page, TRIAL_OWNER)
      const banner = page.getByTestId("trial-banner")
      await expect(banner).toContainText("5 days left in your trial.")
      await shot(page, `trial-banner-${size}`)

      await banner.getByRole("link", { name: "Pick a plan" }).click()
      await expect(page).toHaveURL(/\/billing$/)
      await expect(page.getByRole("heading", { name: "Plan & billing" })).toBeVisible()
      await expect(page.getByText("5 days left in your trial", { exact: false }).last()).toBeVisible()
      await expect(page.getByText("Questions this month")).toBeVisible()
      await expect(page.getByText("owner@trial.e2e.example")).toBeVisible()
      // Nothing on the page is wider than the screen.
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true)
      await shot(page, `billing-${size}`)

      await page
        .getByRole("listitem", { name: "Business plan" })
        .getByRole("button", { name: "Choose Business" })
        .click()
      await expect(page.getByText("Your plan changes as soon as Stripe confirms")).toBeVisible()
      expect(checkouts).toEqual([{ plan: "business" }])
    })
  })
}

test("an ended trial is read-only: questions are refused with the reason", async ({ page }) => {
  await page.setViewportSize(PHONE)
  await signIn(page, LAPSED_OWNER)
  await expect(page.getByTestId("trial-banner")).toContainText("read-only")
  const box = page.getByRole("textbox", { name: "Your question" })
  await box.fill("What is our leave policy?")
  await box.press("Enter")
  const log = conversation(page)
  await expect(log.getByText("Your plan's limit")).toBeVisible()
  await expect(log).toContainText("trial has ended")
  await shot(page, "read-only-390")
  await page.goto("/billing")
  await expect(page.getByText("The workspace is read-only")).toBeVisible()
  await expect(page.getByText("Trial ended")).toBeVisible()
})

test("a workspace without a plan has no banner, as with billing off", async ({ page }) => {
  await signIn(page, ADMIN)
  await expect(page.getByRole("log", { name: "Conversation" })).toBeVisible()
  await expect(page.getByTestId("trial-banner")).toHaveCount(0)
  await page.goto("/billing")
  await expect(page.getByText("No plan to manage")).toBeVisible()
})
