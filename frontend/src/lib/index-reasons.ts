import type { IndexReason, IndexStatus } from "@/lib/api"

// Why indexing stopped, in words for everyone (the reason code from
// kb/services/indexing.py). The exception's own text is only ever shown to admins.
const PAUSED: Record<Exclude<IndexReason, "unknown">, string> = {
  no_ai_key: "no AI provider key set",
  ai_key_rejected: "the AI provider key was rejected",
  ai_quota: "the AI provider's quota or rate limit was reached",
  ai_unreachable: "the AI provider can't be reached",
}

export interface IndexProblem {
  short: string // the header's few words
  sentence: string // the whole plain status
}

// A run that failed, or (retrying) one waiting to try again after failing.
export function describeReason(reason: IndexReason | null, retrying: boolean): IndexProblem {
  const why = reason && reason !== "unknown" ? PAUSED[reason] : null
  const short = why || retrying ? "Indexing paused" : "Indexing failed"
  const sentence = why ? `${short}: ${why}` : short
  return { short, sentence: retrying ? `${sentence}. Trying again soon.` : `${sentence}.` }
}

// The index's problem, or null when all is well.
export function indexProblem(status: IndexStatus | null): IndexProblem | null {
  if (!status) return null
  if (status.status === "error") return describeReason(status.reason, false)
  if (status.status === "indexing" && status.reason) return describeReason(status.reason, true)
  return null
}

const JOB_KIND: Record<string, string> = {
  index: "Indexing",
  retag: "Updating who can see files",
  drop_collection: "Clearing an old index",
  sync: "Syncing a source",
  digest: "Sending a digest",
  draft: "Writing a draft",
  webhooks: "Sending webhooks",
  workspace_export: "Exporting the workspace",
  slack: "Answering in Slack",
  teams: "Answering in Teams",
  email_in: "Answering an email",
}

// A background job's kind in words (kb/jobs.py handlers); an unknown one, tidied.
export const jobKind = (kind: string) =>
  JOB_KIND[kind] ?? kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())
