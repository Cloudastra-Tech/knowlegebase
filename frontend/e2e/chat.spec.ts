import { expect, test } from "@playwright/test"

import { ask, conversation, MEMBER, signIn, waitForAnswer } from "./helpers.ts"

test("a member gets a cited answer, reads its passage, follows up, and finds it all after a reload", async ({
  page,
}) => {
  await signIn(page, MEMBER)
  const log = conversation(page)

  await ask(page, "How many days of paid annual leave do employees get?")
  await expect(log).toContainText("18 days of paid annual leave")

  // The answer's [S#] marker is a chip naming its source; clicking it opens the passage.
  const chip = log.getByRole("button", { name: /^Source \d+: leave_policy\.txt, Annual leave/ })
  await expect(chip).toBeVisible()
  await chip.click()
  const sheet = page.getByRole("dialog")
  await expect(sheet).toBeVisible()
  await expect(sheet.getByLabel("Passage", { exact: true })).toContainText("18 days of paid annual leave")
  await page.keyboard.press("Escape")
  await expect(sheet).toBeHidden()

  // A suggested follow-up asks the next question in the same conversation.
  const followup = page
    .getByRole("group", { name: "Follow-up questions" })
    .getByRole("button", { name: /Sick leave/ })
  const question = (await followup.textContent())!.trim()
  await followup.click()
  await waitForAnswer(page, 1)
  await expect(log).toContainText(question)
  await expect(log).toContainText("12 days of paid sick leave")

  // Saved: the conversation has its own address, and a reload shows both turns.
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/)
  await page.reload()
  await expect(log).toContainText("How many days of paid annual leave do employees get?")
  await expect(log).toContainText(question)
  await expect(log.getByRole("button", { name: "Copy" })).toHaveCount(2)
  await expect(
    page.getByRole("button", {
      name: "How many days of paid annual leave do employees get?",
      exact: true,
    }),
  ).toBeVisible() // in the history sidebar
})
