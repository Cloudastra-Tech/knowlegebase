// The workspace's members, invites and requests to join (admin only, /api/admin/members).

import { json, request, seg } from "@/lib/api/client"
import type { AccountRole, Invite, JoinPolicy, JoinRequest, Member } from "@/lib/api/types"

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
  // Invites not yet accepted or withdrawn (pending, and expired ones to resend).
  invites: () => request<{ invites: Invite[] }>("/admin/members/invites"),
  invite: (email: string, role: AccountRole) =>
    request<Invite>("/admin/members/invite", json("POST", { email, role })),
  // A new link, emailed again (the last one stops working).
  resendInvite: (id: number) =>
    request<Invite>(`/admin/members/invites/${id}/resend`, json("POST")),
  revokeInvite: (id: number) =>
    request<{ ok: boolean }>(`/admin/members/invites/${id}`, json("DELETE")),
  // Requests to join (the 'request' joining policy), oldest first.
  joinRequests: () =>
    request<{
      requests: JoinRequest[]
      policy: JoinPolicy
      request_days: number
      deny_days: number
    }>("/admin/members/requests"),
  approveRequest: (id: number, role: AccountRole) =>
    request<JoinRequest>(`/admin/members/requests/${id}/approve`, json("POST", { role })),
  denyRequest: (id: number) =>
    request<JoinRequest>(`/admin/members/requests/${id}/deny`, json("POST")),
}
