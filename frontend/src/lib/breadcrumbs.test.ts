import { describe, expect, it } from "vitest"

import { breadcrumbs, chatCrumbs, type Crumb } from "@/lib/breadcrumbs"

// The trail as text: "Spaces › Marketing team › Sources", each ancestor with where it
// links ("Spaces→/spaces"), a menu as "Name▾".
const text = (crumbs: Crumb[]) =>
  crumbs.map((c) => (c.menu ? `${c.label}▾` : c.to ? `${c.label}→${c.to}` : c.label)).join(" › ")

const at = (url: string, data = {}) => {
  const [path, search = ""] = url.split("?")
  return breadcrumbs(path, search, data)
}

describe("breadcrumbs", () => {
  it.each([
    ["/chat", "Chat"],
    ["/chat/c1", "Chat"],
    ["/knowledge", "Knowledge"],
    ["/documents", "Documents"],
    ["/integrations", "Integrations"],
    ["/admin/verified", "Verified"],
    ["/admin/verified?from_message=m1", "Verified"],
    ["/admin/assistant", "Assistant"],
    ["/spaces", "Spaces"],
    ["/extension/connect", "Browser extension"],
    ["/operator", "Workspaces"],
  ])("%s is a screen of its own: %s", (url, crumbs) => {
    const trail = at(url)
    expect(text(trail.crumbs)).toBe(crumbs)
    expect(trail.back).toBeNull()
  })

  it.each([
    ["/admin/sign-in", "Access→/admin/sign-in › Sign-in"],
    ["/admin/members", "Access→/admin/sign-in › Members"],
    ["/admin/groups", "Access→/admin/sign-in › Groups"],
    ["/admin/activity", "Access→/admin/sign-in › Activity"],
    ["/admin/api", "Access→/admin/sign-in › API & MCP"],
    ["/admin/webhooks", "Access→/admin/sign-in › Webhooks"],
    ["/admin/data", "Access→/admin/sign-in › Data"],
    ["/admin/insights", "Insights→/admin/insights › Overview"],
    ["/admin/insights/not-found", "Insights→/admin/insights › Not found"],
    ["/admin/insights/feedback/", "Insights→/admin/insights › Feedback"],
  ])("a tab with its own address, %s: %s", (url, crumbs) => {
    const trail = at(url)
    expect(text(trail.crumbs)).toBe(crumbs)
    expect(trail.back).toBeNull()
  })

  it("a space: its name and open tab, back to Spaces", () => {
    const trail = at("/spaces/s1", { name: "Marketing team", tab: "Sources" })
    expect(text(trail.crumbs)).toBe("Spaces→/spaces › Marketing team→/spaces/s1 › Sources")
    expect(trail.back).toEqual({ label: "Spaces", to: "/spaces" })
    // Before it's loaded (or when it isn't there): no tab, a stand-in name.
    expect(text(at("/spaces/s1").crumbs)).toBe("Spaces→/spaces › Space")
  })

  it("a draft: Documents › Drafts › its title, back to the drafts", () => {
    const trail = at("/documents?draft=d1", { name: "Q3 handbook" })
    expect(text(trail.crumbs)).toBe(
      "Documents→/documents › Drafts→/documents?tab=drafts › Q3 handbook",
    )
    expect(trail.back).toEqual({ label: "Drafts", to: "/documents?tab=drafts" })
    expect(text(at("/documents?draft=d1&jobs=1").crumbs)).toMatch(/› Draft$/)
    expect(text(at("/documents?tab=drafts").crumbs)).toBe("Documents→/documents › Drafts")
    expect(at("/documents?tab=drafts").back).toBeNull()
  })

  it.each([
    ["/settings/general", "General"],
    ["/settings/chats", "Chats"],
    ["/settings/connections", "Connections"],
    ["/settings/prompts", "Prompts"],
    ["/settings/digests", "Digests"],
    ["/settings/api-keys", "API keys"],
    ["/settings/billing", "Billing"],
    ["/settings/security", "Security"],
  ])("a settings section, %s, is Settings › %s, back to the list on a phone", (url, name) => {
    const trail = at(url)
    expect(text(trail.crumbs)).toBe(`Settings→/settings › ${name}`)
    expect(trail.back).toEqual({ label: "Settings", to: "/settings" })
  })

  it("/settings itself, or a section it doesn't have, is just Settings", () => {
    expect(text(at("/settings").crumbs)).toBe("Settings")
    expect(text(at("/settings/nope").crumbs)).toBe("Settings")
    expect(at("/settings").back).toBeNull()
  })

  it("a shared chat goes back to your chats", () => {
    const trail = at("/s/tok", { name: "A shared answer" })
    expect(text(trail.crumbs)).toBe("Chat→/chat › A shared answer")
    expect(trail.back).toEqual({ label: "Chat", to: "/chat" })
  })

  it("an address it doesn't know has no trail", () => {
    expect(at("/__kit").crumbs).toEqual([])
  })
})

describe("chatCrumbs", () => {
  it("names the space a chat is in, then its title", () => {
    expect(text(chatCrumbs(null, "What is DevIQ"))).toBe("All company→/knowledge › What is DevIQ")
    expect(text(chatCrumbs({ id: "s9", label: "My space" }, "Leave"))).toBe(
      "My space→/spaces/s9 › Leave",
    )
  })

  it("is empty for a new chat, which has no title yet", () => {
    expect(chatCrumbs(null, "")).toEqual([])
  })
})
