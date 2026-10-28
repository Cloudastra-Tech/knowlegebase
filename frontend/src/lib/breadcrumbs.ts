import { useContext } from "react"
import { useLocation } from "react-router"

import { ACCESS_TABS, ACCOUNT_SCREENS, INSIGHTS_TABS, isUnder } from "@/lib/app-screens"
import { AuthContext } from "@/lib/auth-context"
import { ALL_COMPANY } from "@/lib/spaces"

// Where a screen sits, for the breadcrumbs over its title (components/breadcrumbs.tsx):
// one model for every screen, from the address plus what only the screen knows (a
// space's name, a draft's title). The first crumb is the sidebar's screen (or the
// account menu, for the personal screens); a screen's tabs come after it; the last
// crumb is the page itself.

export interface CrumbLink {
  label: string
  to: string
}

// An ancestor links to its page; a group without a page of its own (the account
// menu's screens) opens a menu of them instead; the last crumb, the page, has neither.
export interface Crumb {
  label: string
  to?: string
  menu?: CrumbLink[]
}

export interface Trail {
  crumbs: Crumb[]
  // The logical parent of a detail view (a space, a draft): where the back button
  // goes. Not history: a deep link has none. Null on a screen of the sidebar's or
  // one of its tabs.
  back: CrumbLink | null
}

// What the screen tells the model.
export interface CrumbData {
  name?: string // the open item's own name: a space's, a draft's, a shared chat's kind
  tab?: string // the open tab, when the address doesn't say (a space's)
  billing?: boolean // Plan & billing is in the account menu (BILLING=on, and allowed)
}

// The account menu's screens, as a crumb: "Account", opening them.
export const ACCOUNT = "Account"

const SCREENS: [string, string][] = [
  ["/chat", "Chat"],
  ["/knowledge", "Knowledge"],
  ["/documents", "Documents"],
  ["/integrations", "Integrations"],
  ["/admin/verified", "Verified"],
  ["/admin/assistant", "Assistant"],
  ["/extension", "Browser extension"],
  ["/passages", "Passage"],
  ["/operator", "Workspaces"],
]

const DRAFTS = "/documents?tab=drafts"

const page = (label: string): Crumb => ({ label })
const trail = (crumbs: Crumb[], back: CrumbLink | null = null): Trail => ({ crumbs, back })

export function breadcrumbs(pathname: string, search = "", data: CrumbData = {}): Trail {
  const path = pathname.replace(/\/+$/, "") || "/"
  const params = new URLSearchParams(search)

  // Spaces, then a space, then its open tab.
  if (isUnder(path, "/spaces")) {
    const id = path.split("/")[2]
    if (!id) return trail([page("Spaces")])
    const spaces = { label: "Spaces", to: "/spaces" }
    const name = data.name ?? "Space"
    const crumbs: Crumb[] = data.tab
      ? [spaces, { label: name, to: `/spaces/${id}` }, page(data.tab)]
      : [spaces, page(name)]
    return trail(crumbs, spaces)
  }

  // Documents, its drafts, then one draft (?draft=<id>).
  if (isUnder(path, "/documents")) {
    const documents = { label: "Documents", to: "/documents" }
    if (params.get("draft")) {
      const drafts = { label: "Drafts", to: DRAFTS }
      return trail([documents, drafts, page(data.name ?? "Draft")], drafts)
    }
    if (params.get("tab") === "drafts") return trail([documents, page("Drafts")])
    return trail([page("Documents")])
  }

  // Insights and its tabs (the overview is /admin/insights).
  if (isUnder(path, "/admin/insights")) {
    const tab = path.split("/")[3] ?? "overview"
    const label = INSIGHTS_TABS.find(([t]) => t === tab)?.[1]
    if (!label) return trail([page("Insights")])
    return trail([{ label: "Insights", to: "/admin/insights" }, page(label)])
  }

  // Access: each tab is /admin/<tab>.
  const access = ACCESS_TABS.find(([t]) => path === `/admin/${t}`)
  if (access) return trail([{ label: "Access", to: "/admin/sign-in" }, page(access[1])])

  // The personal screens: under the account menu, which they're opened from.
  const personal = ACCOUNT_SCREENS.find(([s]) => s !== "/spaces" && isUnder(path, s))
  if (personal) {
    const menu = ACCOUNT_SCREENS.filter(([s]) => s !== "/billing" || data.billing).map(
      ([to, label]) => ({ to, label }),
    )
    return trail([{ label: ACCOUNT, menu }, page(personal[1])])
  }

  // A chat or answer someone shared: back to your own chats.
  if (isUnder(path, "/s")) {
    const chat = { label: "Chat", to: "/chat" }
    return trail([chat, page(data.name ?? "Shared chat")], chat)
  }

  const screen = SCREENS.find(([s]) => isUnder(path, s))
  return trail(screen ? [page(screen[1])] : [])
}

// The trail for the screen at this address (ScreenHeader).
export function useTrail(data: CrumbData = {}): Trail {
  const { pathname, search } = useLocation()
  const billing = useContext(AuthContext)?.user?.billing?.can_view ?? false
  return breadcrumbs(pathname, search, { billing, ...data })
}

// The chat's header: the space it's asked in, then its title. A team space links to
// its page; your own space and All company (the whole knowledge base) to theirs.
export function chatCrumbs(
  space: { id: string; label: string } | null,
  title: string,
): Crumb[] {
  if (!title) return []
  const where: Crumb = space
    ? { label: space.label, to: `/spaces/${space.id}` }
    : { label: ALL_COMPANY, to: "/knowledge" }
  return [where, page(title)]
}
