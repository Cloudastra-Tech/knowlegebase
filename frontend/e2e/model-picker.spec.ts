import { expect, test, type Page } from "@playwright/test"

import { ADMIN, ask, conversation, signIn } from "./helpers.ts"

// The chat's model picker: an admin brings the workspace's own OpenAI key and enables
// a second model; the composer then offers both, the question goes with the one
// picked, and the chat keeps it (after a reload too: the server stores it). The fake
// OpenAI (tests/e2e/fake_openai.py) answers for any model id: no network.
const KEY = "sk-proj-e2e-" + "workspace0123456789abcdef"

async function backToDefault(page: Page) {
  await page.evaluate(async () => {
    await fetch("/api/admin/assistant/models", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${localStorage.getItem("kb_token")}`,
        "X-Role": "hr",
      },
      body: JSON.stringify({ chat_provider: "default", api_keys: { openai: null } }),
    })
  })
}

test("a picked model answers, and the chat keeps it", async ({ page }) => {
  await signIn(page, ADMIN)
  try {
    // One model: no picker in the composer.
    await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible()
    await expect(page.getByRole("button", { name: /^Model:/ })).toHaveCount(0)

    await page.goto("/admin/assistant")
    const models = page.getByRole("region", { name: "Models" })
    await models.getByRole("button", { name: "Add a provider" }).click()
    await page.getByRole("menuitem", { name: "OpenAI" }).click()
    const list = models.getByRole("list", { name: "OpenAI models" })
    await expect(list.getByRole("checkbox", { name: "GPT-4o mini", exact: true })).toBeChecked()
    await list.getByRole("checkbox", { name: "GPT-5", exact: true }).click()
    await models.getByLabel("OpenAI API key").fill(KEY)
    await models.getByRole("button", { name: "Save" }).click()
    await expect(models.getByText("Key saved")).toBeVisible()

    await page.goto("/chat")
    const chip = page.getByRole("button", { name: "Model: GPT-4o mini. Change model" })
    await expect(chip).toBeVisible()
    await chip.click()
    const menu = page.getByRole("menu")
    await expect(menu.getByRole("menuitem", { name: /^GPT-5\s*Best$/ })).toBeVisible()
    await menu.getByRole("menuitem", { name: /^GPT-5\s*Best$/ }).click()
    const picked = page.getByRole("button", { name: "Model: GPT-5. Change model" })
    await expect(picked).toBeVisible()

    await ask(page, "How many days of annual leave do we get?")
    await expect(page).toHaveURL(/\/chat\/[0-9a-f]{32}$/)
    await expect(picked).toBeVisible()
    const id = page.url().split("/").pop()
    const saved = await page.evaluate(async (cid) => {
      const r = await fetch(`/api/conversations/${cid}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("kb_token")}`, "X-Role": "hr" },
      })
      return ((await r.json()) as { model: unknown }).model
    }, id)
    expect(saved).toEqual({ provider: "openai", model: "gpt-5" })

    // Opened again, the chat is on its own model; a follow-up goes with it.
    await page.reload()
    await expect(conversation(page).getByText(/annual leave/).first()).toBeVisible()
    await expect(page.getByRole("button", { name: "Model: GPT-5. Change model" })).toBeVisible()
    await ask(page, "And for part-time staff?")
    await expect(page.getByRole("button", { name: "Model: GPT-5. Change model" })).toBeVisible()
  } finally {
    await backToDefault(page)
  }
})
