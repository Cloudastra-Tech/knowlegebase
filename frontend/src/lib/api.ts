// Thin client for the FastAPI backend (api.py). Vite proxies /api -> :8010.

// The role the app is VIEWED as. An admin views it as "hr" (or "public").
export type Role = "public" | "hr"
// What an account may do: admin is hr, plus who may sign in.
export type AccountRole = Role | "admin"
export type Access = "public" | "restricted"

export interface User {
  username: string
  name: string
  workspace: string
  // What the ACCOUNT may do. An HR or admin account can still view the app as "public".
  role: AccountRole
  provider?: string                   // "google", "microsoft", or an OIDC id such as "okta"
}

export interface AuthOptions {
  providers: { id: string; name: string }[]
  password: boolean
}

export type IntegrationStatus =
  | "connected"
  | "syncing"
  | "error"
  | "disconnected"
  | "not_configured"
  | "unavailable"

export interface Integration {
  id: "uploads" | "google_drive" | "notion" | "outline"
  name: string
  status: IntegrationStatus
  doc_count?: number
  account?: string | null
  connected_by?: string | null
  last_sync_at?: number | null
  last_error?: string | null
  folders?: DriveFolder[]
}

// Something picked to sync from Google Drive: a folder, a Shared Drive or one file.
export interface DriveFolder {
  id: string
  name: string
  kind: "folder" | "drive" | "file"
  mimeType?: string
}

export interface Source {
  title: string
  provider: string
  url: string | null
}

export interface Step {
  tool: string
  input: Record<string, unknown>
  output: string
}

export interface ChatReply {
  answer: string
  sources: Source[]
  steps: Step[]
}

// Events from POST /api/chat/stream (see api.py chat_stream).
export type StreamEvent =
  | { type: "status"; text: string }
  | { type: "step_start"; id: string; tool: string; label: string }
  | { type: "step_end"; id: string; tool: string; result: string }
  | { type: "answer"; text: string }
  | ({ type: "done" } & ChatReply)
  | { type: "error"; message: string }

export interface IndexStatus {
  status: "idle" | "indexing" | "error"
  step: number
  total: number
  message: string
  pending: boolean
  summary: { documents: number; chunks: number; restricted: number } | null
  error: string | null
  finished_at: number | null
}

export interface KnowledgeItem {
  title: string
  source: string
  access: Access
  url: string | null
  modified: number | string | null
  size: number
  chunks: number
}

export interface Doc {
  access: Access
  name: string
  size: number
}

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
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// Called when the server says the session is gone (expired / server restart).
let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

// Called when the server refuses something: the account's role may have changed.
let onForbidden: () => void = () => {}
export function setForbiddenHandler(fn: () => void) {
  onForbidden = fn
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
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
    } catch {
      /* not JSON */
    }
    if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized()
    if (res.status === 403 && !path.startsWith("/auth/")) onForbidden()
    throw new ApiError(res.status, message)
  }
  return res.json() as Promise<T>
}

const docPath = (d: { access: Access; name: string }) =>
  `/docs/${d.access}/${encodeURIComponent(d.name)}`

// A full-page navigation (not fetch): the server redirects to Google / Microsoft.
export const ssoStartUrl = (provider: string) => `/api/auth/sso/${provider}/start`

export const api = {
  authOptions: () => request<AuthOptions>("/auth/providers"),
  // The provider sends the browser back to /#sso?code=...; swap the code for a session.
  ssoExchange: (code: string) =>
    request<{ token: string; user: User }>("/auth/sso/exchange", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  login: (username: string, password: string) =>
    request<{ token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/auth/me"),

  chat: (question: string) =>
    request<ChatReply>("/chat", { method: "POST", body: JSON.stringify({ question }) }),

  // Live version of chat(): calls onEvent for each step / answer update as it
  // happens (Server-Sent Events), and resolves with the final reply.
  async chatStream(question: string, onEvent: (e: StreamEvent) => void): Promise<ChatReply> {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.token}`,
        "X-Role": session.role,
      },
      body: JSON.stringify({ question }),
    })
    if (!res.ok || !res.body) {
      let message = `Request failed (${res.status})`
      try {
        const data = await res.json()
        if (typeof data.detail === "string") message = data.detail
      } catch {
        /* not JSON */
      }
      if (res.status === 401) onUnauthorized()
      throw new ApiError(res.status, message)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let cut
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const line = buffer.slice(0, cut).trim()
        buffer = buffer.slice(cut + 2)
        if (!line.startsWith("data:")) continue
        const event = JSON.parse(line.slice(5)) as StreamEvent
        if (event.type === "done") return event
        if (event.type === "error") throw new ApiError(500, event.message)
        onEvent(event)
      }
    }
    throw new ApiError(500, "The answer stream ended unexpectedly.")
  },

  listDocs: () => request<{ docs: Doc[] }>("/docs"),
  readDoc: (d: { access: Access; name: string }) => request<{ content: string }>(docPath(d)),
  createDoc: (name: string, access: Access, content: string) =>
    request<{ access: Access; name: string }>("/docs", {
      method: "POST",
      body: JSON.stringify({ name, access, content }),
    }),
  saveDoc: (d: Doc, content: string) =>
    request<{ ok: boolean }>(docPath(d), { method: "PUT", body: JSON.stringify({ content }) }),
  deleteDoc: (d: Doc) => request<{ ok: boolean }>(docPath(d), { method: "DELETE" }),
  uploadDocs: (files: File[], access: Access) => {
    const form = new FormData()
    files.forEach((f) => form.append("files", f))
    form.append("access", access)
    return request<{ saved: string[] }>("/docs/upload", { method: "POST", body: form })
  },
  reindex: () => request<IndexStatus>("/reindex", { method: "POST" }),
  indexStatus: () => request<IndexStatus>("/index/status"),

  knowledge: () =>
    request<{ workspace: string; items: KnowledgeItem[]; outline: boolean }>("/knowledge"),

  integrations: () =>
    request<{ workspace: string; integrations: Integration[] }>("/integrations"),
  // Generic for every connector: provider = "google_drive" | "notion".
  connectIntegration: (provider: string) =>
    request<{ url: string }>(`/integrations/${provider}/connect`, { method: "POST" }),
  syncIntegration: (provider: string) =>
    request<{ ok: boolean }>(`/integrations/${provider}/sync`, { method: "POST" }),
  disconnectIntegration: (provider: string) =>
    request<{ ok: boolean }>(`/integrations/${provider}`, { method: "DELETE" }),
  connectDrive: () => api.connectIntegration("google_drive"),
  syncDrive: () => api.syncIntegration("google_drive"),
  driveFolders: (parent?: string) =>
    request<{ drives: DriveFolder[]; folders: DriveFolder[]; files: DriveFolder[] }>(
      `/integrations/google_drive/folders${parent ? `?parent=${encodeURIComponent(parent)}` : ""}`,
    ),
  setDriveFolders: (folders: DriveFolder[]) =>
    request<{ folders: DriveFolder[] }>("/integrations/google_drive/folders", {
      method: "PUT",
      body: JSON.stringify({ folders }),
    }),
  disconnectDrive: () => api.disconnectIntegration("google_drive"),

  // PDFs need the auth header, so fetch them as a blob instead of a plain link.
  async downloadDoc(d: Doc) {
    const res = await fetch(`/api${docPath(d)}`, {
      headers: { Authorization: `Bearer ${session.token}`, "X-Role": session.role },
    })
    if (!res.ok) throw new ApiError(res.status, "Download failed")
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement("a")
    a.href = url
    a.download = d.name
    a.click()
    URL.revokeObjectURL(url)
  },
}
