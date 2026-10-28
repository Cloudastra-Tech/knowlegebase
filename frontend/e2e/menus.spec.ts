import { expect, test } from "@playwright/test"

import { ask, MEMBER, signIn } from "./helpers.ts"

// A submenu opens beside its glass menu, on top of the page: not clipped inside the
// parent (whose backdrop-filter would contain a fixed-position child).
test("a chat's Move to folder submenu is visible and usable", async ({ page }) => {
  await signIn(page, MEMBER)
  await ask(page, "How many days of paid annual leave do employees get?")

  const row = page.getByRole("button", { name: /^Options for / }).first()
  await row.click({ force: true })
  await page.getByRole("menuitem", { name: "Move to folder" }).hover()
  const newFolder = page.getByRole("menuitem", { name: "New folder" })
  await expect(newFolder).toBeVisible()

  // The item is the topmost element at its own centre, so it can really be seen and clicked.
  const box = (await newFolder.boundingBox())!
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2]
  const onTop = await page.evaluate(
    `document.elementFromPoint(${x}, ${y})?.closest('[role="menuitem"]')?.textContent ?? ""`,
  )
  expect(onTop).toContain("New folder")
})
