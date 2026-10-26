import { useRef, useState } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { Composer, QUESTION_LIMIT } from "@/components/chat-composer"

function Harness({
  initial = "",
  busy = false,
  onSend = () => {},
  onStop = () => {},
}: {
  initial?: string
  busy?: boolean
  onSend?: (text: string) => void
  onStop?: () => void
}) {
  const [value, setValue] = useState(initial)
  const ref = useRef<HTMLTextAreaElement>(null)
  return (
    <Composer
      value={value}
      onChange={setValue}
      onSend={() => onSend(value)}
      onStop={onStop}
      busy={busy}
      inputRef={ref}
    />
  )
}

describe("the composer", () => {
  it("is the shadcn Textarea, at 16px on phones so iOS doesn't zoom", () => {
    render(<Harness />)
    const box = screen.getByRole("textbox", { name: "Your question" })
    expect(box).toHaveAttribute("data-slot", "textarea")
    expect(box.className).toMatch(/\btext-base\b/)
    expect(box.className).toMatch(/\bmd:text-sm\b/)
    expect(screen.getByRole("button", { name: "Send" })).toHaveAttribute("data-slot", "button")
  })

  it("sends on Enter and starts a new line on Shift+Enter", async () => {
    const onSend = vi.fn()
    render(<Harness onSend={onSend} />)
    const user = userEvent.setup()
    const box = screen.getByRole("textbox", { name: "Your question" })

    await user.type(box, "first line{Shift>}{Enter}{/Shift}second line")
    expect(onSend).not.toHaveBeenCalled()
    expect(box).toHaveValue("first line\nsecond line")

    await user.type(box, "{Enter}")
    expect(onSend).toHaveBeenCalledWith("first line\nsecond line")
  })

  it("doesn't send an empty question", async () => {
    const onSend = vi.fn()
    render(<Harness onSend={onSend} />)
    await userEvent.setup().type(screen.getByRole("textbox"), "   {Enter}")
    expect(onSend).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled()
  })

  it("counts characters near the limit and won't send past it", async () => {
    const onSend = vi.fn()
    render(<Harness initial={"a".repeat(399)} onSend={onSend} />)
    expect(screen.queryByText(/\/ 500/)).not.toBeInTheDocument()

    const user = userEvent.setup()
    const box = screen.getByRole("textbox")
    await user.type(box, "a")
    expect(screen.getByText(`400 / ${QUESTION_LIMIT}`)).toBeInTheDocument()

    await user.click(box)
    await user.paste("b".repeat(101))
    expect(screen.getByText("1 over the 500-character limit")).toBeInTheDocument()
    expect(box).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled()
    await user.type(box, "{Enter}")
    expect(onSend).not.toHaveBeenCalled()
  })

  it("turns Send into Stop while an answer is written; Esc stops too", async () => {
    const onStop = vi.fn()
    render(<Harness busy onStop={onStop} />)
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Stop answering" }))
    expect(onStop).toHaveBeenCalledTimes(1)

    await user.type(screen.getByRole("textbox"), "{Escape}")
    expect(onStop).toHaveBeenCalledTimes(2)
  })
})
