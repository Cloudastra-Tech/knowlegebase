// Thin client for the FastAPI backend (api.py). Vite proxies /api -> :8010.

export type Role = "public" | "hr"
export type Access = "public" | "restricted"

export interface User {
  username: string
  name: string
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
    if (res.status === 401 && path !== "/auth/login") onUnauthorized()
    throw new ApiError(res.status, message)
  }
  return res.json() as Promise<T>
}

const docPath = (d: { access: Access; name: string }) =>
  `/docs/${d.access}/${encodeURIComponent(d.name)}`

export const api = {
  login: (username: string, password: string) =>
    request<{ token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/auth/me"),

  chat: (question: string) =>
    request<ChatReply>("/chat", { method: "POST", body: JSON.stringify({ question }) }),

  listDocs: () => request<{ docs: Doc[]; changed: boolean }>("/docs"),
  readDoc: (d: Doc) => request<{ content: string }>(docPath(d)),
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
  reindex: () =>
    request<{ summary: { documents: number; chunks: number; restricted: number }; log: string }>(
      "/reindex",
      { method: "POST" },
    ),

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
