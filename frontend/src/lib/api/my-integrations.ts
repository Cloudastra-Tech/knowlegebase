// My connections: each member's OWN Gmail and Google Calendar (/api/me/integrations).
// Everything a personal connection syncs is for that member alone; no admin endpoint
// reaches it (kb/routers/my_integrations.py).

import { json, request, seg } from "@/lib/api/client"
import type { Container, Integration } from "@/lib/api/types"

export const myIntegrationsApi = {
  myIntegrations: () => request<{ integrations: Integration[] }>("/me/integrations"),
  // Google's consent screen to send the browser to; it comes back to My connections.
  connectMyIntegration: (provider: string) =>
    request<{ url: string }>(`/me/integrations/${seg(provider)}/connect`, { method: "POST" }),
  myIntegrationContainers: (provider: string) =>
    request<{ items: Container[]; picked: Container[] }>(
      `/me/integrations/${seg(provider)}/containers`,
    ),
  setMyIntegrationContainers: (provider: string, containers: Container[]) =>
    request<{ containers: Container[] }>(
      `/me/integrations/${seg(provider)}/containers`,
      json("PUT", { containers }),
    ),
  syncMyIntegration: (provider: string, opts: { full?: boolean; confirm_deletes?: boolean } = {}) =>
    request<{ ok: boolean }>(`/me/integrations/${seg(provider)}/sync`, json("POST", opts)),
  disconnectMyIntegration: (provider: string) =>
    request<{ ok: boolean }>(`/me/integrations/${seg(provider)}`, { method: "DELETE" }),
}
