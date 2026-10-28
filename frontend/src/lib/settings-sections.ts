import type { ComponentType } from "react"
import {
  CreditCard,
  KeyRound,
  MessageSquare,
  MessageSquareText,
  Newspaper,
  Plug,
  ShieldCheck,
  UserRound,
} from "lucide-react"

import type { User } from "@/lib/api"

// The Settings screen's sections (components/settings/settings-view.tsx): one URL each,
// /settings/<id>. General is the first; on a phone /settings is the list of them.

export type SettingsSectionId =
  | "general"
  | "chats"
  | "connections"
  | "prompts"
  | "digests"
  | "api-keys"
  | "billing"
  | "security"

export interface SettingsSectionInfo {
  id: SettingsSectionId
  label: string
  icon: ComponentType<{ className?: string }>
}

export const SETTINGS_SECTIONS: SettingsSectionInfo[] = [
  { id: "general", label: "General", icon: UserRound },
  { id: "chats", label: "Chats", icon: MessageSquare },
  { id: "connections", label: "Connections", icon: Plug },
  { id: "prompts", label: "Prompts", icon: MessageSquareText },
  { id: "digests", label: "Digests", icon: Newspaper },
  { id: "api-keys", label: "API keys", icon: KeyRound },
  { id: "billing", label: "Billing", icon: CreditCard },
  { id: "security", label: "Security", icon: ShieldCheck },
]

// The sections this account sees: all of them, but Billing only for whoever may see
// Plan & billing (BILLING=on: admins and billing owners).
export function settingsSectionsFor(user: Pick<User, "billing">) {
  return SETTINGS_SECTIONS.filter((s) => s.id !== "billing" || !!user.billing?.can_view)
}

export const settingsSection = (id: string | undefined) =>
  SETTINGS_SECTIONS.find((s) => s.id === id)

export const settingsPath = (id: SettingsSectionId) => `/settings/${id}`

// The personal screens' old addresses, kept working: each redirects to its section,
// with its query (back from Stripe, Google's consent screen, a digest email's link).
export const MOVED_TO_SETTINGS: [string, SettingsSectionId][] = [
  ["/prompts", "prompts"],
  ["/api-keys", "api-keys"],
  ["/digests", "digests"],
  ["/connections", "connections"],
  ["/billing", "billing"],
]
