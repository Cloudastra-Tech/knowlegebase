// Outbound webhooks (/api/admin/webhooks, admins): where events go, which ones, and
// what was delivered. The signing secret is shown once, when a webhook is added and
// when it's rotated; any delivery can be sent again.

import { json, request, seg } from "@/lib/api/client"

export interface Webhook {
  id: string
  url: string
  description: string | null
  events: string[]
  secret_hint: string
  enabled: boolean
  created_by: string
  created_at: number
  updated_at: number
  // Attempts failed in a row; after WEBHOOK_DISABLE_AFTER of them the server turns it
  // off ("failing", at disabled_at). Turning it on again starts afresh. Absent from a
  // server older than these.
  failures?: number
  disabled_reason?: string | null
  disabled_at?: number | null
  // After a rotation the old secret still signs until then.
  previous_secret_until?: number | null
}

export interface WebhookDelivery {
  id: string
  webhook_id: string
  event: string
  event_id: string
  status: "pending" | "delivered" | "failed"
  attempts: number
  next_at: number | null
  response_code: number | null
  error: string | null
  created_at: number
  last_attempt_at: number | null
  delivered_at: number | null
}

export interface WebhookSettings {
  webhooks: Webhook[]
  events: { id: string; label: string }[]
  retry_hours: number
}

export interface WebhookIn {
  url?: string
  events?: string[]
  description?: string
  enabled?: boolean
}

export const webhooksApi = {
  list: () => request<WebhookSettings>("/admin/webhooks"),
  add: (body: WebhookIn) =>
    request<{ webhook: Webhook; secret: string }>("/admin/webhooks", json("POST", body)),
  change: (id: string, body: WebhookIn) =>
    request<Webhook>(`/admin/webhooks/${seg(id)}`, json("PATCH", body)),
  remove: (id: string) =>
    request<{ ok: boolean }>(`/admin/webhooks/${seg(id)}`, { method: "DELETE" }),
  test: (id: string) =>
    request<WebhookDelivery>(`/admin/webhooks/${seg(id)}/test`, { method: "POST" }),
  deliveries: (id: string) =>
    request<{ deliveries: WebhookDelivery[] }>(`/admin/webhooks/${seg(id)}/deliveries`),
  delivery: (id: string) =>
    request<WebhookDelivery & { payload: unknown }>(`/admin/webhooks/deliveries/${seg(id)}`),
  // A new secret, shown once; the old one keeps signing for grace_hours (default 24).
  rotateSecret: (id: string, graceHours?: number) =>
    request<{ webhook: Webhook; secret: string }>(
      `/admin/webhooks/${seg(id)}/secret`,
      json("POST", graceHours === undefined ? {} : { grace_hours: graceHours }),
    ),
  // The same event and body again, as a new delivery queued now.
  redeliver: (deliveryId: string) =>
    request<WebhookDelivery>(`/admin/webhooks/deliveries/${seg(deliveryId)}/redeliver`, {
      method: "POST",
    }),
}
