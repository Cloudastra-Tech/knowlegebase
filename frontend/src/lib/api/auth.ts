// Signing in and out (/api/auth/*).

import { json, request } from "@/lib/api/client"
import type {
  AuthOptions,
  InviteAccepted,
  InviteInfo,
  PendingSignup,
  SsoRoute,
  User,
} from "@/lib/api/types"

// A full-page navigation (not fetch): the server redirects to Google / Microsoft.
export const ssoStartUrl = (provider: string) => `/api/auth/sso/${provider}/start`
// Sign-up: name the workspace, then confirm with Google Workspace or Microsoft 365.
export const signupStartUrl = (name: string, code = "", method: "google" | "microsoft365" = "google") =>
  `/api/auth/signup/${method === "google" ? "google" : "microsoft"}/start?name=${encodeURIComponent(name)}` +
  (code ? `&code=${encodeURIComponent(code)}` : "")
// Signing in with any company's Microsoft 365 (a workspace made with it).
export const m365StartUrl = (email = "") =>
  `/api/auth/m365/start${email ? `?login_hint=${encodeURIComponent(email)}` : ""}`

export const authApi = {
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
  // Sign-up with a work email and a DNS TXT record: emailed link, then the record.
  signupEmail: (name: string, email: string, code = "") =>
    request<{ ok: boolean; email: string; expires_at: number }>(
      "/auth/signup/email",
      json("POST", { name, email, code }),
    ),
  signupEmailConfirm: (token: string) =>
    request<PendingSignup>("/auth/signup/email/confirm", json("POST", { token })),
  signupEmailComplete: (token: string, password: string) =>
    request<{ token: string; user: User }>(
      "/auth/signup/email/complete",
      json("POST", { token, password }),
    ),
  // The accept page (/invite/:token). The token goes in the body, not a URL.
  inviteLookup: (token: string) =>
    request<InviteInfo>("/auth/invite/lookup", json("POST", { token })),
  inviteAccept: (token: string, name: string, password: string) =>
    request<InviteAccepted>("/auth/invite/accept", json("POST", { token, name, password })),
}
