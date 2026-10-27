import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { EmailInCard } from "@/components/email-in-card"
import type { EmailIn } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const OFF: EmailIn = {
  enabled: false,
  configured: true,
  address: "ask@acme.kb.example.com",
  provider: "postmark",
  webhook_url: "https://kb.example.com/api/email-in/postmark",
  can_manage: true,
  updated_by: null,
  updated_at: null,
}

describe("EmailInCard", () => {
  it("shows the address and lets an admin turn it on", async () => {
    const sent: unknown[] = []
    mockApi({
      "GET /api/admin/email-in": OFF,
      "PUT /api/admin/email-in": (body: unknown) => {
        sent.push(body)
        return { ...OFF, enabled: true }
      },
    })
    render(withProviders(<EmailInCard />))
    expect(await screen.findByText("ask@acme.kb.example.com")).toBeInTheDocument()
    expect(screen.getByText("Off")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("switch", { name: "Answer emails" }))
    await waitFor(() => expect(screen.getByText("On")).toBeInTheDocument())
    expect(sent).toEqual([{ enabled: true }])
  })

  it("says what the host must set when the server has no email-in", async () => {
    mockApi({
      "GET /api/admin/email-in": { ...OFF, configured: false, address: null, can_manage: false },
    })
    render(withProviders(<EmailInCard />))
    expect(await screen.findByText("Not set up on this server")).toBeInTheDocument()
    expect(screen.getByText(/EMAIL_IN_DOMAIN/)).toBeInTheDocument()
    expect(screen.getByRole("switch", { name: "Answer emails" })).toBeDisabled()
  })
})
