import type { ComponentType } from "react"
import {
  BadgeCheck,
  BarChart3,
  BookOpen,
  Bot,
  FileText,
  MessageSquare,
  Plug,
  ShieldCheck,
} from "lucide-react"

import type { Role, User } from "@/lib/api"

// The screens in the app's sidebar (components/app-sidebar.tsx).

// `section`: every URL under it is this screen (Access has a tab per URL).
export interface NavItem {
  to: string
  section: string
  label: string
  icon: ComponentType
}

// The screens this account sees, as viewed now: everyone has Chat (back to the
// conversation it was at) and Knowledge; viewing as HR, Documents and Integrations,
// and the admin group: Insights and Verified, then Assistant and Access for admins
// alone (HR manages documents, not access).
export function screensFor(user: User, role: Role, chatAt: string) {
  const hr = role === "hr"
  const main: NavItem[] = [
    { to: chatAt, section: "/chat", label: "Chat", icon: MessageSquare },
    { to: "/knowledge", section: "/knowledge", label: "Knowledge", icon: BookOpen },
    ...(hr
      ? [
          { to: "/documents", section: "/documents", label: "Documents", icon: FileText },
          { to: "/integrations", section: "/integrations", label: "Integrations", icon: Plug },
        ]
      : []),
  ]
  const admin: NavItem[] = hr
    ? [
        { to: "/admin/insights", section: "/admin/insights", label: "Insights", icon: BarChart3 },
        { to: "/admin/verified", section: "/admin/verified", label: "Verified", icon: BadgeCheck },
        ...(user.role === "admin"
          ? [
              { to: "/admin/assistant", section: "/admin/assistant", label: "Assistant", icon: Bot },
              { to: "/admin/sign-in", section: "/admin", label: "Access", icon: ShieldCheck },
            ]
          : []),
      ]
    : []
  return { main, admin }
}

export const isUnder = (path: string, section: string) =>
  path === section || path.startsWith(`${section}/`)

// The open screen's section: the most specific wins (/admin/insights is Insights, not
// Access's /admin).
export function currentSection(path: string, items: NavItem[]) {
  return items
    .map((item) => item.section)
    .filter((s) => isUnder(path, s))
    .sort((a, b) => b.length - a.length)[0]
}

// The screens reached from the account menu (components/account-menu.tsx), by name,
// in its order.
export const ACCOUNT_SCREENS: [string, string][] = [
  ["/spaces", "Spaces"],
  ["/prompts", "My prompts"],
  ["/api-keys", "API keys"],
  ["/digests", "Digests"],
  ["/connections", "My connections"],
  ["/billing", "Plan & billing"],
]

// Those and the screens reached from outside the app, for the phone's top bar.
const PERSONAL: [string, string][] = [
  ...ACCOUNT_SCREENS,
  ["/extension", "Browser extension"],
  ["/passages", "Passage"],
]

// The Access screen's tabs, each its own URL (/admin/<tab>, routes.tsx).
export type AccessTab = "sign-in" | "members" | "groups" | "activity" | "api" | "webhooks" | "data"

export const ACCESS_TABS: [AccessTab, string][] = [
  ["sign-in", "Sign-in"],
  ["members", "Members"],
  ["groups", "Groups"],
  ["activity", "Activity"],
  ["api", "API & MCP"],
  ["webhooks", "Webhooks"],
  ["data", "Data"],
]

// The Insights screen's tabs: /admin/insights, /admin/insights/<tab>.
export type InsightsTab = "overview" | "not-found" | "feedback"

export const INSIGHTS_TABS: [InsightsTab, string][] = [
  ["overview", "Overview"],
  ["not-found", "Not found"],
  ["feedback", "Feedback"],
]

// The open screen's name (the phone's top bar).
export function screenTitle(path: string, items: NavItem[]) {
  const section = currentSection(path, items)
  const item = items.find((i) => i.section === section)
  return item?.label ?? PERSONAL.find(([s]) => isUnder(path, s))?.[1] ?? "Knowledge assistant"
}
