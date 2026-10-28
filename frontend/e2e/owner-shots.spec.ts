import { expect, test, type Page } from "@playwright/test"

import { ADMIN, ask, DESKTOP, PHONE, signIn } from "./helpers.ts"

// A screenshot of every screen, in both themes at 1280 and 390, for looking over the
// design by eye: E2E_OWNER_SHOTS=<folder> saves <screen>-<theme>-<width>.png there.
// Off without it (it checks nothing the other specs don't).
const DIR = process.env.E2E_OWNER_SHOTS

const SIGNED_OUT = [
  ["landing", "/"],
  ["login", "/login"],
] as const

const SCREENS = [
  ["chat-empty", "/chat"],
  ["knowledge", "/knowledge"],
  ["documents", "/documents"],
  ["documents-draft-missing", "/documents?draft=missing"],
  ["integrations", "/integrations"],
  ["insights", "/admin/insights"],
  ["insights-not-found", "/admin/insights/not-found"],
  ["insights-feedback", "/admin/insights/feedback"],
  ["verified", "/admin/verified"],
  ["assistant", "/admin/assistant"],
  ["access-sign-in", "/admin/sign-in"],
  ["access-members", "/admin/members"],
  ["access-groups", "/admin/groups"],
  ["access-activity", "/admin/activity"],
  ["access-api", "/admin/api"],
  ["access-webhooks", "/admin/webhooks"],
  ["access-data", "/admin/data"],
  ["spaces", "/spaces"],
  ["settings", "/settings"],
  ["settings-general", "/settings/general"],
  ["settings-chats", "/settings/chats"],
  ["settings-connections", "/settings/connections"],
  ["settings-prompts", "/settings/prompts"],
  ["settings-digests", "/settings/digests"],
  ["settings-api-keys", "/settings/api-keys"],
  ["settings-billing", "/settings/billing"],
  ["settings-security", "/settings/security"],
  ["extension-connect", "/extension/connect"],
] as const

async function settle(page: Page) {
  await expect(
    page
      .getByRole("heading", { level: 2 })
      .or(page.getByRole("textbox", { name: "Your question" }))
      .or(page.getByLabel("Username or email"))
      .or(page.getByRole("heading", { level: 1 }))
      .first(),
  ).toBeVisible()
  await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0, { timeout: 10_000 })
  await page.evaluate("document.fonts.ready")
  await page.waitForTimeout(700) // entrances done, the field drawn
}

for (const theme of ["dark", "light"] as const) {
  for (const [width, size] of [
    [1280, DESKTOP],
    [390, PHONE],
  ] as const) {
    test(`owner shots: ${theme} at ${width}`, async ({ page }) => {
      test.skip(!DIR, "set E2E_OWNER_SHOTS=<folder> to take them")
      test.setTimeout(240_000)
      await page.addInitScript(`localStorage.setItem("theme", "${theme}")`)
      await page.setViewportSize(size)
      // An app screen scrolls in its own box (Screen), not the page, so a full-page
      // shot would stop at the fold: the window grows to the screen's length first.
      const shoot = async (name: string) => {
        const extra = (await page.evaluate(`(() => {
          const box = document.querySelector("[data-screen]")?.parentElement
          return box && box.clientHeight ? box.scrollHeight - box.clientHeight : 0
        })()`)) as number
        if (extra > 0) {
          await page.setViewportSize({ width: size.width, height: Math.min(size.height + extra, 8000) })
          await page.waitForTimeout(400)
        }
        await page.screenshot({ path: `${DIR}/${name}-${theme}-${width}.png`, fullPage: true })
        if (extra > 0) await page.setViewportSize(size)
      }

      for (const [name, url] of SIGNED_OUT) {
        await page.goto(url)
        await settle(page)
        await shoot(name)
      }

      // A question first, so Insights has a day of data.
      await signIn(page, ADMIN)
      await ask(page, "How many days of paid annual leave do employees get?")
      await page.waitForTimeout(700)
      await shoot("chat-answer")
      for (const [name, url] of SCREENS) {
        await page.goto(url)
        await settle(page)
        await shoot(name)
      }
    })
  }
}
