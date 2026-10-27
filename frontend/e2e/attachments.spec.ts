import { expect, test } from "@playwright/test"

import { ask, conversation, MEMBER, signIn } from "./helpers.ts"

// A file attached to a chat (chat.md C23) is read into text for that chat only and
// cited as "Attached file"; it never reaches anyone else's chats.
const CONTRACT =
  "The Acme support contract renews on the first of June each year with a ten percent discount."

test("an attached file answers its chat, cited, and is gone from a new chat", async ({ page }) => {
  await signIn(page, MEMBER)
  await page.locator('input[type="file"]').setInputFiles({
    name: "acme-contract.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(CONTRACT),
  })
  // The chat is saved first, and the file shows as a chip above the question.
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/)
  await expect(page.getByRole("button", { name: "Remove acme-contract.txt" })).toBeVisible()

  await ask(page, "When does the attached support contract renew?")
  const log = conversation(page)
  await expect(log).toContainText("renews on the first of June")
  const chip = log.getByRole("button", { name: /^Source \d+: acme-contract\.txt/ })
  await expect(chip).toBeVisible()
  await expect(log.getByText("Attached file").filter({ visible: true }).first()).toBeVisible()
  await chip.click()
  const sheet = page.getByRole("dialog")
  await expect(sheet.getByLabel("Passage")).toContainText("ten percent discount")
  await page.keyboard.press("Escape")

  // A new chat has no file attached, and its answers never read it.
  await page.getByRole("button", { name: "New chat" }).first().click()
  await expect(page.getByRole("button", { name: "Remove acme-contract.txt" })).toHaveCount(0)
  await ask(page, "When does the Acme support contract renew?")
  await expect(conversation(page)).not.toContainText("first of June")
})
