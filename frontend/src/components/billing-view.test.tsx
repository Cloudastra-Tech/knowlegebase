import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { BillingView } from "@/components/billing-view"
import { TrialBanner } from "@/components/trial-banner"
import type { Billing, Plan, User } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const features = (included: string[]) =>
  (["sso_oidc_saml", "scim", "audit_export", "api", "slack", "mcp", "teams"] as const).map((id) => ({
    id,
    label: id,
    included: included.includes(id),
  }))

const plan = (id: string, name: string, cents: number, extra: Partial<Plan> = {}): Plan => ({
  id,
  name,
  seat_price: cents,
  currency: "usd",
  buyable: true,
  limits: { questions_month: 2000, docs: 500, storage_mb: 2048, connectors: 2 },
  features: features(["slack"]),
  available: true,
  current: false,
  ...extra,
})

const TRIAL: Billing = {
  on: true,
  managed: true,
  plan: plan("trial", "Trial", 0, { buyable: false, current: true }),
  status: "trialing",
  trial_ends_at: 1_900_000_000,
  trial_days_left: 5,
  expired: false,
  read_only: false,
  current_period_end: null,
  seats: { used: 3, paid: 0, days: 30 },
  usage: [
    { id: "questions_month", label: "Questions this month", used: 120, limit: 1000 },
    { id: "docs", label: "Documents", used: 200, limit: 200 },
    { id: "storage_mb", label: "Storage (MB)", used: 12.5, limit: 1024 },
    { id: "connectors", label: "Connectors", used: 1, limit: 2 },
  ],
  resets_on: "2026-11-01",
  plans: [plan("starter", "Starter", 800), plan("business", "Business", 2000)],
  owners: [{ username: "founder@acme.example", granted_by: null, granted_at: 1 }],
  is_owner: true,
  stripe: { configured: true, live_key_refused: false },
  portal: false,
}

function show() {
  render(
    withProviders(
      <MemoryRouter>
        <BillingView />
      </MemoryRouter>,
    ),
  )
}

afterEach(() => vi.unstubAllGlobals())

describe("BillingView", () => {
  it("shows the trial, usage against the limits, and sends an owner to Stripe Checkout", async () => {
    const assign = vi.fn()
    vi.stubGlobal("location", { ...window.location, assign })
    const sent: unknown[] = []
    mockApi({
      "GET /api/billing": TRIAL,
      "POST /api/billing/checkout": (body: unknown) => {
        sent.push(body)
        return { url: "https://checkout.stripe.com/c/pay/cs_test_1" }
      },
    })
    show()
    expect(await screen.findByText("5 days left in your trial", { exact: false })).toBeInTheDocument()
    expect(screen.getByText("200 of 200")).toHaveClass("text-destructive") // documents: full
    expect(screen.getByText("3")).toBeInTheDocument() // seats in use
    const business = screen.getByRole("listitem", { name: "Business plan" })
    await userEvent.click(within(business).getByRole("button", { name: "Choose Business" }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.stripe.com/c/pay/cs_test_1"))
    expect(sent).toEqual([{ plan: "business" }])
  })

  it("lets an admin who isn't a billing owner look but not buy", async () => {
    mockApi({ "GET /api/billing": { ...TRIAL, is_owner: false } })
    show()
    expect(await screen.findByText(/Only a billing owner \(founder@acme\.example\)/)).toBeInTheDocument()
    for (const b of screen.getAllByRole("button", { name: /^Choose / })) expect(b).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Add owner" })).not.toBeInTheDocument()
  })

  it("says the workspace is read-only once the trial has ended", async () => {
    mockApi({
      "GET /api/billing": { ...TRIAL, expired: true, read_only: true, trial_days_left: 0 },
    })
    show()
    expect(await screen.findByText("The workspace is read-only")).toBeInTheDocument()
    expect(screen.getByText("Trial ended")).toBeInTheDocument()
  })
})

describe("TrialBanner", () => {
  const user: User = { username: "a@acme.example", name: "A", workspace: "acme", role: "admin" }
  const summary = {
    plan: "Trial",
    status: "trialing" as const,
    trial_ends_at: 1_900_000_000,
    trial_days_left: 2,
    read_only: false,
    expired: false,
    owner: true,
    can_view: true,
  }
  const banner = (u: User) =>
    render(
      <MemoryRouter>
        <TrialBanner user={u} />
      </MemoryRouter>,
    )

  it("counts down for the people who can pick a plan", () => {
    banner({ ...user, billing: summary })
    expect(screen.getByTestId("trial-banner")).toHaveTextContent("2 days left in your trial.")
    expect(screen.getByRole("link", { name: "Pick a plan" })).toHaveAttribute("href", "/billing")
  })

  it("shows nothing to a member until the workspace is read-only", () => {
    const { unmount } = banner({ ...user, role: "public", billing: { ...summary, owner: false, can_view: false } })
    expect(screen.queryByTestId("trial-banner")).not.toBeInTheDocument()
    unmount()
    banner({
      ...user,
      role: "public",
      billing: { ...summary, owner: false, can_view: false, expired: true, read_only: true },
    })
    expect(screen.getByTestId("trial-banner")).toHaveTextContent(/read-only/)
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("shows nothing with billing off", () => {
    banner({ ...user, billing: null })
    expect(screen.queryByTestId("trial-banner")).not.toBeInTheDocument()
  })
})
