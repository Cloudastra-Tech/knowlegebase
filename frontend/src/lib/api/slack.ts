// The Slack app (/api/admin/slack): which app (this server's, or the workspace's
// own internal one), the Slack team it's installed in, and its settings. HR sees
// it; only admins change it. Secrets go in, never come back.

import { json, request } from "@/lib/api/client"

export type SlackStatus = "not_configured" | "ready" | "installed" | "revoked"
export type SlackMode = "central" | "custom"
export type SlackUnknownUsers = "refuse" | "public"

export interface SlackApp {
  status: SlackStatus
  can_manage: boolean // an admin, viewing the app as HR / Admin
  central_available: boolean // this server has its own Slack app set up
  mode: SlackMode
  client_id: string | null // the workspace's own app
  has_app_secrets: boolean | null
  team_id: string | null
  team_name: string | null
  installed_by: string | null
  installed_at: number | null
  unknown_users: SlackUnknownUsers
  auto_answer: boolean
  auto_answer_channels: string[]
  scopes: string[]
  // Where Slack sends requests (the manifest has them too).
  urls: { events: string; commands: string; interactions: string; redirect: string }
}

export interface SlackAppIn {
  mode: SlackMode
  client_id?: string
  client_secret?: string
  signing_secret?: string
}

export interface SlackSettingsIn {
  unknown_users?: SlackUnknownUsers
  auto_answer?: boolean
  auto_answer_channels?: string[]
}

export const slackApi = {
  get: () => request<SlackApp>("/admin/slack"),
  setApp: (body: SlackAppIn) => request<SlackApp>("/admin/slack/app", json("PUT", body)),
  manifest: () => request<{ manifest: string; filename: string }>("/admin/slack/manifest"),
  // Slack's consent screen, to send the browser to.
  install: () => request<{ url: string }>("/admin/slack/install", { method: "POST" }),
  uninstall: () => request<SlackApp>("/admin/slack/install", { method: "DELETE" }),
  setSettings: (body: SlackSettingsIn) =>
    request<SlackApp>("/admin/slack/settings", json("PUT", body)),
}
