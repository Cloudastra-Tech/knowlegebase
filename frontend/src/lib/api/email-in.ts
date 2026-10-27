// Email-in (/api/admin/email-in): members email ask@<workspace>.<mail domain> and get
// a cited answer back in the thread. Off until an admin turns it on; the host sets up
// the mail domain and the provider's webhook. HR sees it; only admins change it.

import { json, request } from "@/lib/api/client"

export interface EmailIn {
  enabled: boolean
  configured: boolean // this server has EMAIL_IN_DOMAIN and the webhook's credentials
  address: string | null // ask@<workspace>.<domain>, once the domain is set
  provider: "postmark"
  webhook_url: string // where the provider posts inbound mail (credentials go in front)
  can_manage: boolean // an admin, viewing the app as HR / Admin
  updated_by: string | null
  updated_at: number | null
}

export const emailInApi = {
  get: () => request<EmailIn>("/admin/email-in"),
  setEnabled: (enabled: boolean) =>
    request<EmailIn>("/admin/email-in", json("PUT", { enabled })),
}
