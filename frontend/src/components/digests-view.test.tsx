import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { DigestsView } from "@/components/digests-view"
import { mockApi, withProviders } from "@/test/render"

const item = (title: string, provider: string, source: string, folder: string | null) => ({
  title, source, provider, folder, access: "public", url: null, modified: 1, size: 1, chunks: 1,
  doc_key: `${folder}/${title}`,
})

const KNOWLEDGE = {
  workspace: "acme",
  outline: false,
  items: [
    item("leave.txt", "uploads", "Uploaded", "public"),
    item("Board minutes", "notion", "Notion", "wiki-root"),
    item("Handbook", "google_drive", "Google Drive", "hr-folder"),
    item("sample.txt", "Sample documents", "Sample", "sample"),
  ],
}

const SUB = {
  id: 4, frequency: "weekly", channel: "email", sources: ["notion"], folders: [],
  enabled: true, last_sent_at: null, next_due_at: 2_000_000_000, last_error: null, created_at: 1,
}

function settings(subscriptions: unknown[]) {
  return {
    subscriptions,
    sources: [
      { id: "uploads", label: "Company documents" },
      { id: "notion", label: "Notion" },
      { id: "google_drive", label: "Google Drive" },
    ],
    channels: { email: true, slack: false },
    frequencies: ["daily", "weekly"],
  }
}

describe("DigestsView: folders", () => {
  it("offers the folders of the sources a digest follows, and saves a pick", async () => {
    const sent: unknown[] = []
    mockApi({
      "GET /api/digests": settings([SUB]),
      "GET /api/knowledge": KNOWLEDGE,
      "PATCH /api/digests/4": (body: unknown) => {
        sent.push(body)
        return SUB
      },
    })
    render(withProviders(<DigestsView />))
    const folders = await screen.findByRole("group", { name: "Folders" })
    // Only Notion's: the digest follows Notion.
    expect(within(folders).getAllByRole("checkbox")).toHaveLength(1)
    await userEvent.setup().click(within(folders).getByLabelText("Notion: folder wiki-root"))
    expect(sent).toEqual([{ folders: ["wiki-root"] }])
  })

  it("subscribes a new digest to the folders picked", async () => {
    const sent: unknown[] = []
    mockApi({
      "GET /api/digests": settings([]),
      "GET /api/knowledge": KNOWLEDGE,
      "POST /api/digests": (body: unknown) => {
        sent.push(body)
        return SUB
      },
    })
    render(withProviders(<DigestsView />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "New digest" }))
    const folders = await screen.findByRole("group", { name: "Folders" })
    // Every source's folders when none is picked; never the sample documents.
    expect(within(folders).getAllByRole("checkbox")).toHaveLength(3)
    expect(within(folders).queryByText(/sample/i)).not.toBeInTheDocument()
    await user.click(within(folders).getByLabelText("Uploads: files for everyone"))
    await user.click(screen.getByRole("button", { name: "Subscribe" }))
    expect(sent).toEqual([
      { frequency: "weekly", channel: "email", sources: [], folders: ["public"] },
    ])
  })
})
