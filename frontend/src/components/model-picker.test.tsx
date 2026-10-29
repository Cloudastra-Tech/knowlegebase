import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ModelPicker } from "@/components/model-picker"
import type { ChatModel } from "@/lib/api"

const model = (provider: string, provider_label: string, id: string, label: string): ChatModel => ({
  provider,
  provider_label,
  model: id,
  label,
  tier: "balanced",
  vision: true,
  tool_calls: true,
})

// What /api/models lists for a workspace on Gemini, Azure OpenAI and a compatible server.
const MODELS = [
  model("gemini", "Google Gemini", "gemini-3.8-flash", "Gemini 3.8 Flash"),
  model("azure", "Azure OpenAI", "gpt-4o-prod", "gpt-4o-prod"),
  model("openai_compatible", "OpenAI-compatible", "llama-3.3-70b-versatile", "llama-3.3-70b-versatile"),
]

describe("ModelPicker", () => {
  it("lists each provider's models under its name, each with its mark", async () => {
    const onChange = vi.fn()
    render(<ModelPicker models={MODELS} value={null} onChange={onChange} />)
    const user = userEvent.setup()
    const trigger = screen.getByRole("button", { name: "Model: Gemini 3.8 Flash. Change model" })
    expect(trigger.querySelector('[data-slot="provider-mark"]')).not.toBeNull()
    await user.click(trigger)
    const menu = await screen.findByRole("menu")
    for (const label of ["Google Gemini", "Azure OpenAI", "OpenAI-compatible"])
      expect(within(menu).getByText(label)).toBeInTheDocument()
    const items = within(menu).getAllByRole("menuitem")
    expect(items.map((i) => i.textContent)).toEqual([
      "Gemini 3.8 FlashBalanced",
      "gpt-4o-prodBalanced",
      "llama-3.3-70b-versatileBalanced",
    ])
    // Every provider has a mark: a brand's (masked SVG), or a plain glyph for a
    // compatible server, which has no brand of its own. Never a text character.
    const marks = items.map((i) => i.querySelector('[data-slot="provider-mark"]'))
    expect(marks.map((m) => m?.tagName.toLowerCase())).toEqual(["span", "span", "svg"])
    await user.click(items[2])
    expect(onChange).toHaveBeenCalledWith({ provider: "openai_compatible", model: "llama-3.3-70b-versatile" })
  })
})
