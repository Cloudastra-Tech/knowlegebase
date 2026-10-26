// Connected sources: Google Drive, Notion, Outline, websites, ... (/api/integrations).
// Every connector has the same endpoints; `provider` is its id in the registry.

import { json, request, seg } from "@/lib/api/client"
import type {
  ConnectorAccess,
  Container,
  Integration,
  IntegrationSetup,
  SkippedItem,
  SyncRun,
} from "@/lib/api/types"

export const integrationsApi = {
  integrations: () =>
    request<{ workspace: string; integrations: Integration[] }>("/integrations"),
  // Sign-in connectors: the provider's consent screen to send the browser to.
  connectIntegration: (provider: string) =>
    request<{ url: string }>(`/integrations/${seg(provider)}/connect`, { method: "POST" }),
  // Token / address connectors: the form, checked and saved by the server.
  setupIntegration: (provider: string, form: IntegrationSetup) =>
    request<{ ok: boolean; account: string; choose: boolean }>(
      `/integrations/${seg(provider)}/setup`,
      json("POST", form),
    ),
  syncIntegration: (provider: string, opts: { full?: boolean; confirm_deletes?: boolean } = {}) =>
    request<{ ok: boolean }>(`/integrations/${seg(provider)}/sync`, json("POST", opts)),
  disconnectIntegration: (provider: string) =>
    request<{ ok: boolean }>(`/integrations/${seg(provider)}`, { method: "DELETE" }),
  setIntegrationAccess: (provider: string, access: ConnectorAccess) =>
    request<{ access: ConnectorAccess }>(
      `/integrations/${seg(provider)}/access`,
      json("PUT", { access }),
    ),
  // What can be picked (inside `parent`, for a tree), and what is picked now.
  integrationContainers: (provider: string, parent?: string) =>
    request<{ items: Container[]; picked: Container[] }>(
      `/integrations/${seg(provider)}/containers${parent ? `?parent=${seg(parent)}` : ""}`,
    ),
  setIntegrationContainers: (
    provider: string,
    containers: Container[],
    limits?: { max_pages?: number; max_depth?: number },
  ) =>
    request<{ containers: Container[] }>(
      `/integrations/${seg(provider)}/containers`,
      json("PUT", { containers, ...limits }),
    ),
  integrationRuns: (provider: string) =>
    request<{ runs: SyncRun[]; skipped: SkippedItem[] }>(`/integrations/${seg(provider)}/runs`),
}
