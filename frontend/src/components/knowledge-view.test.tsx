import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { KnowledgeView } from "@/components/knowledge-view"
import { session } from "@/lib/api"
import { json, mockApi, withProviders } from "@/test/render"

const ITEMS = [
  {
    title: "Leave policy.pdf",
    source: "Uploaded",
    access: "public",
    url: null,
    modified: 1_700_000_000,
    size: 100,
    chunks: 3,
  },
  {
    title: "Salaries.xlsx",
    source: "Google Drive",
    access: "restricted",
    url: "https://drive.example/salaries",
    modified: null,
    size: 50,
    chunks: 0,
  },
]

describe("KnowledgeView", () => {
  it("shows what the server lists, asked as the viewed role", async () => {
    session.setRole("public")
    const fetch = mockApi({
      "GET /api/knowledge": { workspace: "acme", items: ITEMS, outline: true },
    })
    render(withProviders(<KnowledgeView />))

    expect(await screen.findByText("Leave policy.pdf")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Salaries\.xlsx/ })).toHaveAttribute(
      "href",
      "https://drive.example/salaries",
    )
    expect(screen.getByText("acme")).toBeInTheDocument()
    expect(screen.getByText("3 chunks")).toBeInTheDocument()
    expect(screen.getByText("Not indexed")).toBeInTheDocument()
    expect(screen.getByText(/Outline wiki is searched live/)).toBeInTheDocument()
    const headers = new Headers(fetch.mock.calls[0][1]?.headers)
    expect(headers.get("X-Role")).toBe("public")

    // Filtering happens here, without asking the server again.
    await userEvent.setup().type(screen.getByPlaceholderText("Search by name…"), "leave")
    expect(screen.queryByText("Salaries.xlsx")).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it("says so when the server refuses", async () => {
    mockApi({ "GET /api/knowledge": json({ detail: "Not allowed" }, 403) })
    render(withProviders(<KnowledgeView />))

    // No retry for a 4xx, and the list stays a skeleton (the toast says why).
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1))
    expect(screen.queryByText("No documents yet.", { exact: false })).not.toBeInTheDocument()
  })
})
