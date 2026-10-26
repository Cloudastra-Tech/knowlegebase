// API keys for the public API (/api/v1) and the MCP server (/mcp): everyone's own
// (/api/me/api-keys), and the workspace's settings and service keys (admin,
// /api/admin/api). A key itself is only in the answer that makes it.

import { request } from "@/lib/api/client"

export type ApiScope = "ask" | "search" | "docs:read" | "docs:write" | "audit:read"
export type McpTool = "search_knowledge" | "ask" | "read_document" | "list_sources"

export interface ApiKey {
  id: number
  name: string
  kind: "personal" | "service"
  prefix: string
  hint: string // "kb_live_<prefix>_…": enough to tell keys apart
  scopes: ApiScope[]
  created_by: string
  created_at: number
  last_used_at: number | null
  expires_at: number | null
  revoked_at: number | null
  revoked_by: string | null
  status: "active" | "expired" | "revoked"
}

export interface ApiSettings {
  api_enabled: boolean
  mcp_enabled: boolean
  mcp_tools: McpTool[]
  updated_by: string | null
  updated_at: number | null
}

interface Urls {
  api_url: string
  mcp_url: string
  openapi_url: string
}

export interface MyApiKeys extends Urls {
  keys: ApiKey[]
  scopes: ApiScope[] // what this person's role may give a key
  all_scopes: ApiScope[]
  expiry_days: number[]
  default_days: number
  max_keys: number
  settings: ApiSettings
}

export interface AdminApiSettings extends Urls {
  settings: ApiSettings
  keys: ApiKey[] // everyone's, newest first
  tools: McpTool[]
  service_scopes: ApiScope[]
  expiry_days: number[]
  default_days: number
  max_service_keys: number
}

export interface NewApiKey {
  name: string
  scopes: ApiScope[]
  days: number
}

export const apiKeysApi = {
  myApiKeys: () => request<MyApiKeys>("/me/api-keys"),
  createMyApiKey: (body: NewApiKey) =>
    request<ApiKey & { key: string }>("/me/api-keys", { method: "POST", body: JSON.stringify(body) }),
  revokeMyApiKey: (id: number) => request<ApiKey>(`/me/api-keys/${id}`, { method: "DELETE" }),
  adminApi: () => request<AdminApiSettings>("/admin/api"),
  setApiSettings: (body: Partial<Pick<ApiSettings, "api_enabled" | "mcp_enabled" | "mcp_tools">>) =>
    request<ApiSettings>("/admin/api", { method: "PUT", body: JSON.stringify(body) }),
  createServiceKey: (body: NewApiKey) =>
    request<ApiKey & { key: string }>("/admin/api/service-keys", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revokeApiKey: (id: number) => request<ApiKey>(`/admin/api/keys/${id}`, { method: "DELETE" }),
}
