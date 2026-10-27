import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"

import { AppNav, type NavItem } from "@/components/app-nav"

const ADMIN: NavItem[] = [
  ["/chat", "Chat"],
  ["/knowledge", "Knowledge"],
  ["/documents", "Documents"],
  ["/integrations", "Integrations"],
  ["/admin/insights", "Insights"],
  ["/admin/verified", "Verified"],
  ["/admin/assistant", "Assistant"],
  ["/admin/sign-in", "Access"],
].map(([to, label]) => ({ to, section: to, label }))

function renderNav(items: NavItem[], current?: string) {
  render(
    <MemoryRouter>
      <AppNav items={items} current={current} />
    </MemoryRouter>,
  )
  return screen.getByRole("navigation", { name: "Screens" })
}

// jsdom applies no media queries: every breakpoint's links are in the document, and
// which ones show at which width is the CSS's job (checked in the browser).
describe("AppNav", () => {
  it("offers every screen in the phone menu, labelled with the open one", async () => {
    const nav = renderNav(ADMIN, "/admin/insights")
    const menu = within(nav).getByRole("button", { name: "Insights, switch screen" })
    await userEvent.setup().click(menu)
    // Named by the screen alone: the mono route hint beside it is aria-hidden.
    const items = screen.getAllByRole("menuitem")
    expect(items).toHaveLength(ADMIN.length)
    ADMIN.forEach((item, i) => expect(items[i]).toHaveAccessibleName(item.label))
    expect(items[0]).toHaveTextContent("~/chat")
    expect(screen.getByRole("menuitem", { name: "Insights" })).toHaveAttribute(
      "aria-current",
      "page",
    )
  })

  it("puts all but Chat and Knowledge under More on narrower screens", async () => {
    const nav = renderNav(ADMIN, "/admin/sign-in")
    // The tabs: each screen once, the open one marked.
    for (const item of ADMIN) {
      expect(within(nav).getByRole("link", { name: item.label })).toHaveAttribute("href", item.to)
    }
    expect(within(nav).getByRole("link", { name: "Access" })).toHaveAttribute("aria-current", "page")
    await userEvent.setup().click(within(nav).getByRole("button", { name: "More" }))
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual(
      ADMIN.slice(2).map((i) => i.label),
    )
  })

  it("draws one indicator line for the open screen, hidden from assistive technology", () => {
    const nav = renderNav(ADMIN, "/knowledge")
    const lines = nav.querySelectorAll('[data-slot="nav-indicator"]')
    expect(lines).toHaveLength(1)
    expect(lines[0]).toHaveAttribute("aria-hidden", "true")
    // What it sits under: the open tab, or More while the open screen is in that menu.
    expect(nav.querySelectorAll("[data-nav-current]")).toHaveLength(1)
    expect(within(nav).getByRole("link", { name: "Knowledge" })).toHaveAttribute("data-nav-current")
  })

  it("keeps a member's two screens as plain tabs", () => {
    const nav = renderNav(ADMIN.slice(0, 2), "/chat")
    expect(within(nav).getAllByRole("link")).toHaveLength(2)
    expect(within(nav).queryByRole("button")).not.toBeInTheDocument()
  })
})
