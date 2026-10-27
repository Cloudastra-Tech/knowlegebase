// The browser extension (/api/extension, kb/routers/extension.py): whether this
// server lets it connect, the signed-in person's connected extensions, and the
// "Connect" step of its sign-in (a one-time code the extension swaps for its key).

import { json, request } from "@/lib/api/client"

export interface ConnectedExtension {
  id: number // its API key's id: revoke it like any of your keys
  name: string
  hint: string
  created_at: number
  last_used_at: number | null
  expires_at: number | null
}

export interface ExtensionStatus {
  configured: boolean // EXTENSION_ORIGINS is set: the extension can be connected
  api_enabled: boolean // the extension asks through the public API
  connected: ConnectedExtension[]
  key_days: number
}

export const extensionApi = {
  get: () => request<ExtensionStatus>("/extension"),
  // The extension's chromiumapp.org address -> a one-time code to send it back with.
  connect: (redirect_uri: string) =>
    request<{ code: string; expires_in: number }>(
      "/extension/connect",
      json("POST", { redirect_uri }),
    ),
}
