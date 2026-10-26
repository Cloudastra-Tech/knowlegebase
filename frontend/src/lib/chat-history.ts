// The history sidebar's data, shaped for showing: pinned chats, chats by folder, the
// rest by when they were last used; and search snippets split into their matches.

import type { ConversationSummary } from "@/lib/api"

export interface ChatGroup {
  label: string
  items: ConversationSummary[]
}

export interface GroupedChats {
  pinned: ConversationSummary[]
  byFolder: Map<string, ConversationSummary[]>
  dated: ChatGroup[] // Today / Yesterday / Previous 7 days / Older, empty ones left out
}

const DAY = 24 * 60 * 60 * 1000

// `chats` are newest first (as the API lists them); `now` is for tests.
export function groupChats(chats: ConversationSummary[], now = new Date()): GroupedChats {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const bounds: [string, number][] = [
    ["Today", today],
    ["Yesterday", today - DAY],
    ["Previous 7 days", today - 7 * DAY],
    ["Older", -Infinity],
  ]
  const pinned: ConversationSummary[] = []
  const byFolder = new Map<string, ConversationSummary[]>()
  const dated = bounds.map(([label]) => ({ label, items: [] as ConversationSummary[] }))
  for (const chat of chats) {
    if (chat.pinned) pinned.push(chat)
    else if (chat.folder_id) byFolder.set(chat.folder_id, [...(byFolder.get(chat.folder_id) ?? []), chat])
    else {
      const at = chat.updated_at * 1000
      dated[bounds.findIndex(([, from]) => at >= from)].items.push(chat)
    }
  }
  return { pinned, byFolder, dated: dated.filter((g) => g.items.length > 0) }
}

// A search snippet ("…the \u0002refund\u0003 policy…") as pieces, the matches marked.
export function splitSnippet(snippet: string): { text: string; match: boolean }[] {
  const out: { text: string; match: boolean }[] = []
  for (const [i, part] of snippet.split(/[\u0002\u0003]/).entries()) {
    if (part) out.push({ text: part, match: i % 2 === 1 })
  }
  return out
}

// "1 year", "90 days".
export function duration(days: number) {
  if (days % 365 === 0) return days === 365 ? "1 year" : `${days / 365} years`
  return `${days} days`
}

// "for 1 year", "until you delete them": how long chats are kept.
export function keptFor(days: number | null) {
  return days === null ? "until you delete them" : `for ${duration(days)}`
}

// The "‹ 2 / 3 ›" of a message with versions: its place among them, and its
// neighbours' ids. null when it has no other version.
export function branchOf(id: string | undefined, siblings: string[] | undefined) {
  if (!id || !siblings || siblings.length < 2) return null
  const index = siblings.indexOf(id)
  if (index < 0) return null
  return {
    index: index + 1,
    count: siblings.length,
    prev: index > 0 ? siblings[index - 1] : null,
    next: index < siblings.length - 1 ? siblings[index + 1] : null,
  }
}
