// Drafts the assistant writes for HR to review (/api/drafts): a source's or a
// folder's summary (Knowledge), or a doc for a content gap (Insights). Never
// searchable until published, at an access level no lower than their sources'.

import { json, request, seg } from "@/lib/api/client"
import type { Access } from "@/lib/api/types"

export type DraftStatus = "generating" | "draft" | "failed" | "published"

export interface DraftSource {
  doc_key: string
  title: string | null
  provider: string | null
  url: string | null
  access: Access
}

export interface Draft {
  id: string
  kind: "summary" | "gap"
  title: string
  scope: { provider?: string; folder?: string | null; gap_id?: number; label?: string | null }
  status: DraftStatus
  sources: DraftSource[]
  max_access: Access // the most restricted of its sources: the lowest it may be published at
  error: string | null
  created_by: string
  created_at: number
  updated_at: number
  published_as: string | null
  published_at: number | null
  content?: string // one draft read on its own
}

export const draftsApi = {
  list: () => request<{ drafts: Draft[] }>("/drafts"),
  get: (id: string) => request<Draft>(`/drafts/${seg(id)}`),
  summarise: (provider: string, folder?: string | null) =>
    request<Draft>("/drafts/summarise", json("POST", { provider, folder: folder ?? null })),
  fromGap: (gapId: number) => request<Draft>("/drafts/from-gap", json("POST", { gap_id: gapId })),
  save: (id: string, body: { title?: string; content?: string }) =>
    request<Draft>(`/drafts/${seg(id)}`, json("PATCH", body)),
  publish: (id: string, access: Access, name: string) =>
    request<Draft>(`/drafts/${seg(id)}/publish`, json("POST", { access, name })),
  remove: (id: string) => request<{ ok: boolean }>(`/drafts/${seg(id)}`, { method: "DELETE" }),
}
