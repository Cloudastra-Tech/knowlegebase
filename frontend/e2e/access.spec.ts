import { expect, test } from "@playwright/test"

import { ADMIN, ask, conversation, MEMBER, signIn } from "./helpers.ts"

// docs/restricted/hiring_plan_2027.txt is HR-only (tests/e2e/server.py).
const QUESTION = "What is the hiring plan for the Pune campus and the Hyderabad centre?"

test("an HR-only document answers HR, and is invisible to a public member", async ({
  browser,
}) => {
  const admin = await browser.newPage()
  await signIn(admin, ADMIN)
  await ask(admin, QUESTION)
  await expect(
    conversation(admin).getByRole("button", { name: /^Open source \d+: hiring_plan_2027\.txt/ }),
  ).not.toHaveCount(0)
  await admin.close()

  const member = await browser.newPage()
  await signIn(member, MEMBER)
  await ask(member, QUESTION)
  const log = conversation(member)
  await expect(log.getByRole("button", { name: /hiring_plan_2027/ })).toHaveCount(0)
  await expect(log).not.toContainText("severance")
  await expect(log).not.toContainText("open a new campus in Pune")
  await member.goto("/knowledge")
  await expect(member.getByText("leave_policy.txt").first()).toBeVisible()
  await expect(member.getByText("hiring_plan_2027.txt")).toHaveCount(0)
  await member.close()
})

test("an admin opens Access, then Activity, and sees the audit log", async ({ page }) => {
  await signIn(page, ADMIN)
  // Playwright's window is 720px tall, short enough that the Admin group starts folded.
  const nav = page.getByRole("navigation", { name: "Screens" })
  await nav.getByRole("button", { name: "Admin" }).click()
  await nav.getByRole("link", { name: "Access" }).click()
  await page.getByRole("tab", { name: "Activity" }).click()
  await expect(page).toHaveURL(/\/admin\/activity/)
  const row = page.getByRole("row").filter({ hasText: "member@e2e.example" })
  await expect(row.first()).toBeVisible()
})
