// The workspace's assistant and its content controls: verified answers
// (/api/admin/verified-answers, HR and admins), source rules (/api/admin/doc-rules,
// HR and admins), and the assistant's settings and glossary (/api/admin/assistant,
// /api/admin/glossary, admins). Members read the assistant's name (/api/assistant).

import { json, request, seg } from "@/lib/api/client"
import type { Access, ChatModel, KnowledgeItem, ModelRef, ModelTier } from "@/lib/api/types"

// ---- Verified answers ----
export type VerifiedStatus = "current" | "due" | "expired"

export interface VerifiedSource {
  doc_key: string
  title: string
  provider: string | null
  access: Access | null
  url: string | null
  missing?: boolean // no longer in the search index
}

export interface VerifiedAnswer {
  id: string
  title: string
  variants: string[]
  answer_md: string
  sources: VerifiedSource[]
  access: Access
  min_access: Access // it was made from something this restricted: never lower
  owner: string
  owner_name: string
  verified_by: string
  verified_by_name: string
  verified_at: number
  review_days: number
  due_at: number
  status: VerifiedStatus
  from_message: string | null
  from_gap: number | null
  created_by: string
  created_at: number
  updated_at: number
}

export interface VerifiedList {
  answers: VerifiedAnswer[]
  owners: { username: string; name: string }[]
  review_days: number[]
  default_review_days: number
  notice_days: number
}

// What the editor sends; a draft (from a chat answer or a content gap) has the same shape.
export interface VerifiedInput {
  title: string
  variants: string[]
  answer_md: string
  sources: string[] // document keys
  access: Access
  owner?: string
  review_days?: number
  from_message?: string | null
  from_gap?: number | null
}

export interface VerifiedDraft extends VerifiedInput {
  min_access: Access
}

// ---- Source rules ----
export type RuleMatch = NonNullable<KnowledgeItem["rule"]>["match_type"]
export type RuleAction = NonNullable<KnowledgeItem["rule"]>["action"]

export interface DocRule {
  id: number
  match_type: RuleMatch
  match_value: string
  action: RuleAction
  weight: number
  created_by?: string
  created_at?: number
  updated_at?: number
}

export interface RuleWeights {
  boost: { min: number; max: number; default: number }
  demote: { min: number; max: number; default: number }
}

// ---- Assistant settings ----
export type Tone = "default" | "friendly" | "formal" | "concise" | "detailed"
export type AnswerCache = "off" | "exact" | "semantic"

// The personal data policy (pii.py): which types are masked in answers and in the
// question log, and flagged at ingest; the workspace's own email domains stay in answers.
export type PiiEntity =
  | "EMAIL"
  | "PHONE"
  | "CARD"
  | "IBAN"
  | "US_SSN"
  | "UK_NI"
  | "IN_PAN"
  | "IN_AADHAAR"

export interface PiiPolicy {
  answers: PiiEntity[]
  logs: PiiEntity[]
  ingest: PiiEntity[]
  allow_own_domains: boolean
  allowed_domains: string[]
  dense_min: number
}

export interface GlossaryEntry {
  id: number
  term: string
  definition: string
  synonyms: string[]
  created_by: string
  updated_at: number
}

export interface AssistantSettings {
  assistant_name: string | null
  tone: Tone
  instructions: string
  language: string // "auto" or a language from choices.languages
  allow_wikipedia: boolean // as applied: the server's switch is a ceiling
  allow_currency: boolean
  allow_wikipedia_server: boolean
  allow_currency_server: boolean
  show_steps: boolean
  adapt_verified: boolean
  answer_cache: AnswerCache
  answer_cache_server: boolean // false: ANSWER_CACHE=off on this server
  cross_lingual: boolean
  pii_policy: PiiPolicy
  own_domains: string[] // the workspace's verified domains
  updated_by: string | null
  updated_at: number | null
  choices: {
    tones: Tone[]
    languages: string[]
    answer_cache: AnswerCache[]
    pii_entities: { id: PiiEntity; label: string }[]
    pii_packs: Record<string, PiiEntity[]>
  }
  limits: {
    name: number
    instructions: number
    term: number
    definition: number
    synonyms: number
    pii_domains: number
  }
  glossary: GlossaryEntry[]
}

export type AssistantChange = Partial<
  Pick<
    AssistantSettings,
    | "assistant_name"
    | "tone"
    | "instructions"
    | "language"
    | "allow_wikipedia"
    | "allow_currency"
    | "show_steps"
    | "adapt_verified"
    | "answer_cache"
    | "cross_lingual"
  >
> & { pii_policy?: PiiPolicy | null }

export interface GlossaryInput {
  term: string
  definition: string
  synonyms: string[]
}

// ---- Model providers (admins): the server's, or the workspace's own, for answers (chat)
// and for search (embeddings) apart. `providers` and `provider_defaults` come from the
// server's registry (models.py), so a provider added there shows up here by itself.
export type ModelProvider = "default" | "bedrock" // the column from before the split
export type AwsAuth = "role" | "keys"

export interface ProviderInfo {
  name: string
  label: string
  chat: boolean // can write answers
  embeddings: boolean // can build the search index
  vision: boolean
  prompt_caching: string
  tool_calls: boolean
  in_house: boolean // text stays in the customer's own cloud account
  api_key: boolean // takes the workspace's own API key
  base_url?: boolean // the admin types its address (Azure's endpoint, a compatible server's URL)
  api_version?: boolean // ... and an api-version (Azure)
  key_optional?: boolean // its key may be left out (a self-hosted server)
  typed_models?: boolean // no curated list: model ids (Azure deployments) are typed in
}

// The address typed for a provider that takes one: never secret, shown back.
export interface ProviderEndpoint {
  base_url: string | null
  api_version: string | null
}

export interface ProviderDefaults {
  chat_model: string | null
  fast_model: string | null
  embed_model: string | null
  embed_dims: number | null
  api_version?: string // Azure: the api-version used when none is typed
}

// A model of a provider's curated list (models.CATALOGUE).
export interface CatalogueModel {
  id: string
  label: string
  tier: ModelTier
  vision: boolean
  tool_calls: boolean
}

export interface EnabledModel extends ChatModel {
  default: boolean
}

export interface ModelProviderSettings {
  provider: ModelProvider
  chat_provider: string // "default" or a provider's name
  embed_provider: string
  region: string | null
  auth: AwsAuth
  role_arn: string | null
  external_id: string // generated by the server, for the role's trust policy
  keys_saved: boolean
  access_key_hint: string | null
  api_keys_saved: Record<string, boolean> // only whether a key is saved, never the key
  api_key_hints?: Record<string, string> // its last 4 characters
  endpoints?: Record<string, ProviderEndpoint> // Azure's endpoint, a compatible server's URL
  chat_models?: EnabledModel[] // the answer models people may pick, the default first
  default_model?: ModelRef | null // null: chat is on the server's provider
  catalogue?: Record<string, CatalogueModel[]> // per chat provider
  max_models?: number
  chat_model: string | null // null: the provider's default
  fast_model: string | null
  embed_model: string | null
  embed_dims: number | null
  tested_at: number | null
  updated_by: string | null
  updated_at: number | null
  // What the workspace runs on now. embed_model is the index's name for it: OpenAI's
  // model alone, else <provider>:<model>:<dimensions>.
  effective: {
    provider: string
    embed_provider: string | null
    chat_model: string
    fast_model: string
    embed_model: string
  }
  server: {
    provider: string | null
    chat_provider?: string | null
    embed_provider?: string | null
    region: string | null
  }
  providers: ProviderInfo[]
  provider_defaults: Record<string, ProviderDefaults>
  defaults: { chat_model: string; fast_model: string; embed_model: string; embed_dims: number | null }
  embed_dimensions: Record<string, number[]>
  index_embeddings: string | null // what the current index was built with; null: none yet
  trust_policy: object
  permissions_policy: object
  reindex?: boolean
}

export interface ModelProviderChange {
  provider?: ModelProvider
  chat_provider?: string
  embed_provider?: string
  api_keys?: Record<string, string | null> // write-only; null removes it
  endpoints?: Record<string, Partial<ProviderEndpoint>>
  region?: string | null
  auth?: AwsAuth
  role_arn?: string | null
  access_key_id?: string
  secret_access_key?: string
  chat_model?: string | null
  fast_model?: string | null
  embed_model?: string | null
  embed_dims?: number | null
  chat_models?: ModelRef[]
  default_model?: ModelRef
  confirm_reindex?: boolean
}

// Test connection: OpenAI, Anthropic, Gemini and an OpenAI-compatible server list their
// models (free); Bedrock and Azure answer one short reply on the default model and make
// one short embedding.
export interface ProviderTest {
  provider: string
  label: string
  ok: boolean // false too when it lists its models but not an enabled one (missing)
  models: number | null // how many it listed; null: it has no list (Bedrock, local)
  missing: string[] // enabled models it didn't list
  checked?: string[] // the models a real call reached (Bedrock)
  error?: string
}

export interface ConnectionTest {
  ok: boolean
  providers: ProviderTest[]
  error?: string // the settings couldn't be tried at all
}

export const assistantApi = {
  // Everyone: the chat's greeting and whether members see the steps panel.
  info: () => request<{ assistant_name: string | null; show_steps: boolean }>("/assistant"),

  verifiedAnswers: () => request<VerifiedList>("/admin/verified-answers"),
  verifiedDraft: (from: { messageId?: string; gapId?: number }) => {
    const q = new URLSearchParams()
    if (from.messageId) q.set("message_id", from.messageId)
    if (from.gapId !== undefined) q.set("gap_id", String(from.gapId))
    return request<VerifiedDraft>(`/admin/verified-answers/draft?${q.toString()}`)
  },
  createVerified: (body: VerifiedInput) =>
    request<VerifiedAnswer>("/admin/verified-answers", json("POST", body)),
  updateVerified: (id: string, body: Partial<VerifiedInput>) =>
    request<VerifiedAnswer>(`/admin/verified-answers/${seg(id)}`, json("PATCH", body)),
  reviewVerified: (id: string) =>
    request<VerifiedAnswer>(`/admin/verified-answers/${seg(id)}/review`, json("POST")),
  deleteVerified: (id: string) =>
    request<{ ok: true }>(`/admin/verified-answers/${seg(id)}`, json("DELETE")),

  docRules: () => request<{ rules: DocRule[]; weights: RuleWeights }>("/admin/doc-rules"),
  addDocRule: (rule: { match_type: RuleMatch; match_value: string; action: RuleAction; weight?: number }) =>
    request<DocRule>("/admin/doc-rules", json("POST", rule)),
  changeDocRule: (id: number, action: RuleAction, weight?: number) =>
    request<DocRule>(`/admin/doc-rules/${id}`, json("PATCH", { action, weight })),
  deleteDocRule: (id: number) => request<{ ok: true }>(`/admin/doc-rules/${id}`, json("DELETE")),

  settings: () => request<AssistantSettings>("/admin/assistant"),
  saveSettings: (change: AssistantChange) =>
    request<AssistantSettings>("/admin/assistant", json("PUT", change)),
  addTerm: (entry: GlossaryInput) => request<GlossaryEntry>("/admin/glossary", json("POST", entry)),
  updateTerm: (id: number, entry: GlossaryInput) =>
    request<GlossaryEntry>(`/admin/glossary/${id}`, json("PATCH", entry)),
  deleteTerm: (id: number) => request<{ ok: true }>(`/admin/glossary/${id}`, json("DELETE")),

  modelProvider: () => request<ModelProviderSettings>("/admin/assistant/models"),
  saveModelProvider: (change: ModelProviderChange) =>
    request<ModelProviderSettings>("/admin/assistant/models", json("PUT", change)),
  testModelProvider: (change: ModelProviderChange) =>
    request<ConnectionTest>("/admin/assistant/models/test", json("POST", change)),
}
