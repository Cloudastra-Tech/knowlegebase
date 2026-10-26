// Thin client for the FastAPI backend (api.py). Vite proxies /api -> :8010.
// The session (bearer token + the role the app is viewed as) and request(), which
// every domain module (auth.ts, docs.ts, ...) goes through.

import type { Access, Role } from "@/lib/api/types"

const TOKEN_KEY = "kb_token"
const ROLE_KEY = "kb_role"

function storageGet(key: string) {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function storageSet(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* private mode etc. — the app still works for this tab */
  }
}

export const session = {
  token: storageGet(TOKEN_KEY),
  role: (storageGet(ROLE_KEY) as Role | null) ?? "hr",
  setToken(token: string | null) {
    this.token = token
    storageSet(TOKEN_KEY, token)
  },
  setRole(role: Role) {
    this.role = role
    storageSet(ROLE_KEY, role)
  },
}

export class ApiError extends Error {
  status: number
  // A 429's Retry-After: seconds until the rate limit lets this through.
  retryAfter: number | null
  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

export function retryAfterOf(res: Response) {
  const seconds = Number(res.headers.get("Retry-After"))
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null
}

// Called when the server says the session is gone (expired / server restart).
let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}
export const unauthorized = () => onUnauthorized()

// Called when the server refuses something: the account's role may have changed.
let onForbidden: () => void = () => {}
export function setForbiddenHandler(fn: () => void) {
  onForbidden = fn
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (session.token) headers.set("Authorization", `Bearer ${session.token}`)
  headers.set("X-Role", session.role)
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json")
  }
  const res = await fetch(`/api${path}`, { ...init, headers })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const data = await res.json()
      if (typeof data.detail === "string") message = data.detail
      // FastAPI's own validation errors: a list; show the first one.
      else if (Array.isArray(data.detail) && data.detail[0]?.msg) message = data.detail[0].msg
    } catch {
      /* not JSON */
    }
    if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized()
    if (res.status === 403 && !path.startsWith("/auth/")) onForbidden()
    throw new ApiError(res.status, message, retryAfterOf(res))
  }
  return res.json() as Promise<T>
}

export const docPath = (d: { access: Access; name: string }) =>
  `/docs/${d.access}/${encodeURIComponent(d.name)}`

export const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})
export const seg = encodeURIComponent
