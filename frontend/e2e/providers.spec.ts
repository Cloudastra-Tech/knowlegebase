import { expect, test, type Page } from "@playwright/test"

import { ADMIN, signIn } from "./helpers.ts"

// Assistant → Models: an admin brings an Anthropic key for answers. Saving it makes no
// model call (Test connection only lists models, and this spec doesn't press it), so
// no network. The key is write-only: after saving, the page only says "Key saved"
// with its last 4 characters.
const KEY = "sk-ant-e2e-" + "0123456789abcdef0123456789"

// The specs share one workspace: put answers back on the server's provider (the fake
// OpenAI) whatever happened, so the specs after this one never reach Anthropic.
async function backToDefault(page: Page) {
  await page.evaluate(async () => {
    await fetch("/api/admin/assistant/models", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${localStorage.getItem("kb_token")}`,
        "X-Role": "hr",
      },
      body: JSON.stringify({ chat_provider: "default", api_keys: { anthropic: null } }),
    })
  })
}

test("an admin saves an Anthropic key for answers and never sees it again", async ({ page }) => {
  await signIn(page, ADMIN)
  try {
    await page.goto("/admin/assistant")
    const models = page.getByRole("region", { name: "Models" })
    await models.getByRole("button", { name: "Add a provider" }).click()
    await page.getByRole("menuitem", { name: "Anthropic" }).click()
    await models.getByLabel("Anthropic API key").fill(KEY)
    await models.getByRole("button", { name: "Save" }).click()

    await expect(models.getByText("Key saved")).toBeVisible()
    await expect(models.getByText(`••••${KEY.slice(-4)}`)).toBeVisible()
    await expect(models.getByLabel("Anthropic API key")).toHaveCount(0)
    expect(await page.content()).not.toContain(KEY)
    await page.reload()
    await expect(models.getByText("Key saved")).toBeVisible()
    expect(await page.content()).not.toContain(KEY)

    // Remove takes its key and models: answers go back to the server's.
    await models.getByRole("button", { name: "Remove Anthropic" }).click()
    await page.getByRole("alertdialog").getByRole("button", { name: "Remove" }).click()
    await expect(models.getByText("Key saved")).toHaveCount(0)
    await expect(models.getByText(/Answers use the server's model/)).toBeVisible()
  } finally {
    await backToDefault(page)
  }
})
