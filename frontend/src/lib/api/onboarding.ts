// A new workspace's first steps (/api/onboarding: kb/routers/onboarding.py), billing
// or not: the checklist (worked out on the server from the workspace's data, the
// same in every browser), the first-run suggestions, and the sample documents.

import { json, request } from "@/lib/api/client"

export type OnboardingStepId =
  | "domain"
  | "source"
  | "index"
  | "answer"
  | "colleagues"
  | "idp"
  | "slack"

export interface OnboardingStep {
  id: OnboardingStepId
  title: string
  optional: boolean
  done: boolean
  done_at: number | null
}

export interface SampleDocs {
  on: boolean // the sample HR handbook is in the workspace
  available: boolean // this server has it to add
  files: string[]
}

export interface Checklist {
  steps: OnboardingStep[]
  complete: boolean // every step that isn't optional is done
  dismissed: boolean // put away, for the whole workspace
  sample: SampleDocs
}

// Whether this member has seen a product tour (lib/tour.ts): kept on the server, so
// it's the same in every browser. Finished, or closed early ("skipped"): either is seen.
export interface TourProgress {
  seen: boolean
  finished_at: number | null
  skipped_at: number | null
}

export interface Tours {
  members: TourProgress
  admin?: TourProgress // admins only
}

export interface Onboarding {
  first_run: boolean // they haven't asked anything yet
  suggestions: string[] // questions from titles they may read (only on the first run)
  sample: SampleDocs
  tours?: Tours
  checklist?: Checklist // admins viewing as HR
}

export const onboardingApi = {
  get: () => request<Onboarding>("/onboarding"),
  dismiss: (dismissed: boolean) =>
    request<Checklist>("/onboarding/dismissed", json("PUT", { dismissed })),
  addSample: () => request<SampleDocs>("/onboarding/sample", json("POST")),
  removeSample: () => request<SampleDocs>("/onboarding/sample", json("DELETE")),
  tourSeen: (tour: "members" | "admin", outcome: "finished" | "skipped") =>
    request<Tours>(`/onboarding/tours/${tour}`, json("PUT", { outcome })),
}
