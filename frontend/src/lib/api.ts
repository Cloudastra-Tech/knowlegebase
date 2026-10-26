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
  // Some workspace has its own sign-in provider: ask for a work email first.
  email_sso: boolean
  // A new company can create its workspace here (with Google Workspace)...
  signup: boolean
  // ...with an invite code.
  signup_invite: boolean
}

// What the sign-in page's route pass shows for a work email's domain.
export interface SsoRoute {
  connections: SsoConnection[]
  workspace: {
    google: boolean // "Continue with Google Workspace" works for it
    password: boolean // its people may still use a password
    roles_from: string | null // the provider whose groups set roles; null: the admin
  } | null
  signup?: boolean // no workspace yet: can one be created here?
}

// ---- Sign-in settings (admin): domains, the workspace's own providers, events ----
export interface SignInDomain {
  domain: string
  verified: boolean
  verified_at: number | null
  txt_name: string | null // the DNS record that proves it (null for domains set by the server)
  txt_value: string | null
}

export type Protocol = "oidc" | "saml"

export interface SignInConnection {
  id: string
  protocol: Protocol
  label: string
  issuer: string // OIDC issuer, or the SAML IdP's entity ID
  enabled: boolean
  created_at: number
  last_login_at: number | null
  // OpenID Connect
  client_id?: string
  redirect_uri?: string
  logout_at_idp?: boolean // signing out here also signs out there
  supports_logout?: boolean // the provider has a sign-out endpoint
  post_logout_redirect_uri?: string
  // SAML
  sp_entity_id?: string
  acs_url?: string
  sp_metadata_url?: string
  sso_url?: string
  metadata_url?: string | null
  email_attribute?: string | null
  signs_requests?: boolean
  metadata_refreshed_at?: number | null // last fetched again from metadata_url
  cert_expires_at?: number | null // the earliest-expiring signing certificate still valid
  needs_review?: boolean // fetched metadata wasn't applied: another entity ID or host
  review_reason?: string | null
  // Roles from the groups it sends. They only count on the workspace's role
  // source (SignInSettings.role_source); empty: it doesn't set roles.
  group_claim: string | null
  group_rules: GroupRule[]
  health: ProviderHealth
}

// Failing: its latest real sign-ins all failed, since `since` (api.py, sso_errors.py).
export type ProviderHealth =
  | { status: "ok" }
  | {
      status: "failing"
      since: number
      count: number
      code: string
      reason: string
      fix: string | null
    }

export interface GroupRule {
  group: string
  role: AccountRole
}

// Where a member's role comes from: set by an admin, their groups, or neither.
export type RoleSource = "override" | "groups" | "default"

export interface Member {
  username: string
  name: string
  role: AccountRole
  role_source: RoleSource
  override_role: AccountRole | null
  group_role: AccountRole | null
  groups: string[]
  groups_updated_at: number | null // when the role source last sent their groups
  admin_kept: boolean // admin from groups past the limit: kept only as the last admin
  disabled: boolean
  has_password: boolean
  last_login_at: number | null
  created_at: number
}

// What the IdP needs before the provider is added (GET /admin/sign-in/new-provider).
export interface NewProvider {
  id: string
  redirect_uri: string
  sp_entity_id: string
  acs_url: string
  sp_metadata_url: string
}

export interface SignInEvent {
  connection_id: string | null
  connection: string | null
  email: string | null
  outcome: "signed_in" | "refused" | "failed" | "test" | "warning" | "updated" | "attention"
  reason: string | null // a code (sso_errors.py)
  reason_text: string | null // what it means, for the admin
  fix: string | null
  ref: string | null // the reference the person was shown
  at: number
}

export interface SignInSettings {
  domains: SignInDomain[]
  connections: SignInConnection[]
  events: SignInEvent[]
  require_sso: boolean // passwords only for admins
  password_login: boolean // the server allows passwords at all
  role_source: string | null // the one provider whose groups set roles
  group_admin_days: number // admin from groups lasts this long without a sign-in (0: forever)
}

export interface OidcConnectionInput {
  id?: string
  label?: string
  issuer?: string
  client_id?: string
  client_secret?: string
  enabled?: boolean
  logout_at_idp?: boolean
}

export interface SamlConnectionInput {
  id?: string
  label?: string
  metadata_url?: string
  metadata_xml?: string
  entity_id?: string
  sso_url?: string
  certificate?: string
  email_attribute?: string
  enabled?: boolean
}

// The result of an admin's "Test sign-in": what the provider sent, and what would happen.
export interface SsoTestResult {
  ok: boolean
  reason?: string | null // a code (sso_errors.py)
  reason_text?: string | null
  fix?: string | null
  detail?: string | null
  email?: string | null
  name?: string
  role?: AccountRole
  role_source?: RoleSource
  groups?: string[] | null // null: the provider doesn't set roles
  new_member?: boolean
  sets_roles?: boolean // the provider is the role source
  sent: Record<string, string>
}

// A workspace's own provider, found from an email. `url` starts sign-in.
export interface SsoConnection {
  id: string
  name: string
  url: string
  kind?: string | null // "google", "microsoft", "okta", ...: whose logo to show
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
  // A 429's Retry-After: seconds until the rate limit lets this through.
  retryAfter: number | null
  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

function retryAfterOf(res: Response) {
  const seconds = Number(res.headers.get("Retry-After"))
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null
}

// What the server takes per upload (UPLOAD_MAX_FILE_MB / UPLOAD_MAX_REQUEST_MB).
export interface UploadLimits {
  file_mb: number
  request_mb: number
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

const docPath = (d: { access: Access; name: string }) =>
  `/docs/${d.access}/${encodeURIComponent(d.name)}`

// A full-page navigation (not fetch): the server redirects to Google / Microsoft.
export const ssoStartUrl = (provider: string) => `/api/auth/sso/${provider}/start`
// Sign-up: name the workspace, then confirm with Google Workspace.
export const signupStartUrl = (name: string, code = "") =>
  `/api/auth/signup/google/start?name=${encodeURIComponent(name)}` +
  (code ? `&code=${encodeURIComponent(code)}` : "")

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})
const seg = encodeURIComponent

// Sign-in settings (admin only).
export const signIn = {
  get: () => request<SignInSettings>("/admin/sign-in"),
  newProvider: () => request<NewProvider>("/admin/sign-in/new-provider"),
  setPolicy: (require_sso: boolean) =>
    request<{ require_sso: boolean }>("/admin/sign-in/policy", json("PUT", { require_sso })),
  addDomain: (domain: string) =>
    request<SignInDomain>("/admin/sign-in/domains", json("POST", { domain })),
  verifyDomain: (domain: string) =>
    request<SignInDomain>(`/admin/sign-in/domains/${seg(domain)}/verify`, json("POST")),
  removeDomain: (domain: string) =>
    request<{ ok: boolean }>(`/admin/sign-in/domains/${seg(domain)}`, json("DELETE")),
  addOidc: (body: OidcConnectionInput) =>
    request<SignInConnection>("/admin/sign-in/connections", json("POST", body)),
  editOidc: (id: string, body: OidcConnectionInput) =>
    request<SignInConnection>(`/admin/sign-in/connections/${seg(id)}`, json("PATCH", body)),
  addSaml: (body: SamlConnectionInput) =>
    request<SignInConnection>("/admin/sign-in/saml-connections", json("POST", body)),
  editSaml: (id: string, body: SamlConnectionInput) =>
    request<SignInConnection>(`/admin/sign-in/saml-connections/${seg(id)}`, json("PATCH", body)),
  setEnabled: (c: SignInConnection, enabled: boolean) =>
    c.protocol === "saml"
      ? signIn.editSaml(c.id, { enabled })
      : signIn.editOidc(c.id, { enabled }),
  remove: (id: string) =>
    request<{ ok: boolean }>(`/admin/sign-in/connections/${seg(id)}`, json("DELETE")),
  startTest: (id: string) =>
    request<{ ticket: string; url: string }>(
      `/admin/sign-in/connections/${seg(id)}/test`,
      json("POST"),
    ),
  // make_role_source: take over setting roles from another provider (else a 409)
  setGroupRules: (id: string, group_claim: string, rules: GroupRule[], make_role_source = false) =>
    request<SignInConnection>(
      `/admin/sign-in/connections/${seg(id)}/roles`,
      json("PUT", { group_claim: group_claim || null, rules, make_role_source }),
    ),
  setGroupAdminDays: (days: number) =>
    request<{ group_admin_days: number }>(
      "/admin/sign-in/group-admin-days",
      json("PUT", { days }),
    ),
  members: () => request<{ members: Member[] }>("/admin/members"),
  // role: null goes back to the role their groups give
  editMember: (username: string, body: { role?: AccountRole | null; disabled?: boolean }) =>
    request<Member>(`/admin/members/${seg(username)}`, json("PATCH", body)),
  removeMember: (username: string) =>
    request<{ ok: boolean }>(`/admin/members/${seg(username)}`, json("DELETE")),
  // End every session they have. They can sign in again unless their access is off.
  signOutMember: (username: string) =>
    request<{ ok: boolean; ended: number }>(
      `/admin/members/${seg(username)}/sign-out`,
      json("POST"),
    ),
  testResult: (ticket: string) =>
    request<{ done: boolean; result: SsoTestResult | null }>(
      `/admin/sign-in/tests/${seg(ticket)}`,
    ),
}

export const api = {
  authOptions: () => request<AuthOptions>("/auth/providers"),
  ssoDiscover: (email: string) =>
    request<SsoRoute>("/auth/sso/discover", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
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
  // logout_url: where to go next to also sign out at the provider (if it's set to)
  logout: () =>
    request<{ ok: boolean; logout_url: string | null }>("/auth/logout", { method: "POST" }),
  // End every session of this account, on every device (this one too).
  logoutEverywhere: () =>
    request<{ ok: boolean; ended: number }>("/auth/logout-everywhere", { method: "POST" }),
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
      throw new ApiError(res.status, message, retryAfterOf(res))
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

  listDocs: () => request<{ docs: Doc[]; upload_limits?: UploadLimits }>("/docs"),
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
