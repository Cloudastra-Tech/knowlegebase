// Sign-in settings (admin only, /api/admin/sign-in): domains, the workspace's own
// providers, the role rules, and "Test sign-in".

import { json, request, seg } from "@/lib/api/client"
import type {
  GroupRule,
  NewProvider,
  OidcConnectionInput,
  SamlConnectionInput,
  SignInConnection,
  SignInDomain,
  SignInSettings,
  SsoTestResult,
} from "@/lib/api/types"

const editOidc = (id: string, body: OidcConnectionInput) =>
  request<SignInConnection>(`/admin/sign-in/connections/${seg(id)}`, json("PATCH", body))
const editSaml = (id: string, body: SamlConnectionInput) =>
  request<SignInConnection>(`/admin/sign-in/saml-connections/${seg(id)}`, json("PATCH", body))

export const signInSettingsApi = {
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
  editOidc,
  addSaml: (body: SamlConnectionInput) =>
    request<SignInConnection>("/admin/sign-in/saml-connections", json("POST", body)),
  editSaml,
  setEnabled: (c: SignInConnection, enabled: boolean) =>
    c.protocol === "saml" ? editSaml(c.id, { enabled }) : editOidc(c.id, { enabled }),
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
  testResult: (ticket: string) =>
    request<{ done: boolean; result: SsoTestResult | null }>(
      `/admin/sign-in/tests/${seg(ticket)}`,
    ),
}
