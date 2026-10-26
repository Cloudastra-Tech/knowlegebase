import { createContext } from "react"

import type { Source } from "@/lib/api"

// Inline citations (chat.md C13): what turns an answer's [S1], [S2], ... markers into
// chips, and the words the chips and the source viewer use. The components are in
// components/citations.tsx; kept apart so Fast Refresh can hot-reload that file.
// The server already checked every marker against the passages the question
// actually read (agent.cite()).

const CITE = /\[S(\d{1,3})\]/g
const CITE_HREF = "#cite-"

// Markdown (mdast) as far as the plugin below needs it.
interface MdNode {
  type: string
  value?: string
  url?: string
  children?: MdNode[]
}

// A remark plugin: each [S#] in the answer's text becomes a link to "#cite-#", which
// the answer's Markdown renders as a CitationChip. Code and links are left alone.
export function remarkCitations() {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (!node.children || node.type === "link" || node.type === "linkReference") return
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || !child.value) {
          walk(child)
          return [child]
        }
        const parts: MdNode[] = []
        let last = 0
        for (const m of child.value.matchAll(CITE)) {
          if (m.index > last) parts.push({ type: "text", value: child.value.slice(last, m.index) })
          parts.push({
            type: "link",
            url: `${CITE_HREF}${m[1]}`,
            children: [{ type: "text", value: m[1] }],
          })
          last = m.index + m[0].length
        }
        if (!parts.length) return [child]
        if (last < child.value.length) parts.push({ type: "text", value: child.value.slice(last) })
        return parts
      })
    }
    walk(tree)
  }
}

// The number a "#cite-#" link points at, or null for an ordinary link.
export function citedNumber(href?: string) {
  if (!href?.startsWith(CITE_HREF)) return null
  const n = Number(href.slice(CITE_HREF.length))
  return Number.isInteger(n) && n > 0 ? n : null
}

// The cited sources of one answer, and how to open one in the source viewer.
export interface Citations {
  byNumber: Map<number, Source>
  open: (source: Source) => void
}
export const CitationsContext = createContext<Citations | null>(null)

// Where in its document a passage is: "Page 3 · Handbook › Refunds". A top heading
// that only repeats the document's title is left out.
export function placeOf(source: Pick<Source, "heading_path" | "page" | "title">) {
  const parts = []
  if (source.page) parts.push(`Page ${source.page}`)
  const headings = (source.heading_path ?? "").split(" › ").filter(Boolean)
  if (headings[0]?.toLowerCase() === source.title.toLowerCase()) headings.shift()
  if (headings.length) parts.push(headings.join(" › "))
  return parts.join(" · ")
}

// A passage as one paragraph of plain text, for a short preview (no Markdown
// heading marks, no blank lines).
export function previewOf(snippet: string) {
  return snippet
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim()
}

// "Updated 12 Sep 2026", from an ISO time ("" without one).
export function updatedOn(modified?: string | null) {
  if (!modified) return ""
  const date = new Date(modified)
  if (Number.isNaN(date.getTime())) return ""
  return `Updated ${date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`
}

// How stale a source is, for the hover card: "Updated 8 months ago", "Updated
// 2 years ago"; under a month, the date as updatedOn gives it ("" without one).
export function updatedAgo(modified?: string | null, now: Date = new Date()) {
  if (!modified) return ""
  const date = new Date(modified)
  if (Number.isNaN(date.getTime())) return ""
  const months =
    (now.getFullYear() - date.getFullYear()) * 12 +
    now.getMonth() -
    date.getMonth() -
    (now.getDate() < date.getDate() ? 1 : 0)
  if (months < 1) return updatedOn(modified)
  if (months < 24) return `Updated ${months} month${months === 1 ? "" : "s"} ago`
  return `Updated ${Math.floor(months / 12)} years ago`
}

const ORDINAL = new Intl.PluralRules("en", { type: "ordinal" })
const SUFFIX: Record<string, string> = { one: "st", two: "nd", few: "rd", other: "th" }

// "Why this source" (C14), in plain words: the searched words it contains and its
// rank among the matches. No scores.
export function whyOf(source: Source) {
  const why = source.why
  if (!why) return ""
  const parts = []
  if (why.terms.length) parts.push(`matched ${why.terms.map((t) => `“${t}”`).join(", ")}`)
  if (why.rank) {
    parts.push(`ranked ${why.rank}${SUFFIX[ORDINAL.select(why.rank)]} of ${why.of}`)
    if (why.meaning) parts.push("close in meaning")
  } else parts.push("the opening of a matching document")
  const text = parts.join("; ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// What a chip says to a screen reader: "Source 2: Refund policy, Page 3".
export function citationLabel(n: number, source: Source) {
  const place = placeOf(source)
  return `Source ${n}: ${source.title}${place ? `, ${place}` : ""}`
}
