// Connected sources: Google Drive, Notion, ... (/api/integrations).

import { request } from "@/lib/api/client"
import type { ConnectorAccess, DriveFolder, Integration } from "@/lib/api/types"

// Generic for every connector: provider = "google_drive" | "notion".
const connectIntegration = (provider: string) =>
  request<{ url: string }>(`/integrations/${provider}/connect`, { method: "POST" })
const syncIntegration = (provider: string) =>
  request<{ ok: boolean }>(`/integrations/${provider}/sync`, { method: "POST" })
const disconnectIntegration = (provider: string) =>
  request<{ ok: boolean }>(`/integrations/${provider}`, { method: "DELETE" })

export const integrationsApi = {
  integrations: () =>
    request<{ workspace: string; integrations: Integration[] }>("/integrations"),
  connectIntegration,
  syncIntegration,
  disconnectIntegration,
  setIntegrationAccess: (provider: string, access: ConnectorAccess) =>
    request<{ access: ConnectorAccess }>(`/integrations/${provider}/access`, {
      method: "PUT",
      body: JSON.stringify({ access }),
    }),
  connectDrive: () => connectIntegration("google_drive"),
  syncDrive: () => syncIntegration("google_drive"),
  driveFolders: (parent?: string) =>
    request<{ drives: DriveFolder[]; folders: DriveFolder[]; files: DriveFolder[] }>(
      `/integrations/google_drive/folders${parent ? `?parent=${encodeURIComponent(parent)}` : ""}`,
    ),
  setDriveFolders: (folders: DriveFolder[]) =>
    request<{ folders: DriveFolder[] }>("/integrations/google_drive/folders", {
      method: "PUT",
      body: JSON.stringify({ folders }),
    }),
  disconnectDrive: () => disconnectIntegration("google_drive"),
}
