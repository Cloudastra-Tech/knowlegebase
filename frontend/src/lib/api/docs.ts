// Documents, the search index and the knowledge list (/api/docs, /api/reindex,
// /api/index/status, /api/knowledge).

import { ApiError, docPath, request, session } from "@/lib/api/client"
import type { DocRule } from "@/lib/api/assistant"
import type { Access, Doc, IndexStatus, KnowledgeItem, UploadLimits } from "@/lib/api/types"

export const docsApi = {
  listDocs: () => request<{ docs: Doc[]; upload_limits?: UploadLimits }>("/docs"),
  readDoc: (d: { access: Access; name: string }) => request<{ content: string }>(docPath(d)),
  createDoc: (name: string, access: Access, content: string) =>
    request<{ access: Access; name: string }>("/docs", {
      method: "POST",
      body: JSON.stringify({ name, access, content }),
    }),
  saveDoc: (d: Doc, content: string) =>
    request<{ ok: boolean }>(docPath(d), { method: "PUT", body: JSON.stringify({ content }) }),
  deleteDoc: (d: Doc) => request<{ ok: boolean }>(docPath(d), { method: "DELETE" }),
  uploadDocs: (files: File[], access: Access) => {
    const form = new FormData()
    files.forEach((f) => form.append("files", f))
    form.append("access", access)
    return request<{ saved: string[] }>("/docs/upload", { method: "POST", body: form })
  },
  reindex: () => request<IndexStatus>("/reindex", { method: "POST" }),
  indexStatus: () => request<IndexStatus>("/index/status"),

  // rules: the workspace's source rules, for HR / Admin only.
  knowledge: () =>
    request<{ workspace: string; items: KnowledgeItem[]; outline: boolean; rules?: DocRule[] }>(
      "/knowledge",
    ),

  // PDFs need the auth header, so fetch them as a blob instead of a plain link.
  async downloadDoc(d: Doc) {
    const res = await fetch(`/api${docPath(d)}`, {
      headers: { Authorization: `Bearer ${session.token}`, "X-Role": session.role },
    })
    if (!res.ok) throw new ApiError(res.status, "Download failed")
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement("a")
    a.href = url
    a.download = d.name
    a.click()
    URL.revokeObjectURL(url)
  },
}
