import { render, screen } from "@testing-library/react"
import { Users } from "lucide-react"
import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"

import { ScreenHeader } from "@/components/screen-header"

function renderAt(url: string, ui: React.ReactNode) {
  return render(<MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter>)
}

describe("ScreenHeader", () => {
  it("titles the screen with an h2 under a path from the address", () => {
    renderAt(
      "/admin/Sign-In/",
      <ScreenHeader title="Access" icon={Users} description="Who can sign in." actions={<button type="button">Add</button>} />,
    )
    expect(screen.getByRole("heading", { level: 2, name: "Access" })).toBeInTheDocument()
    const eyebrow = screen.getByText("~/admin/sign-in")
    expect(eyebrow).toHaveAttribute("aria-hidden", "true")
    expect(screen.getByText("Who can sign in.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument()
  })

  it("takes its own eyebrow", () => {
    renderAt("/spaces/42", <ScreenHeader title="Spaces" eyebrow="~/spaces/finance" />)
    expect(screen.getByText("~/spaces/finance")).toBeInTheDocument()
  })
})
