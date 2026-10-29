// Spaces (/api/spaces: your own and your team spaces, their sources, members,
// instructions and your own files), files attached to a chat
// (/api/conversations/{id}/attachments) and saved prompts (/api/prompts).

import { json, request, seg } from "@/lib/api/client"

export type SpaceKind = "personal" | "team"
export type SpaceRole = "owner" | "member"
export type SourceMatch = "doc_key" | "folder" | "provider"

export interface SpaceSummary {
  id: string
  kind: SpaceKind
  name: string
  description: string
  my_role: SpaceRole
  has_instructions: boolean
}

export interface SpaceSource {
  id: number
  match_type: SourceMatch
  match_value: string
  documents: number // how many documents you can see that it matches
}

export interface Attachment {
  id: string
  filename: string
  kind: string // pdf, docx, xlsx, pptx, txt, md, csv, html, image
  size: number
  chunks: number
  tables: number
  flagged: boolean // its text reads like instructions to a model (still only data)
  created_at: number
  expires_at: number | null
}

export interface SpaceDetail extends SpaceSummary {
  instructions: string
  sources: SpaceSource[]
  hidden_sources: number // sources with nothing you may see
  members: { username: string; role: SpaceRole }[]
  files?: Attachment[] // your own space
}

export interface SourceOption {
  value: string
  label: string
  documents?: number
  provider?: string
}

export type SourceOptions = Record<SourceMatch, SourceOption[]>

export interface AttachmentLimits {
  file_mb: number
  per_chat: number
  types: string[] // ".pdf", ".docx", ...
  images: boolean // a model that reads images is set
}

export interface SavedPrompt {
  id: string
  title: string
  body: string
  variables: string[]
  search: boolean // false: answered directly, without searching the knowledge
  workspace: boolean // the workspace's, kept by its admins
  editable: boolean
  updated_at: number
}

const files = (list: File[]) => {
  const form = new FormData()
  list.forEach((f) => form.append("files", f))
  return form
}

export const spacesApi = {
  spaces: () => request<{ spaces: SpaceSummary[]; can_create: boolean }>("/spaces"),
  space: (id: string) => request<SpaceDetail>(`/spaces/${seg(id)}`),
  sourceOptions: () => request<SourceOptions>("/spaces/source-options"),
  createSpace: (body: { name: string; description?: string; instructions?: string }) =>
    request<SpaceSummary>("/spaces", json("POST", body)),
  editSpace: (id: string, body: { name?: string; description?: string; instructions?: string }) =>
    request<SpaceSummary>(`/spaces/${seg(id)}`, json("PATCH", body)),
  deleteSpace: (id: string) => request<{ ok: true }>(`/spaces/${seg(id)}`, json("DELETE")),
  addSource: (id: string, matchType: SourceMatch, value: string) =>
    request<SpaceSource>(`/spaces/${seg(id)}/sources`, json("POST", { match_type: matchType, value })),
  removeSource: (id: string, sourceId: number) =>
    request<{ ok: true }>(`/spaces/${seg(id)}/sources/${sourceId}`, json("DELETE")),
  setMember: (id: string, username: string, role: SpaceRole) =>
    request<{ ok: true }>(`/spaces/${seg(id)}/members/${seg(username)}`, json("PUT", { role })),
  removeMember: (id: string, username: string) =>
    request<{ ok: true }>(`/spaces/${seg(id)}/members/${seg(username)}`, json("DELETE")),
  addSpaceFiles: (id: string, list: File[]) =>
    request<{ files: Attachment[] }>(`/spaces/${seg(id)}/files`, { method: "POST", body: files(list) }),
  removeSpaceFile: (id: string, fileId: string) =>
    request<{ ok: true }>(`/spaces/${seg(id)}/files/${seg(fileId)}`, json("DELETE")),

  attachmentLimits: () => request<AttachmentLimits>("/attachments/limits"),
  attachments: (conversationId: string) =>
    request<{ attachments: Attachment[] }>(`/conversations/${seg(conversationId)}/attachments`).then(
      (r) => r.attachments,
    ),
  attach: (conversationId: string, list: File[]) =>
    request<{ attachments: Attachment[] }>(`/conversations/${seg(conversationId)}/attachments`, {
      method: "POST",
      body: files(list),
    }).then((r) => r.attachments),
  detach: (conversationId: string, id: string) =>
    request<{ ok: true }>(`/conversations/${seg(conversationId)}/attachments/${seg(id)}`, json("DELETE")),

  prompts: () => request<{ prompts: SavedPrompt[]; can_manage_workspace: boolean }>("/prompts"),
  createPrompt: (body: { title: string; body: string; workspace: boolean; search: boolean }) =>
    request<SavedPrompt>("/prompts", json("POST", body)),
  editPrompt: (id: string, body: { title: string; body: string; search: boolean }) =>
    request<SavedPrompt>(`/prompts/${seg(id)}`, json("PUT", body)),
  deletePrompt: (id: string) => request<{ ok: true }>(`/prompts/${seg(id)}`, json("DELETE")),
}
