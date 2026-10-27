import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { DraftEditor } from "@/components/draft-editor"
import { json, mockApi, withProviders } from "@/test/render"

describe("DraftEditor", () => {
  it("says a draft that isn't there is gone, instead of loading forever", async () => {
    mockApi({ "GET /api/drafts/nope": json({ detail: "No such draft." }, 404) })
    const close = vi.fn()
    render(withProviders(<DraftEditor id="nope" onPublished={() => {}} onDeleted={close} />))

    expect(await screen.findByText("This draft isn't here.")).toBeInTheDocument()
    expect(document.querySelector('[data-slot="skeleton"]')).toBeNull()
    await userEvent.setup().click(screen.getByText("Back to the drafts"))
    expect(close).toHaveBeenCalled()
  })
})
