// The Teams bot (/api/admin/teams): the workspace's own Azure Bot (or this server's),
// the Microsoft tenant it answers, and its settings. HR sees it; only admins change
// it. The client secret goes in, never comes back.

import { download, json, request } from "@/lib/api/client"

export type TeamsStatus = "not_configured" | "configured" | "connected"
export type TeamsMode = "central" | "custom"
export type TeamsUnknownUsers = "refuse" | "public"

export interface TeamsApp {
  status: TeamsStatus
  can_manage: boolean // an admin, viewing the app as HR / Admin
  central_available: boolean // this server has its own (multi-tenant) bot set up
  mode: TeamsMode
  app_id: string | null // the workspace's own bot
  app_tenant: string | null
  tenant_id: string | null // the Microsoft tenant answered
  has_secret: boolean | null
  bot_app_id: string | null // the bot in use, either way
  last_activity_at: number | null
  updated_by: string | null
  updated_at: number | null
  unknown_users: TeamsUnknownUsers
  messaging_endpoint: string // where Azure sends the bot's messages
}

export interface TeamsAppIn {
  mode: TeamsMode
  app_id?: string
  app_tenant?: string
  client_secret?: string
  tenant_id?: string
}

export const teamsApi = {
  get: () => request<TeamsApp>("/admin/teams"),
  setApp: (body: TeamsAppIn) => request<TeamsApp>("/admin/teams/app", json("PUT", body)),
  setSettings: (body: { unknown_users: TeamsUnknownUsers }) =>
    request<TeamsApp>("/admin/teams/settings", json("PUT", body)),
  disconnect: () => request<TeamsApp>("/admin/teams", { method: "DELETE" }),
  // The Teams app to upload: manifest.json and the icons, as a zip.
  appPackage: () => download("/admin/teams/package"),
}
