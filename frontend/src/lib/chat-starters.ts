import type { KnowledgeItem } from "@/lib/api"

// Starter questions when a workspace's documents give none (see startersFrom).
export const GENERIC_STARTERS = [
  "What is our leave policy?",
  "How do I claim expenses?",
  "Who do I ask for help with my laptop?",
]

const modifiedAt = (i: KnowledgeItem) =>
  typeof i.modified === "number" ? i.modified * 1000 : Date.parse(i.modified ?? "") || 0

// The chat's starter questions, from what this workspace has indexed and the viewer
// may search (GET /api/knowledge): its most recently changed documents' titles.
// Without any, generic ones.
export function startersFrom(items: KnowledgeItem[] | undefined): string[] {
  const titles = (items ?? [])
    .filter((i) => i.chunks > 0)
    .sort((a, b) => modifiedAt(b) - modifiedAt(a))
    .map((i) => i.title.replace(/\.(txt|md|pdf|docx?)$/i, "").replace(/[_-]+/g, " ").trim())
    .filter((t, i, all) => t && all.indexOf(t) === i)
    .slice(0, 3)
  return titles.length ? titles.map((t) => `What does “${t}” cover?`) : GENERIC_STARTERS
}
