import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { WebhooksView } from "@/components/webhooks-view"
import type { Webhook, WebhookDelivery } from "@/lib/api"
import { json, mockApi, withProviders } from "@/test/render"

function hook(more: Partial<Webhook> = {}): Webhook {
  return {
    id: "wh1",
    url: "https://siem.example.com/hooks",
    description: null,
    events: ["member.added"],
    secret_hint: "abcd",
    enabled: true,
    created_by: "ada@acme.example",
    created_at: 1,
    updated_at: 1,
    failures: 0,
    disabled_reason: null,
    disabled_at: null,
    previous_secret_until: null,
    ...more,
  }
}

function delivery(more: Partial<WebhookDelivery> = {}): WebhookDelivery {
  return {
    id: "d1",
    webhook_id: "wh1",
    event: "member.added",
    event_id: "ev1",
    status: "failed",
    attempts: 9,
    next_at: null,
    response_code: 500,
    error: "HTTP 500",
    created_at: 1,
    last_attempt_at: 1,
    delivered_at: null,
    ...more,
  }
}

const list = (webhooks: Webhook[]) => ({
  webhooks,
  events: [{ id: "member.added", label: "Someone joined" }],
  retry_hours: 24,
})

describe("WebhooksView: rotating a webhook's secret", () => {
  it("asks first, then shows the new secret once, with a copy button", async () => {
    const asked: unknown[] = []
    const until = Date.now() / 1000 + 86400
    mockApi({
      "GET /api/admin/webhooks": list([hook()]),
      "POST /api/admin/webhooks/wh1/secret": (body: unknown) => {
        asked.push(body)
        return { webhook: hook({ previous_secret_until: until }), secret: "whsec_new_secret" }
      },
    })
    render(withProviders(<WebhooksView />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Rotate secret" }))

    const confirm = await screen.findByRole("alertdialog")
    expect(within(confirm).getByText(/keeps signing alongside it for 24 hours/)).toBeInTheDocument()
    expect(asked).toEqual([])
    await user.click(within(confirm).getByRole("button", { name: "Rotate" }))

    const shown = await screen.findByRole("dialog", { name: "Its new signing secret" })
    expect(asked).toEqual([{}]) // the server's default grace period
    expect(within(shown).getByText("whsec_new_secret")).toBeInTheDocument()
    expect(within(shown).getByRole("button", { name: "Copy the secret" })).toBeInTheDocument()
    expect(within(shown).getByText(/The old secret also signs each request until/)).toBeInTheDocument()
  })

  it("can stop the old secret at once", async () => {
    const asked: unknown[] = []
    mockApi({
      "GET /api/admin/webhooks": list([hook()]),
      "POST /api/admin/webhooks/wh1/secret": (body: unknown) => {
        asked.push(body)
        return { webhook: hook(), secret: "whsec_new_secret" }
      },
    })
    render(withProviders(<WebhooksView />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Rotate secret" }))
    const confirm = await screen.findByRole("alertdialog")
    await user.click(within(confirm).getByLabelText("Stop the old secret now"))
    await user.click(within(confirm).getByRole("button", { name: "Rotate" }))
    const shown = await screen.findByRole("dialog", { name: "Its new signing secret" })
    expect(asked).toEqual([{ grace_hours: 0 }])
    expect(within(shown).getByText(/The old secret has stopped signing/)).toBeInTheDocument()
  })
})

describe("WebhooksView: deliveries", () => {
  it("sends a failed delivery again", async () => {
    const sent: string[] = []
    mockApi({
      "GET /api/admin/webhooks": list([hook()]),
      "GET /api/admin/webhooks/wh1/deliveries": {
        deliveries: [delivery(), delivery({ id: "d2", status: "delivered", response_code: 200, error: null })],
      },
      "POST /api/admin/webhooks/deliveries/d1/redeliver": () => {
        sent.push("d1")
        return delivery({ id: "d3", status: "pending", attempts: 0 })
      },
    })
    render(withProviders(<WebhooksView />))
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Deliveries" }))
    const sheet = await screen.findByRole("dialog", { name: "Deliveries" })
    // Only the failed one can be sent again.
    const again = await within(sheet).findAllByRole("button", { name: "Send again" })
    expect(again).toHaveLength(1)
    await user.click(again[0])
    await waitFor(() => expect(sent).toEqual(["d1"]))
  })

  it("says to turn the webhook on before sending again", async () => {
    mockApi({
      "GET /api/admin/webhooks": list([hook({ enabled: false })]),
      "GET /api/admin/webhooks/wh1/deliveries": { deliveries: [delivery()] },
    })
    render(withProviders(<WebhooksView />))
    await userEvent.setup().click(await screen.findByRole("button", { name: "Deliveries" }))
    const sheet = await screen.findByRole("dialog", { name: "Deliveries" })
    const again = await within(sheet).findByRole("button", { name: "Send again" })
    expect(again).toBeDisabled()
    expect(again).toHaveAccessibleDescription("Turn the webhook on to send it again.")
  })
})

describe("WebhooksView: turned off, or not available", () => {
  it("shows a webhook the server turned off, and why", async () => {
    mockApi({
      "GET /api/admin/webhooks": list([
        hook({ enabled: false, disabled_reason: "failing", disabled_at: Date.now() / 1000 - 120, failures: 50 }),
      ]),
    })
    render(withProviders(<WebhooksView />))
    expect(await screen.findByText("Turned off by the server")).toBeInTheDocument()
    expect(screen.getByText(/its deliveries kept failing/)).toBeInTheDocument()
  })

  it("disables adding, with the reason, when the server has no webhooks", async () => {
    mockApi({ "GET /api/admin/webhooks": json({ detail: "Not Found" }, 404) })
    render(withProviders(<WebhooksView />))
    expect(await screen.findByText("Webhooks are unavailable.")).toBeInTheDocument()
    expect(screen.getByText(/aren't available on this server/)).toBeInTheDocument()
    const add = screen.getByRole("button", { name: "Add webhook" })
    expect(add).toBeDisabled()
    expect(add).toHaveAccessibleDescription("Webhooks can't be added on this server.")
  })

  it("disables adding at the workspace's limit", async () => {
    mockApi({
      "GET /api/admin/webhooks": list(Array.from({ length: 20 }, (_, i) => hook({ id: `wh${i}` }))),
    })
    render(withProviders(<WebhooksView />))
    const add = await screen.findByRole("button", { name: "Add webhook" })
    await waitFor(() => expect(add).toBeDisabled())
    expect(add).toHaveAccessibleDescription(/up to 20 webhooks/)
  })
})
