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

export interface OperatorWorkspace {
  slug: string
  name: string
  created_at: number
  plan: string | null // billing isn't there yet
  members: number
  seats: number // members active in the last 30 days
  usage: { questions: number; tokens: number; cost_usd: number } // last 30 days
  last_activity: number | null
  connectors: number
  connector_errors: { provider: string; error: string | null }[]
  index: { status: string; message: string; finished_at: number | null }
  status: WorkspaceStatus
  suspended_reason: string | null
  deleting_at: number | null
  support_until: number | null // its admin allows support until then
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
  // A one-time code: /#sso?code=... opens the workspace as support, in this browser.
  supportSession: (ws: string) =>
    call<{ code: string }>(`/workspaces/${encodeURIComponent(ws)}/support-session`, post()),
  audit: (ws: string) =>
    call<{
      events: AuditEvent[]
      next_cursor: number | null
      verify: { ok: boolean; count: number }
    }>(`/workspaces/${encodeURIComponent(ws)}/audit`),
}
