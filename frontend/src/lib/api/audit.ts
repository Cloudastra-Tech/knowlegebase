// The admin audit log (admin only, /api/admin/audit).

import { download, request } from "@/lib/api/client"
import type { AuditEvent, AuditFilters } from "@/lib/api/types"

function query(params: Record<string, string | number | undefined | null>) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ""
}

export const auditApi = {
  // A page, newest first; pass next_cursor back as `cursor` for the next one.
  audit: (filters: AuditFilters, cursor?: number | null) =>
    request<{ events: AuditEvent[]; next_cursor: number | null }>(
      `/admin/audit${query({ ...filters, cursor })}`,
    ),
  // Every matching event, oldest first: CSV for spreadsheets, JSON lines exactly as hashed.
  exportAudit: (filters: AuditFilters, format: "csv" | "jsonl") =>
    download(`/admin/audit/export${query({ ...filters, format })}`),
  verifyAudit: () =>
    request<{ ok: boolean; count: number; head: string | null; broken_at: number | null }>(
      "/admin/audit/verify",
    ),
}
