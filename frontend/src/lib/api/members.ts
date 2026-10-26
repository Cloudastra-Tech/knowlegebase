// The workspace's members (admin only, /api/admin/members).

import { json, request, seg } from "@/lib/api/client"
import type { AccountRole, Member } from "@/lib/api/types"

export const membersApi = {
  members: () => request<{ members: Member[] }>("/admin/members"),
  // role: null goes back to the role their groups give
  editMember: (username: string, body: { role?: AccountRole | null; disabled?: boolean }) =>
    request<Member>(`/admin/members/${seg(username)}`, json("PATCH", body)),
  removeMember: (username: string) =>
    request<{ ok: boolean }>(`/admin/members/${seg(username)}`, json("DELETE")),
  // End every session they have. They can sign in again unless their access is off.
  signOutMember: (username: string) =>
    request<{ ok: boolean; ended: number }>(
      `/admin/members/${seg(username)}/sign-out`,
      json("POST"),
    ),
}
