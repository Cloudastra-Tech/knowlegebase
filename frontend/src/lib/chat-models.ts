import type { ModelRef, ModelTier } from "@/lib/api"

// How the model picker and the Assistant screen name a model's tier (models.CATALOGUE).
export const TIER_LABEL: Record<ModelTier, string> = {
  fast: "Fast",
  balanced: "Balanced",
  best: "Best",
}

// The same model: provider and id (an id may be another provider's too).
export const sameModel = (a: ModelRef | null | undefined, b: ModelRef | null | undefined) =>
  !!a && !!b && a.provider === b.provider && a.model === b.model
