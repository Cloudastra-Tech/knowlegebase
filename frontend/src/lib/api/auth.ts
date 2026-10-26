// Signing in and out (/api/auth/*).

import { request } from "@/lib/api/client"
import type { AuthOptions, SsoRoute, User } from "@/lib/api/types"

// A full-page navigation (not fetch): the server redirects to Google / Microsoft.
export const ssoStartUrl = (provider: string) => `/api/auth/sso/${provider}/start`
// Sign-up: name the workspace, then confirm with Google Workspace.
export const signupStartUrl = (name: string, code = "") =>
  `/api/auth/signup/google/start?name=${encodeURIComponent(name)}` +
  (code ? `&code=${encodeURIComponent(code)}` : "")

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
}
