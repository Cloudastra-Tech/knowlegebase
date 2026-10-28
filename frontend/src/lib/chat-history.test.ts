import { describe, expect, it } from "vitest"

import type { ConversationSummary } from "@/lib/api"
import { branchOf, groupChats, keptFor, splitSnippet } from "@/lib/chat-history"

const NOW = new Date(2026, 9, 27, 15, 0)
const at = (daysAgo: number, hour = 12) =>
  new Date(2026, 9, 27 - daysAgo, hour).getTime() / 1000

function chat(id: string, updated: number, extra: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id,
    title: id,
    role_used: "public",
    role_changed: false,
    pinned: false,
    folder_id: null,
    created_at: updated,
    updated_at: updated,
    ...extra,
  }
}

describe("the history sidebar's groups", () => {
  it("puts pinned chats and folders apart, and the rest by when they were last used", () => {
    const g = groupChats(
      [
        chat("today", at(0, 9)),
        chat("pinned", at(0, 8), { pinned: true }),
        chat("filed", at(1), { folder_id: "f1" }),
        chat("yesterday", at(1, 23)),
        chat("this-week", at(6)),
        chat("old", at(30)),
      ],
      NOW,
    )
    expect(g.pinned.map((c) => c.id)).toEqual(["pinned"])
    expect(g.byFolder.get("f1")?.map((c) => c.id)).toEqual(["filed"])
    expect(g.dated.map((d) => [d.label, d.items.map((c) => c.id)])).toEqual([
      ["Today", ["today"]],
      ["Yesterday", ["yesterday"]],
      ["Previous 7 days", ["this-week"]],
      ["Older", ["old"]],
    ])
  })

  it("leaves out empty groups", () => {
    expect(groupChats([chat("old", at(90))], NOW).dated.map((d) => d.label)).toEqual(["Older"])
  })
})

describe("search snippets", () => {
  it("mark the matched words", () => {
    expect(splitSnippet("…the \u0002refund\u0003 policy for \u0002refunds\u0003")).toEqual([
      { text: "…the ", match: false },
      { text: "refund", match: true },
      { text: " policy for ", match: false },
      { text: "refunds", match: true },
    ])
    expect(splitSnippet("no match")).toEqual([{ text: "no match", match: false }])
  })
})

describe("versions of a message", () => {
  it("give its place among them and its neighbours", () => {
    expect(branchOf("b", ["a", "b", "c"])).toEqual({ index: 2, count: 3, prev: "a", next: "c" })
    expect(branchOf("a", ["a", "b"])).toEqual({ index: 1, count: 2, prev: null, next: "b" })
    expect(branchOf("a", ["a"])).toBeNull()
    expect(branchOf(undefined, ["a", "b"])).toBeNull()
  })
})

describe("retention", () => {
  it("reads as a length of time", () => {
    expect(keptFor(365)).toBe("for 1 year")
    expect(keptFor(90)).toBe("for 90 days")
    expect(keptFor(null)).toBe("until you delete them")
  })
})
