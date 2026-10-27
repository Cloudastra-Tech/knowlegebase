// The workspace itself (admin, /api/admin/workspace: kb/routers/workspace.py) and data
// requests about one member (/api/admin/members/{username}/export | /erase).

import { download, json, request, seg } from "@/lib/api/client"

export type WorkspaceStatus = "active" | "suspended" | "deleting"

export interface WorkspaceExport {
  id: string
  status: "queued" | "running" | "done" | "failed" | "expired"
  requested_by: string
  created_at: number
  finished_at: number | null
  expires_at: number | null // downloadable until then (export_hours after it's made)
  size: number | null
  counts: Record<string, number>
  error: string | null
}

export interface WorkspaceSettings {
  slug: string
  name: string
  status: WorkspaceStatus
  deleting_at: number | null // purged then, unless cancelled
  delete_requested_at: number | null
  delete_requested_by: string | null
  support_until: number | null // Cloudastra support may open it until then
  support_granted_by: string | null
  support_hours: number[]
  grace_days: number
  audit_days: number | null // null: the audit log is kept for good
  audit_day_choices: number[]
  export_hours: number
  exports: WorkspaceExport[]
  can_delete: boolean
  support_session: boolean // this session is Cloudastra support, not the workspace's admin
}

export const workspaceApi = {
  settings: () => request<WorkspaceSettings>("/admin/workspace"),
  startExport: () => request<WorkspaceExport>("/admin/workspace/export", json("POST")),
  downloadExport: (id: string) => download(`/admin/workspace/exports/${seg(id)}/download`),
  // `confirm`: the workspace's name, typed.
  scheduleDeletion: (confirm: string) =>
    request<WorkspaceSettings>("/admin/workspace", json("DELETE", { confirm })),
  cancelDeletion: () =>
    request<WorkspaceSettings>("/admin/workspace/cancel-deletion", json("POST")),
  grantSupport: (hours: number) =>
    request<WorkspaceSettings>("/admin/workspace/support-access", json("PUT", { hours })),
  revokeSupport: () =>
    request<WorkspaceSettings>("/admin/workspace/support-access", json("DELETE")),
  setAuditRetention: (days: number | null) =>
    request<WorkspaceSettings>("/admin/workspace/audit-retention", json("PUT", { days })),
  // Everything held about one member, as a JSON file.
  exportMember: (username: string) => download(`/admin/members/${seg(username)}/export`),
  // `confirm`: their username, typed. Their audit events show the pseudonym from now on.
  eraseMember: (username: string, confirm: string) =>
    request<{ ok: boolean; pseudonym: string; deleted: Record<string, number> }>(
      `/admin/members/${seg(username)}/erase`,
      json("POST", { confirm }),
    ),
}

// Save a downloaded file in the browser.
export function saveFile({ blob, filename }: { blob: Blob; filename: string }) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
