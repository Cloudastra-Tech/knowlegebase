// The platform operator console (/api/operator: kb/routers/operator.py). Operators have
// their own session, kept apart from a member's (its own token in this browser), and
// sign in with single sign-on only.

import { ApiError, retryAfterOf } from "@/lib/api/client"
import type { AuditEvent } from "@/lib/api/types"
import type { WorkspaceStatus } from "@/lib/api/workspace"

const TOKEN_KEY = "kb_operator_token"

function stored() {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export const operatorSession = {
  token: stored(),
  set(token: string | null) {
    this.token = token
    try {
      if (token === null) localStorage.removeItem(TOKEN_KEY)
      else localStorage.setItem(TOKEN_KEY, token)
    } catch {
      /* private mode: this tab only */
    }
  },
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (operatorSession.token) headers.set("Authorization", `Bearer ${operatorSession.token}`)
  if (init.body) headers.set("Content-Type", "application/json")
  const res = await fetch(`/api/operator${path}`, { ...init, headers })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const data = await res.json()
      if (typeof data.detail === "string") message = data.detail
    } catch {
      /* not JSON */
    }
    if (res.status === 401) operatorSession.set(null)
    throw new ApiError(res.status, message, retryAfterOf(res))
  }
  return res.json() as Promise<T>
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
})

export interface Operator {
  email: string
  name: string | null
  provider: string | null
}

// A workspace's plan and trial (kb/services/billing.py); null: billing doesn't apply.
export interface OperatorPlan {
  id: string
  name: string
  status: "trialing" | "active" | "past_due" | "canceled"
  trial_ends_at: number | null
  trial_expired: boolean
  seats_paid: number
  owners: string[] // who may change its billing
}

export interface OperatorWorkspace {
  slug: string
  name: string
  created_at: number
  plan: OperatorPlan | null
  members: number
  seats: number // members not turned off who were active in the last 30 days
  usage: { questions: number; tokens: number; cost_usd: number } // last 30 days
  last_activity: number | null
  connectors: number
  connector_errors: { provider: string; error: string | null }[]
  index: { status: string; message: string; finished_at: number | null }
  status: WorkspaceStatus
  suspended_reason: string | null
  deleting_at: number | null
  support_until: number | null // its admin allows support until then
  // Today's model cost (UTC day) and the daily caps: null = no cap.
  spend: { today_usd: number; soft_usd: number | null; hard_usd: number | null }
  open_alerts: number // the abuse and cost monitor's, not yet acknowledged
}

export type AlertKind =
  | "questions_spike"
  | "user_burst"
  | "failed_sign_ins"
  | "signups_per_ip"
  | "spend_soft"
  | "spend_hard"

// What the abuse and cost monitor found (kb/services/abuse.py).
export interface AbuseAlert {
  id: number
  workspace: string // "_platform": not one workspace (sign-ups from one address)
  kind: AlertKind
  title: string
  summary: string
  subject: string | null
  details: Record<string, unknown>
  created_at: number
  notified: string[]
  acknowledged_by: string | null
  acknowledged_at: number | null
}

export interface AbuseThresholds {
  spike_factor: number
  spike_min: number
  user_per_hour: number
  failed_sign_ins: number
  signups_per_ip: number
}

export const operatorApi = {
  providers: () =>
    call<{ providers: { id: string; name: string; url: string }[] }>("/providers"),
  exchange: (code: string) =>
    call<{ token: string; operator: Operator }>("/auth/exchange", post({ code })),
  logout: () => call<{ ok: boolean }>("/auth/logout", post()),
  me: () => call<Operator>("/me"),
  workspaces: () => call<{ workspaces: OperatorWorkspace[] }>("/workspaces"),
  suspend: (ws: string, reason: string) =>
    call<{ ok: boolean }>(`/workspaces/${encodeURIComponent(ws)}/suspend`, post({ reason })),
  unsuspend: (ws: string) =>
    call<{ ok: boolean }>(`/workspaces/${encodeURIComponent(ws)}/unsuspend`, post()),
  reindex: (ws: string) =>
    call<{ ok: boolean }>(`/workspaces/${encodeURIComponent(ws)}/reindex`, post()),
  resetRateLimits: (ws: string) =>
    call<{ ok: boolean; counters: number }>(
      `/workspaces/${encodeURIComponent(ws)}/reset-rate-limits`,
      post(),
    ),
  // More trial: from its end, or from now if it ended (lifts read-only). Audited.
  extendTrial: (ws: string, days: number) =>
    call<{ ok: boolean; trial_ends_at: number }>(
      `/workspaces/${encodeURIComponent(ws)}/extend-trial`,
      post({ days }),
    ),
  // Make a member an owner of the workspace's billing (one made before billing, or
  // whose owner left). Audited.
  addBillingOwner: (ws: string, email: string) =>
    call<{ ok: boolean }>(`/workspaces/${encodeURIComponent(ws)}/billing-owner`, post({ email })),
  // A one-time code: /#sso?code=... opens the workspace as support, in this browser.
  supportSession: (ws: string) =>
    call<{ code: string }>(`/workspaces/${encodeURIComponent(ws)}/support-session`, post()),
  alerts: (openOnly = false) =>
    call<{ alerts: AbuseAlert[]; thresholds: AbuseThresholds }>(
      `/alerts${openOnly ? "?open=true" : ""}`,
    ),
  acknowledge: (id: number) => call<AbuseAlert>(`/alerts/${id}/acknowledge`, post()),
  setSpendCaps: (ws: string, caps: { soft_usd: number | null; hard_usd: number | null }) =>
    call<{ cost_usd: number; soft_usd: number | null; hard_usd: number | null }>(
      `/workspaces/${encodeURIComponent(ws)}/spend-caps`,
      { method: "PUT", body: JSON.stringify(caps) },
    ),
  audit: (ws: string) =>
    call<{
      events: AuditEvent[]
      next_cursor: number | null
      verify: { ok: boolean; count: number }
    }>(`/workspaces/${encodeURIComponent(ws)}/audit`),
}
