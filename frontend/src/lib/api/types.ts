// The shapes the FastAPI backend (api.py) sends and takes.

// The role the app is VIEWED as. An admin views it as "hr" (or "public").
export type Role = "public" | "hr"
// What an account may do: admin is hr, plus who may sign in.
export type AccountRole = Role | "admin"
export type Access = "public" | "restricted"

export interface User {
  username: string
  name: string
  workspace: string
  // What the ACCOUNT may do. An HR or admin account can still view the app as "public".
  role: AccountRole
  provider?: string                   // "google", "microsoft", or an OIDC id such as "okta"
}

export interface AuthOptions {
  providers: { id: string; name: string }[]
  password: boolean
  // Some workspace has its own sign-in provider: ask for a work email first.
  email_sso: boolean
  // A new company can create its workspace here (with Google Workspace)...
  signup: boolean
  // ...with an invite code.
  signup_invite: boolean
}

// What the sign-in page's route pass shows for a work email's domain.
export interface SsoRoute {
  connections: SsoConnection[]
  workspace: {
    google: boolean // "Continue with Google Workspace" works for it
    password: boolean // its people may still use a password
    roles_from: string | null // the provider whose groups set roles; null: the admin
  } | null
  signup?: boolean // no workspace yet: can one be created here?
}

// ---- Sign-in settings (admin): domains, the workspace's own providers, events ----
export interface SignInDomain {
  domain: string
  verified: boolean
  verified_at: number | null
  txt_name: string | null // the DNS record that proves it (null for domains set by the server)
  txt_value: string | null
}

export type Protocol = "oidc" | "saml"

export interface SignInConnection {
  id: string
  protocol: Protocol
  label: string
  issuer: string // OIDC issuer, or the SAML IdP's entity ID
  enabled: boolean
  created_at: number
  last_login_at: number | null
  // OpenID Connect
  client_id?: string
  redirect_uri?: string
  logout_at_idp?: boolean // signing out here also signs out there
  supports_logout?: boolean // the provider has a sign-out endpoint
  post_logout_redirect_uri?: string
  // SAML
  sp_entity_id?: string
  acs_url?: string
  sp_metadata_url?: string
  sso_url?: string
  metadata_url?: string | null
  email_attribute?: string | null
  signs_requests?: boolean
  metadata_refreshed_at?: number | null // last fetched again from metadata_url
  cert_expires_at?: number | null // the earliest-expiring signing certificate still valid
  needs_review?: boolean // fetched metadata wasn't applied: another entity ID or host
  review_reason?: string | null
  // Roles from the groups it sends. They only count on the workspace's role
  // source (SignInSettings.role_source); empty: it doesn't set roles.
  group_claim: string | null
  group_rules: GroupRule[]
  health: ProviderHealth
}

// Failing: its latest real sign-ins all failed, since `since` (api.py, sso_errors.py).
export type ProviderHealth =
  | { status: "ok" }
  | {
      status: "failing"
      since: number
      count: number
      code: string
      reason: string
      fix: string | null
    }

export interface GroupRule {
  group: string
  role: AccountRole
}

// Where a member's role comes from: set by an admin, their groups, or neither.
export type RoleSource = "override" | "groups" | "default"

export interface Member {
  username: string
  name: string
  role: AccountRole
  role_source: RoleSource
  override_role: AccountRole | null
  group_role: AccountRole | null
  groups: string[]
  groups_updated_at: number | null // when the role source last sent their groups
  admin_kept: boolean // admin from groups past the limit: kept only as the last admin
  disabled: boolean
  has_password: boolean
  last_login_at: number | null
  created_at: number
}

// What the IdP needs before the provider is added (GET /admin/sign-in/new-provider).
export interface NewProvider {
  id: string
  redirect_uri: string
  sp_entity_id: string
  acs_url: string
  sp_metadata_url: string
}

export interface SignInEvent {
  connection_id: string | null
  connection: string | null
  email: string | null
  outcome: "signed_in" | "refused" | "failed" | "test" | "warning" | "updated" | "attention"
  reason: string | null // a code (sso_errors.py)
  reason_text: string | null // what it means, for the admin
  fix: string | null
  ref: string | null // the reference the person was shown
  at: number
}

export interface SignInSettings {
  domains: SignInDomain[]
  connections: SignInConnection[]
  events: SignInEvent[]
  require_sso: boolean // passwords only for admins
  password_login: boolean // the server allows passwords at all
  role_source: string | null // the one provider whose groups set roles
  group_admin_days: number // admin from groups lasts this long without a sign-in (0: forever)
}

export interface OidcConnectionInput {
  id?: string
  label?: string
  issuer?: string
  client_id?: string
  client_secret?: string
  enabled?: boolean
  logout_at_idp?: boolean
}

export interface SamlConnectionInput {
  id?: string
  label?: string
  metadata_url?: string
  metadata_xml?: string
  entity_id?: string
  sso_url?: string
  certificate?: string
  email_attribute?: string
  enabled?: boolean
}

// The result of an admin's "Test sign-in": what the provider sent, and what would happen.
export interface SsoTestResult {
  ok: boolean
  reason?: string | null // a code (sso_errors.py)
  reason_text?: string | null
  fix?: string | null
  detail?: string | null
  email?: string | null
  name?: string
  role?: AccountRole
  role_source?: RoleSource
  groups?: string[] | null // null: the provider doesn't set roles
  new_member?: boolean
  sets_roles?: boolean // the provider is the role source
  sent: Record<string, string>
}

// A workspace's own provider, found from an email. `url` starts sign-in.
export interface SsoConnection {
  id: string
  name: string
  url: string
  kind?: string | null // "google", "microsoft", "okta", ...: whose logo to show
}

export type IntegrationStatus =
  | "connected"
  | "syncing"
  | "error"
  | "disconnected"
  | "not_configured"
  | "unavailable"

export interface Integration {
  id: "uploads" | "google_drive" | "notion" | "outline"
  name: string
  status: IntegrationStatus
  doc_count?: number
  account?: string | null
  connected_by?: string | null
  last_sync_at?: number | null
  last_error?: string | null
  access?: ConnectorAccess // who may ask about a connection's synced files
  folders?: DriveFolder[]
}

// "restricted": HR and admins only (the default). "everyone": the whole workspace.
export type ConnectorAccess = "restricted" | "everyone"

// Something picked to sync from Google Drive: a folder, a Shared Drive or one file.
export interface DriveFolder {
  id: string
  name: string
  kind: "folder" | "drive" | "file"
  mimeType?: string
}

// Why a passage was found (agent.py _source): its rank among the search's matches
// (null: the opening of a matched document, added for context), whether the
// meaning search found it, and the searched words it contains.
export interface SourceWhy {
  rank: number | null
  of: number
  meaning: boolean
  terms: string[]
}

// Where a passage sits in its document (rag.passage_of).
export interface PassagePlace {
  doc_id?: string | null
  chunk_id?: string | null // opens the passage (GET /api/passages/{chunk_id})
  page?: number | null // PDFs, 1-based
  heading_path?: string | null // "Handbook › Refunds"
  char_start?: number | null
  char_end?: number | null
  modified?: string | null // ISO time the document last changed
}

export interface Source extends PassagePlace {
  n?: number // a cited passage: the answer's [S{n}] marker
  title: string
  provider: string
  url: string | null
  kind?: "general_knowledge" // Wikipedia: not from the company's documents
  access?: Access
  snippet?: string // the passage, as it was when the answer was written
  why?: SourceWhy
}

// GET /api/passages/{chunk_id}: a passage and its document's text around it.
export interface Passage extends PassagePlace {
  title: string
  provider: string
  url: string | null
  access: Access
  text: string
  before: string
  after: string
}

// A tool the assistant used. Its raw input and output come to admins only.
export interface Step {
  tool: string
  input?: Record<string, unknown>
  output?: string
}

// How an answer ended (kb/services/conversations.py outcome()). "pending": still
// being written; "stopped": the asker pressed Stop (what was written so far is kept).
export type AnswerState =
  | "answered"
  | "partial"
  | "not_found"
  | "blocked"
  | "general_knowledge"
  | "error"
  | "stopped"
  | "pending"

export interface ChatReply {
  answer: string
  sources: Source[]
  steps: Step[]
  state: AnswerState
  standalone_question?: string | null // a follow-up as it was searched
  message_id?: string | null // saved in a conversation
}

// Events from POST /api/chat/stream (kb/routers/chat.py chat_stream).
export type StreamEvent =
  | {
      type: "conversation"
      id: string
      title: string | null
      user_message_id: string
      message_id: string
      new_conversation: boolean // the role changed, so a new conversation was started
    }
  | { type: "status"; text: string }
  | { type: "step_start"; id: string; tool: string; label: string }
  | { type: "step_end"; id: string; tool: string; result: string }
  | { type: "answer"; text: string; sources?: Source[] } // sources: when the cited ones change
  | ({ type: "done" } & ChatReply)
  | { type: "error"; message: string; message_id?: string | null }

// ---- Saved conversations (kb/routers/conversations.py) ----
export interface ConversationSummary {
  id: string
  title: string
  role_used: Role
  role_changed: boolean // viewed as another role now: the next question starts a new chat
  created_at: number
  updated_at: number
}

// One line of the steps panel ("Searching company docs · found 2 relevant documents").
export interface StepLine {
  id: string
  tool?: string
  label: string
  result?: string
}

export type FeedbackReason =
  | "wrong"
  | "wrong_source"
  | "missing"
  | "outdated"
  | "refused"
  | "unclear"

export interface Feedback {
  rating: 1 | -1
  reasons: FeedbackReason[]
  comment: string | null
}

export interface SavedMessage {
  id: string
  parent_id: string | null
  author: "user" | "assistant"
  content: string
  state: AnswerState | null // null for questions
  created_at: number
  // The answer used documents the viewer's role can't see now: no content or sources.
  withheld: boolean
  sources?: Source[]
  steps?: StepLine[]
  trace?: Step[] // admins only
  standalone_question?: string | null
  feedback?: Feedback | null
}

export interface Conversation extends ConversationSummary {
  messages: SavedMessage[]
}

export interface IndexStatus {
  status: "idle" | "indexing" | "error"
  step: number
  total: number
  message: string
  pending: boolean
  summary: { documents: number; chunks: number; restricted: number } | null
  error: string | null
  finished_at: number | null
}

export interface KnowledgeItem {
  title: string
  source: string
  access: Access
  url: string | null
  modified: number | string | null
  size: number
  chunks: number
}

export interface Doc {
  access: Access
  name: string
  size: number
}

// What the server takes per upload (UPLOAD_MAX_FILE_MB / UPLOAD_MAX_REQUEST_MB).
export interface UploadLimits {
  file_mb: number
  request_mb: number
}
