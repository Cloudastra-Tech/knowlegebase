import { expect, type Page } from "@playwright/test"

// The accounts tests/e2e/server.py makes, in the default workspace.
export const ADMIN = { email: "admin@e2e.example", password: "admin-password-1" }
export const MEMBER = { email: "member@e2e.example", password: "member-password-1" }
// Workspaces made as sign-up makes them, with BILLING=on (tests/e2e/server.py, TRIALS):
// one with 5 days of its trial left, and one whose trial has ended (read-only).
export const TRIAL_OWNER = { email: "owner@trial.e2e.example", password: "owner-password-1" }
export const LAPSED_OWNER = { email: "owner@lapsed.e2e.example", password: "owner-password-1" }

export const DESKTOP = { width: 1280, height: 800 }
export const PHONE = { width: 390, height: 844 }

// E2E_SHOTS=<folder>: also save a full-page screenshot there, to look at by eye.
export async function shot(page: Page, name: string) {
  const dir = process.env.E2E_SHOTS
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
}

// Nothing on the page is wider than the screen (no sideways scrolling). A string, so
// the e2e tsconfig needs no DOM types.
export async function fitsWidth(page: Page) {
  return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
}

// The boxes that scroll inside the page and are wider inside than they are: a
// vertical scroller (a list, a sheet, the chat) that a child pushes sideways scrolls
// both ways, which fitsWidth can't see (the page itself still fits). A box meant to
// scroll sideways says so with an overflow-x-auto (or overflow-auto) class, as tables
// and code blocks do, and isn't counted. Returns a short description of each.
export async function overflowingScrollers(page: Page): Promise<string[]> {
  return page.evaluate(`(() => {
    const found = []
    for (const el of document.querySelectorAll("body *")) {
      const style = getComputedStyle(el)
      if (!/auto|scroll/.test(style.overflowX)) continue
      if (!el.clientWidth || el.scrollWidth <= el.clientWidth + 1) continue
      const cls = typeof el.className === "string" ? el.className : ""
      if (/(^|\\s)overflow-(x-)?(auto|scroll)(\\s|$)/.test(cls)) continue
      const name = el.tagName.toLowerCase() + (el.dataset.slot ? "[data-slot=" + el.dataset.slot + "]" : "")
      found.push(name + " " + el.scrollWidth + ">" + el.clientWidth + " " + cls.slice(0, 80))
    }
    return found
  })()`)
}

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
