// Access groups (Access → Groups, admins) and who a document can be shared with
// (the access pickers, HR / Admin): /api/admin/groups, /api/access/principals.

import { json, request, seg } from "@/lib/api/client"
import type { AccessGroup, PrincipalOption } from "@/lib/api/types"

export const groupsApi = {
  groups: () =>
    request<{ groups: AccessGroup[]; members: { username: string; name: string }[] }>(
      "/admin/groups",
    ),
  createGroup: (name: string, description = "") =>
    request<AccessGroup>("/admin/groups", json("POST", { name, description })),
  editGroup: (id: string, body: { name?: string; description?: string }) =>
    request<AccessGroup>(`/admin/groups/${seg(id)}`, json("PATCH", body)),
  setGroupMembers: (id: string, usernames: string[]) =>
    request<AccessGroup>(`/admin/groups/${seg(id)}/members`, json("PUT", { usernames })),
  deleteGroup: (id: string) => request<{ ok: boolean }>(`/admin/groups/${seg(id)}`, json("DELETE")),
  principals: () => request<{ principals: PrincipalOption[] }>("/access/principals"),
}
