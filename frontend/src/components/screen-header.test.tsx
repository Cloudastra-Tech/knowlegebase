import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Users } from "lucide-react"
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router"
import { describe, expect, it } from "vitest"

import { ScreenHeader } from "@/components/screen-header"

function renderAt(url: string, ui: React.ReactNode) {
  return render(<MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter>)
}

describe("ScreenHeader", () => {
  it("titles the screen with an h2 under breadcrumbs from the address", () => {
    renderAt(
      "/admin/webhooks",
      <ScreenHeader title="Access" icon={Users} description="Who can sign in." actions={<button type="button">Add</button>} />,
    )
    expect(screen.getByRole("heading", { level: 2, name: "Access" })).toBeInTheDocument()
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" })
    expect(within(nav).getByRole("list")).toBeInTheDocument()
    expect(within(nav).getByRole("link", { name: "Access" })).toHaveAttribute("href", "/admin/sign-in")
    const page = within(nav).getByText("Webhooks")
    expect(page).toHaveAttribute("aria-current", "page")
    expect(page.closest("a")).toBeNull()
    expect(screen.getByText("Who can sign in.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument()
  })

  it("names a detail view from the screen's data, with a way back to its parent", async () => {
    const router = createMemoryRouter(
      [
        {
          path: "/spaces/:id",
          element: <ScreenHeader title="Marketing team" crumbs={{ name: "Marketing team", tab: "Sources" }} />,
        },
        { path: "/spaces", element: <h2>Your spaces</h2> },
      ],
      { initialEntries: ["/spaces/s1"] },
    )
    render(<RouterProvider router={router} />)
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" })
    expect(within(nav).getByRole("link", { name: "Marketing team" })).toHaveAttribute("href", "/spaces/s1")
    expect(within(nav).getByText("Sources")).toHaveAttribute("aria-current", "page")
    // The phone's back button: the parent, by name.
    expect(screen.getByRole("link", { name: "Back to Spaces" })).toHaveAttribute("href", "/spaces")
    await userEvent.setup().click(within(nav).getByRole("link", { name: "Spaces" }))
    expect(router.state.location.pathname).toBe("/spaces")
  })

  it("puts a settings section under Settings, with a way back to the list", () => {
    renderAt("/settings/api-keys", <ScreenHeader title="Settings" />)
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" })
    expect(within(nav).getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings")
    expect(within(nav).getByText("API keys")).toHaveAttribute("aria-current", "page")
    expect(screen.getByRole("link", { name: "Back to Settings" })).toHaveAttribute("href", "/settings")
  })

  it("takes a trail of its own", () => {
    renderAt("/__kit", <ScreenHeader title="Kit" trail={{ crumbs: [{ label: "Kit" }], back: null }} />)
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Kit")
  })
})
