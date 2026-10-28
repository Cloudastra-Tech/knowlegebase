import { act, render, screen } from "@testing-library/react"
import { useTheme } from "next-themes"
import { afterEach, describe, expect, it } from "vitest"

import { ThemeRoot } from "@/components/theme-root"
import { useForcedDark } from "@/lib/forced-theme"

function DarkPage() {
  useForcedDark()
  return null
}

function Shown() {
  const { forcedTheme, resolvedTheme } = useTheme()
  return <p>{forcedTheme ?? `own:${resolvedTheme}`}</p>
}

afterEach(() => {
  localStorage.clear()
  document.documentElement.className = ""
})

describe("ThemeRoot", () => {
  it("keeps the landing and sign-in pages dark for someone who chose light, then gives their theme back", () => {
    localStorage.setItem("theme", "light")
    const page = (dark: boolean) => (
      <ThemeRoot>
        {dark && <DarkPage />}
        <Shown />
      </ThemeRoot>
    )
    const { rerender } = render(page(false))
    expect(document.documentElement).toHaveClass("light")
    expect(screen.getByText("own:light")).toBeInTheDocument()

    // The toaster reads forcedTheme, so its toasts are dark here too.
    act(() => rerender(page(true)))
    expect(document.documentElement).toHaveClass("dark")
    expect(document.documentElement).not.toHaveClass("light")
    expect(screen.getByText("dark")).toBeInTheDocument()

    act(() => rerender(page(false)))
    expect(document.documentElement).toHaveClass("light")
    expect(screen.getByText("own:light")).toBeInTheDocument()
  })
})
