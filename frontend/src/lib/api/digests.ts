// Digests (/api/digests): everyone's own "what changed" emails or Slack DMs, and the
// one-click unsubscribe link in each email (no sign-in: the link's token is enough).
// What a digest holds is decided when it's sent, for its reader only.

import { json, request } from "@/lib/api/client"

export type DigestFrequency = "daily" | "weekly"
export type DigestChannel = "email" | "slack"

export interface DigestSubscription {
  id: number
  frequency: DigestFrequency
  channel: DigestChannel
  sources: string[] // none: every source
  folders: string[] // none: every folder
  enabled: boolean
  last_sent_at: number | null
  next_due_at: number
  last_error: string | null
  created_at: number
}

export interface DigestSettings {
  subscriptions: DigestSubscription[]
  sources: { id: string; label: string }[] // the sources this person may follow
  channels: { email: boolean; slack: boolean }
  frequencies: DigestFrequency[]
}

export interface DigestIn {
  frequency?: DigestFrequency
  channel?: DigestChannel
  sources?: string[]
  folders?: string[]
  enabled?: boolean
}

export interface UnsubscribeResult {
  enabled: boolean
  frequency: DigestFrequency
  channel: DigestChannel
  workspace_name: string | null
}

export const digestsApi = {
  list: () => request<DigestSettings>("/digests"),
  subscribe: (body: DigestIn) => request<DigestSubscription>("/digests", json("POST", body)),
  change: (id: number, body: DigestIn) =>
    request<DigestSubscription>(`/digests/${id}`, json("PATCH", body)),
  remove: (id: number) => request<{ ok: boolean }>(`/digests/${id}`, { method: "DELETE" }),
  sendNow: (id: number) =>
    request<{ sent?: boolean; entries?: number; error?: string; skipped?: string }>(
      `/digests/${id}/send`,
      { method: "POST" },
    ),
  unsubscribe: (token: string) =>
    request<UnsubscribeResult>("/digests/unsubscribe", json("POST", { token })),
  resubscribe: (token: string) =>
    request<UnsubscribeResult>("/digests/resubscribe", json("POST", { token })),
}
