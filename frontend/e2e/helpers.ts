import { expect, type Page } from "@playwright/test"

// The accounts tests/e2e/server.py makes, in the default workspace.
export const ADMIN = { email: "admin@e2e.example", password: "admin-password-1" }
export const MEMBER = { email: "member@e2e.example", password: "member-password-1" }

export async function signIn(page: Page, account: { email: string; password: string }) {
  await page.goto("/login")
  await page.getByLabel("Username or email").fill(account.email)
  await page.getByLabel("Password").fill(account.password)
  await page.getByRole("button", { name: "Sign in with password" }).click()
  await expect(page).toHaveURL(/\/chat/)
}

export const conversation = (page: Page) => page.getByRole("log", { name: "Conversation" })

// Every finished answer has a Copy button: wait for one more than before.
export async function waitForAnswer(page: Page, before: number) {
  await expect(conversation(page).getByRole("button", { name: "Copy" })).toHaveCount(
    before + 1,
    { timeout: 20_000 },
  )
}

export async function ask(page: Page, question: string) {
  const before = await conversation(page).getByRole("button", { name: "Copy" }).count()
  const box = page.getByRole("textbox", { name: "Your question" })
  await box.fill(question)
  await box.press("Enter")
  await waitForAnswer(page, before)
}
