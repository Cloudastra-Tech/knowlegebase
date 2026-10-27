import { expect, test, type Page } from "@playwright/test"

import { conversation, DESKTOP, PHONE, shot, signIn, TRIAL_OWNER, waitForAnswer } from "./helpers.ts"

// A workspace just made at sign-up (Trial Co, tests/e2e/server.py): nothing uploaded,
// nobody has asked anything. The checklist comes from the server, so it's the same in
// every browser; the first run offers questions and the sample HR handbook.
const SAMPLE_QUESTION = /^What does “Leave and holidays” say\?$/

async function checklist(page: Page) {
  await page.goto("/admin/sign-in")
  const list = page.getByRole("region", { name: "Set up your workspace" })
  await expect(list).toBeVisible()
  return list
}

test("at 390px: the first run and the checklist fit a phone", async ({ page }) => {
  await page.setViewportSize(PHONE)
  await signIn(page, TRIAL_OWNER)
  await expect(page.getByRole("heading", { name: "Ask your first question" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Try it with a sample HR handbook" })).toBeVisible()
  await shot(page, "first-run-390")
  const list = await checklist(page)
  await expect(list.locator("[data-step=domain] [aria-label=Done]")).toBeVisible()
  await expect(list.locator("[data-step=source] [aria-label='To do']")).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await shot(page, "onboarding-390")
})

test("at 1280px: try the sample handbook, ask about it, then remove it in one click", async ({
  page,
}) => {
  test.setTimeout(90_000)
  await page.setViewportSize(DESKTOP)
  await signIn(page, TRIAL_OWNER)
  await expect(page.getByRole("heading", { name: "Ask your first question" })).toBeVisible()
  await shot(page, "first-run-1280")

  await page.getByRole("button", { name: "Try it with a sample HR handbook" }).click()
  await expect(page.getByText("Sample HR handbook added")).toBeVisible()
  // Indexed in the background: the suggestions come from its titles once it's done.
  await expect(async () => {
    await page.reload()
    await expect(page.getByRole("button", { name: SAMPLE_QUESTION })).toBeVisible({
      timeout: 2_000,
    })
  }).toPass({ timeout: 45_000 })
  await expect(page.getByRole("button", { name: "Remove sample documents" })).toBeVisible()
  await shot(page, "first-run-sample-1280")

  let list = await checklist(page)
  await expect(list).toContainText("The sample HR handbook is in this workspace")
  await shot(page, "onboarding-1280")

  await page.goto("/chat")
  await page.getByRole("button", { name: SAMPLE_QUESTION }).click()
  await waitForAnswer(page, 0)
  await expect(
    conversation(page).getByRole("button", { name: /^Open source \d+: Leave_and_holidays\.txt/ }),
  ).not.toHaveCount(0)
  // Asked once: the next chat is an ordinary one.
  await page.goto("/chat")
  await expect(page.getByRole("heading", { name: "Ask your company docs" })).toBeVisible()

  list = await checklist(page)
  // A sample answer doesn't count as the workspace's first answer.
  await expect(list.locator("[data-step=answer] [aria-label='To do']")).toBeVisible()
  await list.getByRole("button", { name: "Remove sample documents" }).click()
  await expect(page.getByText("Sample documents removed")).toBeVisible()
  await page.goto("/knowledge")
  await expect(page.getByText("Leave_and_holidays.txt")).toHaveCount(0)
})
