// Plan & billing (/api/billing: kb/routers/billing.py), only with BILLING=on on the
// server. Admins see it; the workspace's billing owners change it (Stripe Checkout
// and the Customer Portal: full-page redirects to Stripe).

import { json, request, seg } from "@/lib/api/client"

export type BillingStatus = "trialing" | "active" | "past_due" | "canceled"
export type LimitId = "questions_month" | "docs" | "storage_mb" | "connectors"
export type FeatureId =
  | "sso_oidc_saml"
  | "scim"
  | "audit_export"
  | "api"
  | "slack"
  | "mcp"
  | "teams"

export interface Plan {
  id: string
  name: string
  seat_price: number // cents a seat a month
  currency: string
  buyable: boolean
  limits: Record<LimitId, number | null> // null: no limit
  features: { id: FeatureId; label: string; included: boolean }[]
  available: boolean // Stripe has a price for it on this server
  current: boolean
}

export interface BillingOwner {
  username: string
  granted_by: string | null
  granted_at: number
}

export type Billing =
  | { on: false }
  | {
      on: true
      managed: boolean // false: billing doesn't apply to this workspace (no subscription)
      plan: Plan | null
      status: BillingStatus | null
      trial_ends_at: number | null
      trial_days_left: number | null
      expired: boolean
      read_only: boolean
      current_period_end: number | null
      seats: { used: number; paid: number; days: number }
      usage: { id: LimitId; label: string; used: number; limit: number | null }[]
      resets_on: string // YYYY-MM-01: when the monthly question count starts again
      plans: Plan[]
      owners: BillingOwner[]
      is_owner: boolean
      stripe: { configured: boolean; live_key_refused: boolean }
      portal: boolean // there's a Stripe customer to manage
    }

// What GET /api/auth/me says of it (the trial banner); null with BILLING off.
export interface BillingSummary {
  plan: string | null
  status: BillingStatus | null
  trial_ends_at: number | null
  trial_days_left: number | null
  read_only: boolean
  expired: boolean
  owner: boolean
  can_view: boolean // admins and owners open Plan & billing
}

export const billingApi = {
  get: () => request<Billing>("/billing"),
  checkout: (plan: string) => request<{ url: string }>("/billing/checkout", json("POST", { plan })),
  portal: () => request<{ url: string }>("/billing/portal", json("POST")),
  addOwner: (username: string) =>
    request<{ owners: BillingOwner[] }>("/billing/owners", json("POST", { username })),
  removeOwner: (username: string) =>
    request<{ owners: BillingOwner[] }>(`/billing/owners/${seg(username)}`, json("DELETE")),
}
