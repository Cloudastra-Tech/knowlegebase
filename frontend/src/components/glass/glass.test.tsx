import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { GlassPanel } from "@/components/glass/glass-panel"
import { Panel } from "@/components/glass/panel"
import { StatusDot } from "@/components/glass/status-dot"

describe("GlassPanel", () => {
  it("is one of the three tiers, and grain only goes on a blurred one", () => {
    render(
      <>
        <GlassPanel data-testid="chrome" noise />
        <GlassPanel data-testid="floating" tier="floating" as="aside" />
        <GlassPanel data-testid="subtle" tier="subtle" noise />
      </>,
    )
    expect(screen.getByTestId("chrome")).toHaveClass("glass", "glass-noise")
    expect(screen.getByTestId("floating").tagName).toBe("ASIDE")
    expect(screen.getByTestId("floating")).toHaveClass("glass-strong")
    expect(screen.getByTestId("subtle")).toHaveClass("glass-subtle")
    expect(screen.getByTestId("subtle")).not.toHaveClass("glass-noise")
  })
})

describe("Panel", () => {
  it("is a region named by its title, with the eyebrow and corners hidden", () => {
    render(
      <Panel title="Providers" eyebrow="sign-in" actions={<button type="button">Add</button>} corners>
        <p>Okta</p>
      </Panel>,
    )
    const region = screen.getByRole("region", { name: "Providers" })
    expect(region).toHaveClass("glass-subtle")
    expect(screen.getByRole("heading", { level: 3, name: "Providers" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument()
    expect(screen.getByText("sign-in")).toHaveAttribute("aria-hidden", "true")
    expect(screen.getByText("┌")).toHaveAttribute("aria-hidden", "true")
  })

  it("is described by its description, under the title", () => {
    render(
      <Panel title="Usage" description="Questions start again on 1 Nov.">
        <p>12 of 100</p>
      </Panel>,
    )
    const region = screen.getByRole("region", { name: "Usage" })
    expect(region).toHaveAccessibleDescription("Questions start again on 1 Nov.")
    expect(screen.getByText("Questions start again on 1 Nov.")).toHaveAttribute("data-slot", "panel-description")
  })

  it("is opaque for reading, and plain without a head", () => {
    const { container } = render(
      <Panel tone="solid">
        <p>A table</p>
      </Panel>,
    )
    const panel = container.querySelector('[data-slot="panel"]')!
    expect(panel).toHaveClass("bg-card")
    expect(panel).not.toHaveAttribute("aria-labelledby")
    expect(panel.querySelector('[data-slot="panel-header"]')).toBeNull()
  })
})

describe("StatusDot", () => {
  it("says its state in words; the dot is decoration", () => {
    const { container } = render(<StatusDot status="live" label="Live" />)
    expect(screen.getByText("Live")).toBeInTheDocument()
    expect(container.querySelector('[data-status="live"] > [aria-hidden]')).not.toBeNull()
  })
})
