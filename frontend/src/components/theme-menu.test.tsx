import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ThemeProvider } from "next-themes"
import { afterEach, describe, expect, it } from "vitest"

import { ThemeMenu } from "@/components/theme-menu"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { setEffects } from "@/lib/ascii-loop"

function renderMenu() {
  render(
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button>Account</Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <ThemeMenu />
        </DropdownMenuContent>
      </DropdownMenu>
    </ThemeProvider>,
  )
}

afterEach(() => {
  setEffects("full")
  document.documentElement.className = ""
})

describe("ThemeMenu", () => {
  it("picks the theme: dark by default, then light", async () => {
    const user = userEvent.setup()
    renderMenu()
    await user.click(screen.getByRole("button", { name: "Account" }))
    expect(screen.getByRole("menuitemradio", { name: "Dark" })).toBeChecked()
    await user.click(screen.getByRole("menuitemradio", { name: "Light" }))
    expect(document.documentElement).toHaveClass("light")
    expect(localStorage.getItem("theme")).toBe("light")
  })

  it('turns "Reduce effects" on and off, kept for next time, and stays open', async () => {
    const user = userEvent.setup()
    renderMenu()
    await user.click(screen.getByRole("button", { name: "Account" }))
    const item = screen.getByRole("menuitemcheckbox", { name: "Reduce effects" })
    expect(item).not.toBeChecked()

    await user.click(item)
    expect(document.documentElement.dataset.effects).toBe("reduced")
    expect(localStorage.getItem("effects")).toBe("reduced")
    expect(screen.getByRole("menuitemcheckbox", { name: "Reduce effects" })).toBeChecked()

    await user.click(screen.getByRole("menuitemcheckbox", { name: "Reduce effects" }))
    expect(document.documentElement.dataset.effects).toBeUndefined()
    expect(localStorage.getItem("effects")).toBeNull()
  })
})
